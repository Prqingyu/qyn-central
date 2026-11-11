"""指标采集 —— 产出统一指标模型。

**本模块不做任何 I/O**：不联网、不写文件、不打印。
这样同一份采集逻辑既能喂 MQTT 上报器，也能被本地仪表盘、Agent、测试直接复用。

平台差异经 sysinfo 处理。

温度：`cpu.temp_c` 是 CPU 核心温度。Linux 上 psutil 直接读内核的 coretemp/k10temp
（零安装）；Windows 上 psutil 没有这个接口，由 PowerShell 采集器经 LibreHardwareMonitor
的库读（见 collector/win.ps1）。两条路都刻意**不取 ACPI 热区** —— 那是机箱温度。

网络延迟**不在这里**测：本模块不联网，延迟由采集入口 collect.py 补进 `net.latency_ms`。
"""

from __future__ import annotations

import hashlib
import socket
import time
from datetime import datetime

import psutil

from . import sysinfo

# 首次采集的 CPU 测量窗口（秒）。
#
# 为什么不是 0.5 秒：cpu_percent 测的是"窗口内的平均值"，窗口太短会被瞬时
# 抖动主导（一次 GC、一次编译就能把 0.5 秒拉到 30%），数字没有参考价值。
# 用 10 秒窗口拿到的是稳定值，代价只是启动时多等 10 秒——对 24×7 服务无所谓。
DEFAULT_CPU_WINDOW = 10.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_node_id(raw: str) -> str:
    """把主机名收敛成安全的节点 ID。

    节点 ID 会进入 MQTT 主题（qyn/node/<id>/telemetry），必须**跨机器可预测**。
    实测踩过：有台 Windows 主机名里带 emoji（某台机器），直接拿来当 ID 会污染主题，
    而且打到 GBK 控制台直接崩。

    规则：转小写 → 只保留 [a-z0-9-_] → 其余替换为 '-' → 折叠连续 '-'。
    全被清空时（主机名全是非 ASCII）退回基于原文的短哈希，保证仍然唯一。
    """
    out = []
    for ch in raw.strip().lower():
        out.append(ch if (ch.isascii() and (ch.isalnum() or ch in "-_")) else "-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    if slug:
        return slug
    return "node-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]


def _default_node_id() -> str:
    return sanitize_node_id(socket.gethostname())


# 虚拟 / 回环网卡的名字前缀（小写比较）：这些不是"这台机器跟网络之间的那根线"，
# 它们的内网流量会重复计入，还会把回环流量算成"网络流量"。
_VIRTUAL_PREFIX = ("lo", "docker", "veth", "br-", "virbr", "vmnet", "vboxnet",
                   "tun", "tap", "wg", "zt", "tailscale", "snap", "hyper-v", "npcap")


def _iface_is_real(name: str, st) -> bool:
    """这张网卡值不值得上报（见 `_net_rates` 的说明）。"""
    if name.lower().startswith(_VIRTUAL_PREFIX):   # lo / loopback 也在里面
        return False
    if st is None or not st.isup:                  # 没有这张卡的信息，或它没起来
        return False
    return True


class MetricsCollector:
    """采集本机指标。

    持有跨次调用的状态：网络计数器（算速率）、CPU 是否已过首次采样。
    """

    def __init__(self, node_id: str | None = None,
                 cpu_window: float = DEFAULT_CPU_WINDOW):
        self.node_id = node_id or _default_node_id()
        self.cpu_window = float(cpu_window)
        self._last_net = None
        self._last_net_ts = None
        self._first_cpu = True

    def _cpu_pct(self) -> float:
        """CPU 使用率。

        psutil.cpu_percent(interval=None) 返回"距上次调用"的区间值：
          · 首次调用没有参照点，必然返回 0.0
          · 预热后立刻再调，区间≈0，也接近 0.0

        所以首次改用阻塞式测量，窗口 = cpu_window。之后是非阻塞的，
        测量窗口由采样循环的间隔决定（所以循环间隔应设成和窗口同一量级）。
        """
        if self._first_cpu:
            self._first_cpu = False
            return psutil.cpu_percent(interval=self.cpu_window)
        return psutil.cpu_percent(interval=None)

    def _net_rates(self) -> dict:
        """逐网卡的上下行速率 + 链路速率。

        ★ 单位是**千比特每秒（kbps）**，不是 KB/s。旧实现除以 1024 却叫 kbps —— 差 8 倍，
          而且没法跟"网卡上限"比。现在统一按比特算，这样条形图的分母（链路速率，
          如 1000 Mbps）和读数在同一个量纲上。

        ★ 排除：回环（lo）、没有载波的口、以及虚拟网卡（docker/veth/virbr/vmnet…）。
          只看"真的有线/无线连着"的那几张 —— 否则虚拟网卡的字节数会把合计弄虚。
          （实测：中控机的 lo 一天能跑几十 MB，混进"网络流量"里毫无意义。）

        `rx_kbps` / `tx_kbps` 是**合计**（只算留下来的那些网卡）；`ifaces` 是逐张明细。
        """
        cur = psutil.net_io_counters(pernic=True)
        try:
            stats = psutil.net_if_stats()
        except Exception:
            stats = {}
        now = time.monotonic()
        first = self._last_net is None
        elapsed = 0.0 if first else max(now - self._last_net_ts, 1e-6)

        ifaces: list[dict] = []
        tot_rx = tot_tx = 0.0
        for name, c in cur.items():
            st = stats.get(name)
            if not _iface_is_real(name, st):
                continue
            rx = tx = None
            if not first:
                prev = self._last_net.get(name)
                if prev is not None:
                    # 计数器可能回绕或重置 ⇒ 负数没有意义，夹到 0
                    rx = max((c.bytes_recv - prev.bytes_recv) * 8 / elapsed / 1000, 0.0)
                    tx = max((c.bytes_sent - prev.bytes_sent) * 8 / elapsed / 1000, 0.0)
                    tot_rx += rx
                    tot_tx += tx
                    rx, tx = round(rx, 1), round(tx, 1)
            ifaces.append({
                "name": name,
                "up": bool(st.isup) if st else None,
                # 链路速率（Mbps）—— 条形图的满量程；拿不到就是 null（如部分 WiFi）
                "speed_mbps": int(st.speed) if (st and st.speed and st.speed > 0) else None,
                "rx_kbps": rx,
                "tx_kbps": tx,
            })

        ifaces.sort(key=lambda i: i["name"])
        self._last_net, self._last_net_ts = cur, now
        if first:
            return {"rx_kbps": None, "tx_kbps": None, "ifaces": ifaces}
        return {"rx_kbps": round(tot_rx, 1), "tx_kbps": round(tot_tx, 1), "ifaces": ifaces}

    def _cpu_temp(self) -> dict | None:
        """CPU 温度 —— Linux 上**什么都不用装**。

        实测（中控机 通用 ITX 主板）：内核的 coretemp hwmon 已经在了，
        psutil.sensors_temperatures() 直接给出 "Package id 0" + 每个核的温度。
        Windows 上 psutil **没有**这个接口，那边由 PowerShell 采集器经
        LibreHardwareMonitor 的库读（见 collector/win.ps1）。

        ★ 刻意**不取 acpitz**（ACPI 热区）：那是机箱/主板温度、不是 CPU 核心温度 ——
          用户 2026-10-13 明确说"热区没有意义"。

        读不到就返回 None：不用 0 冒充。
        """
        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            return None
        if not temps:
            return None
        # Intel: coretemp / AMD: k10temp（zenpower 是第三方替代）/ ARM: cpu_thermal
        for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
            vals = {
                (e.label or key): round(float(e.current), 1)
                for e in (temps.get(key) or [])
                if e.current is not None
            }
            if vals:
                return {"max": max(vals.values()), "sensors": vals}
        return None

    def sample(self) -> dict:
        """采集一次，返回统一指标模型（见 STATUS_WALL.md §5.2）。"""
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(sysinfo.root_disk())

        # 核心指标：跨平台一定有（拿不到的语义用 null 表示，不用 0 冒充）
        cpu = {
            "pct": round(self._cpu_pct(), 1),
            "load1": sysinfo.cpu_load1(),   # Windows 恒为 null
            "cores": psutil.cpu_count(logical=True),
        }
        # 开放通道：本节点特有的信息，schema 不预先完备。
        #   例：mc 放玩家数/世界大小；bmc 放电源态；central 放 WAN 认证状态
        extra: dict = {}
        temp = self._cpu_temp()
        if temp:
            cpu["temp_c"] = temp["max"]                  # 头条值 = 最高的那个传感器
            extra["temp_sensors"] = temp["sensors"]      # 逐传感器明细

        return {
            "node": self.node_id,
            "ts": _now_iso(),
            "online": True,
            "platform": sysinfo.PLATFORM_LABEL,
            "cpu": cpu,
            "mem": {
                "used_pct": round(mem.percent, 1),
                "used_mb": int(mem.used / 1024 / 1024),
                "total_mb": int(mem.total / 1024 / 1024),
            },
            "disk": {
                "used_pct": round(disk.percent, 1),
                "free_gb": round(disk.free / 1024 / 1024 / 1024, 1),
                "total_gb": round(disk.total / 1024 / 1024 / 1024, 1),
            },
            "net": self._net_rates(),
            "extra": extra,
        }
