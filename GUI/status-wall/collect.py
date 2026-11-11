#!/usr/bin/env python3
"""状态墙 · 采集器入口。

组装 metrics（采集）+ sinks（上报），本身不含业务逻辑。

Windows 开发机（快速验证，不出本机）：
    python CENTRAL/GUI/status-wall/collect.py --once
    python CENTRAL/GUI/status-wall/collect.py --sink mqtt --broker 127.0.0.1
    python CENTRAL/GUI/status-wall/collect.py --sink http --url http://127.0.0.1:8080/api/telemetry

Linux 中控机（24×7，systemd 托管）：
    ~/qyn/status-wall/.venv/bin/python collect.py --sink mqtt --broker 127.0.0.1
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台默认是 GBK，任何非 ASCII 输出都会抛 UnicodeEncodeError 直接崩。
# 统一改成 UTF-8：日志要能承载中文/特殊字符，多机器部署时这是必须的。
# （实测踩过：某台 Windows 主机名带 emoji，一打印就挂）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from qynwall import sinks as sinks_mod  # noqa: E402
from qynwall.metrics import DEFAULT_CPU_WINDOW, MetricsCollector  # noqa: E402

# 延迟的参照物：内网网关。所有机器都 ping 同一个目标，数字才可比。
PING_TARGET_DEFAULT = "192.0.2.10"

# 出口（公网）通断的参照物：先阿里 DNS，失败再试 114。
# 用户 2026-10-13：出口这块**只要通断，不要速率** —— 路由器是 上游路由器固件，
# 既没有 SNMP 也没有 SSH（官方规格 + UDP 161 实测），拿不到 WAN 字节计数。
WAN_TARGETS = ("223.5.5.5", "114.114.114.114")
WAN_PING_COUNT = 3


def measure_latency(target: str) -> float | None:
    """到网关的 ICMP 往返（毫秒）。读不到就返回 None —— 不用 0 冒充。

    为什么放在这里、不放进 metrics.py：metrics 有一条"不做任何 I/O、不联网"的约定
    （这样同一份采集逻辑能被仪表盘、测试、MQTT 上报器直接复用），而 ping 是真联网。
    采集入口本来就负责 I/O（sinks），放这儿不破坏那条约定。

    为什么用 ping 而不是 TCP 握手：实测连本机状态墙要 11ms（含应用层开销），
    而 ICMP 只有 0.5ms —— 延迟要测的是链路，不是某个端口。
    """
    try:
        out = subprocess.run(
            ["ping", "-c", "1", "-W", "1", "-n", target],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return None
    m = re.search(r"time=([0-9.]+)", out.stdout or "")
    if not m:
        return None
    try:
        return round(float(m.group(1)), 1)
    except ValueError:
        return None


def measure_wan(targets=WAN_TARGETS, count=WAN_PING_COUNT) -> dict | None:
    """到公网的**通断性**：丢包率 + 往返（毫秒）。

    返回 {"ok": bool, "loss_pct": float, "latency_ms": float|None, "target": str, "n": int}；
    **完全没有 ping 命令可用时返回 None**（"测不了"和"不通"是两件事，不能混）。

    为什么一个目标要打 3 个包：单包会抖，一次丢包就把整块标红太吵；
    3 包里丢多少就是丢多少。为什么有两个目标：有些网络会屏蔽某一个 DNS 的 ICMP。
    """
    ran = False
    for target in targets:
        try:
            out = subprocess.run(
                ["ping", "-c", str(count), "-W", "1", "-n", target],
                capture_output=True, text=True, timeout=count * 2 + 3,
            ).stdout or ""
        except Exception:
            continue
        ran = True
        loss = re.search(r"([0-9.]+)% packet loss", out)
        rtt = re.search(r"=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)", out)
        loss_pct = float(loss.group(1)) if loss else None
        latency = round(float(rtt.group(2)), 1) if rtt else None   # avg
        if loss_pct is not None and loss_pct < 100.0:
            return {"ok": True, "loss_pct": loss_pct, "latency_ms": latency,
                    "target": target, "n": count}
    if not ran:
        return None
    return {"ok": False, "loss_pct": 100.0, "latency_ms": None,
            "target": targets[0], "n": count}


def load_config(path: str | None) -> dict:
    """读 TOML 配置。Python 3.11+ 自带 tomllib，无第三方依赖。"""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"配置文件不存在: {p}")
    import tomllib
    with p.open("rb") as f:
        return tomllib.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="信息中心状态墙 · 采集器")
    ap.add_argument("--config", help="TOML 配置文件路径")
    ap.add_argument("--node", help="节点 ID（默认取主机名小写）")
    ap.add_argument("--sink", choices=sorted(sinks_mod.SINKS),
                    help="输出去向（默认 stdout，便于本地验证）")
    ap.add_argument("--broker", help="MQTT broker 地址（--sink mqtt 时必填）")
    ap.add_argument("--url", help="状态墙 ingest 地址（--sink http 时必填），"
                                  "如 http://127.0.0.1:8080/api/telemetry")
    ap.add_argument("--port", type=int, help="MQTT 端口（默认 1883）")
    ap.add_argument("--prefix", help="MQTT 主题前缀（默认 qyn）")
    ap.add_argument("--interval", type=float, help="采样间隔秒（默认 10）")
    ap.add_argument("--cpu-window", type=float,
                    help=f"首次 CPU 测量窗口秒（默认 {DEFAULT_CPU_WINDOW}）")
    ap.add_argument("--ping-target", help=f"延迟参照物（默认 {PING_TARGET_DEFAULT}）")
    ap.add_argument("--no-latency", action="store_true", help="不测延迟")
    # 公网探测只在中控机上开（它是 24×7 且常在内网的那台）：出口的"通断"由它代表。
    # 默认关，免得内网每台机器都去敲公网 DNS。
    ap.add_argument("--wan-probe", action="store_true",
                    help="额外探测公网通断（丢包率 + 往返），填 net.wan —— 只在中控机开")
    ap.add_argument("--once", action="store_true", help="只采一次就退出")
    args = ap.parse_args()

    cfg = load_config(args.config)

    def pick(key, arg, default):
        if arg is not None:
            return arg
        return cfg.get(key, default)

    node_id = args.node or cfg.get("node") or None
    interval = float(pick("interval", args.interval, 10.0))
    cpu_window = float(pick("cpu_window", args.cpu_window, DEFAULT_CPU_WINDOW))
    ping_target = pick("ping_target", args.ping_target, PING_TARGET_DEFAULT)
    wan_probe = bool(args.wan_probe or cfg.get("wan_probe", False))

    collector = MetricsCollector(node_id, cpu_window=cpu_window)

    sink_name = pick("sink", args.sink, "stdout")
    if sink_name == "mqtt":
        broker = pick("broker", args.broker, None)
        if not broker:
            raise SystemExit("--sink mqtt 需要 --broker 或配置里的 broker")
        sink = sinks_mod.MqttSink(
            broker,
            int(pick("port", args.port, 1883)),
            pick("prefix", args.prefix, "qyn"),
        )
    elif sink_name == "http":
        url = pick("url", args.url, None)
        if not url:
            raise SystemExit("--sink http 需要 --url 或配置里的 url")
        sink = sinks_mod.HttpSink(url)
    else:
        sink = sinks_mod.SINKS[sink_name]()
    sink.start(collector.node_id)
    print(f"[collect] 节点={collector.node_id} 去向={sink.name} "
          f"间隔={interval}s CPU窗口={cpu_window}s", file=sys.stderr)
    print(f"[collect] 首次采样需 {cpu_window}s 测量 CPU…", file=sys.stderr)

    try:
        while True:
            sample = collector.sample()
            if not args.no_latency:
                sample["net"]["latency_ms"] = measure_latency(ping_target)
            if wan_probe:
                sample["net"]["wan"] = measure_wan()
            sink.emit(sample)
            if args.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[collect] 收到中断，退出", file=sys.stderr)
    finally:
        sink.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
