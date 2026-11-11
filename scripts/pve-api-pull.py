#!/usr/bin/env python3
"""从 Proxmox API **拉** SERVER 的宿主机状态 → 状态墙（替代装在服务器上的探针 ✓）。

为什么换（用户 2026-11-10）：
    探针每 5 秒写一行 journal ⇒ 一天 ~1.7 万行 ✗ 把日志塞满了。
    改成"墙主动去拉 PVE API" ⇒ **服务器上什么都不用装** ✓ 零日志 ✓。

前置（只做一次）：
    在 PVE 上建一个只读 API Token，把 secret 写到**中控**的这个文件（权限 600）：
        ~/.config/qyn/pve-token        # 见 --init 生成的模板
    格式（shell 风格 key=value）：
        PVE_HOST=192.0.2.10
        PVE_NODE=pve-node-1
        PVE_TOKEN=qyn@pve!wall=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

用法：
    # ① 先测试能不能拉到（不推墙 ✓ 这是"先测试"那一步）
    python3 pve-api-pull.py --test

    # ② 拉一帧并推墙
    python3 pve-api-pull.py --once

    # ③ 常驻（每 5 秒一帧；给 systemd 用）
    python3 pve-api-pull.py --interval 5

    # 生成 token 文件模板
    python3 pve-api-pull.py --init

契约与其它采集器**完全一致** ✓（node/cpu/mem/disk/net/extra ✓）。
TEMP：PVE 的 REST API **不暴露 CPU 温度** ✗ ⇒ 该字段为 null（墙上显示 `—` ✓ 不是故障 ✓）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONF_DEFAULT = Path.home() / ".config" / "qyn" / "pve-token"
DEFAULT_URL = "http://127.0.0.1:8080/api/telemetry"   # 墙的 ingest（中控本机也可 127.0.0.1）
DEFAULT_NODE_ID = "mc"                                  # 墙上 SERVER 的节点 id ✓


# ─────────────────────────────────────────────────────────── 配置
def load_conf(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"找不到 token 文件: {path}\n"
            f"先建：mkdir -p {path.parent} && python3 {sys.argv[0]} --init > {path} && chmod 600 {path}"
        )
    conf = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        conf[k.strip()] = v.strip()
    for k in ("PVE_HOST", "PVE_TOKEN"):
        if not conf.get(k):
            raise SystemExit(f"{path} 里缺少 {k}")
    return conf


# ─────────────────────────────────────────────────────────── PVE API
class Pve:
    def __init__(self, host: str, token: str, node: str, timeout: float = 10.0):
        self.base = f"https://{host}:8006"
        self.token = token
        self.node = node
        self.timeout = timeout
        # PVE 是自签证书 ⇒ 必须关校验（只读内网接口 ✓）
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def get(self, path: str):
        req = urllib.request.Request(self.base + path, headers={
            "Authorization": f"PVEAPIToken={self.token}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                return json.loads(r.read().decode("utf-8")).get("data")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SystemExit("PVE API 返回 401 —— token 不对 / 没勾权限 / token 已过期 ✗")
            raise RuntimeError(f"PVE API {path} → HTTP {e.code}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"连不上 PVE API {self.base}: {e.reason}")

    def status(self) -> dict:
        return self.get(f"/api2/json/nodes/{self.node}/status") or {}


# ─────────────────────────────────────────────────────────── 组装 sample
def ping_ms(host: str) -> float | None:
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "1", "-n", host],
                             capture_output=True, text=True, timeout=4)
    except Exception:
        return None
    m = re.search(r"time=([0-9.]+)", out.stdout or "")
    return round(float(m.group(1)), 1) if m else None


def build_sample(pve: Pve, node_id: str, ping_host: str) -> dict:
    st = pve.status()
    mem = st.get("memory") or {}
    root = st.get("rootfs") or {}
    load = st.get("loadavg") or []
    mem_total = mem.get("total") or 1
    root_total = root.get("total") or 1

    def gb(n):
        return round(n / 1024 ** 3, 1)

    return {
        "node": node_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "online": True,
        "platform": "Proxmox VE " + (st.get("pveversion") or "").split("/")[1][:12]
                    if (st.get("pveversion") or "").count("/") >= 1 else "Proxmox VE",
        "cpu": {
            "pct": round((st.get("cpu") or 0.0) * 100, 1),
            "load1": round(float(load[0]), 2) if load else None,
            "cores": (st.get("cpuinfo") or {}).get("cpus"),
            "temp_c": None,          # PVE API 不暴露温度 ✗（墙上显示 — ✓ 不是故障）
        },
        "mem": {
            "used_pct": round(mem.get("used", 0) / mem_total * 100, 1),
            "used_mb": int(mem.get("used", 0) / 1024 / 1024),
            "total_mb": int(mem_total / 1024 / 1024),
        },
        "disk": {
            "used_pct": round(root.get("used", 0) / root_total * 100, 1),
            "free_gb": gb(max(0, root_total - root.get("used", 0))),
            "total_gb": gb(root_total),
        },
        "net": {"latency_ms": ping_ms(ping_host)},
        "extra": {
            "uptime_s": st.get("uptime"),
            "kernel": st.get("kversion"),
            "source": "pve-api",          # 标记：这一帧是**拉**来的 ✓（不是服务器上的探针）
        },
    }


def push(url: str, sample: dict, timeout: float = 8.0) -> int:
    body = json.dumps(sample, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


# ─────────────────────────────────────────────────────────── 入口
TEMPLATE = """# QYN 状态墙 · PVE API token（chmod 600 ✓ 别提交到 git ✗）
# 在 PVE 上建：Datacenter → Permissions → API Tokens → Add
#    User: qyn@pve（或 root@pam）   Token ID: wall   Privilege Separation: 关 ✗
#    角色给只读（PVEAuditor ✓）
PVE_HOST=192.0.2.10
PVE_NODE=pve-node-1
PVE_TOKEN=qyn@pve!wall=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="从 PVE API 拉宿主机状态 → 状态墙")
    ap.add_argument("--conf", default=str(CONF_DEFAULT), help=f"token 文件（默认 {CONF_DEFAULT}）")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"墙 ingest（默认 {DEFAULT_URL}）")
    ap.add_argument("--node", default=DEFAULT_NODE_ID, help=f"墙上节点 id（默认 {DEFAULT_NODE_ID}）")
    ap.add_argument("--interval", type=float, default=0.0, help="循环间隔秒（0 = 只拉一次）")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true", help="只拉并打印（不推墙 ✓ 先测通不通用这个）")
    ap.add_argument("--verbose", action="store_true", help="每帧都打日志（默认只在第一帧/出错时 ✓）")
    ap.add_argument("--init", action="store_true", help="打印 token 文件模板")
    ap.add_argument("--set-token", help="写入 token 文件（会把 --host/--pve-node 一起写进去 ✓）")
    ap.add_argument("--host", help="PVE 地址（配合 --set-token，默认 192.0.2.10）")
    ap.add_argument("--pve-node", help="PVE 节点名（配合 --set-token，默认 pve-node-1）")
    ap.add_argument("--ping", default=None, help="延迟参照物（默认 = PVE_HOST ✓ 即宿主本身）")
    args = ap.parse_args()

    if args.init:
        print(TEMPLATE, end="")
        return 0

    if args.set_token:
        p = Path(args.conf)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = (f"# QYN 状态墙 · PVE API token（chmod 600 ✓）\n"
                f"PVE_HOST={args.host or '192.0.2.10'}\n"
                f"PVE_NODE={args.pve_node or 'pve-node-1'}\n"
                f"PVE_TOKEN={args.set_token}\n")
        p.write_text(body, encoding="utf-8")
        os.chmod(p, 0o600)
        print(f"✓ 已写入 {p}（权限 600）")
        print("  下一步测通不通：")
        print(f"  python3 {sys.argv[0]} --test")
        return 0

    conf = load_conf(Path(args.conf))
    pve = Pve(conf["PVE_HOST"], conf["PVE_TOKEN"], conf.get("PVE_NODE") or conf["PVE_HOST"])
    ping_host = args.ping or conf["PVE_HOST"]

    said_first = False
    while True:
        try:
            sample = build_sample(pve, args.node, ping_host)
        except SystemExit:
            raise
        except Exception as e:                      # noqa: BLE001
            print(f"[pve-pull] 拉取失败: {type(e).__name__}: {e}", file=sys.stderr)
            if not args.interval:
                return 1
            time.sleep(max(1.0, args.interval))
            continue

        if args.test:
            print(json.dumps(sample, ensure_ascii=False, indent=2))
            print("\n✓ 拉取成功 —— 数据形状和墙上契约一致 ✓（上面这个去掉 --test 就会推给墙 ✓）",
                  file=sys.stderr)
            return 0

        try:
            code = push(args.url, sample)
            if args.verbose or not said_first:
                c, m = sample["cpu"], sample["mem"]
                print(f"[pve-pull] 已推送 {args.node} → {args.url} ({code}) "
                      f"CPU {c['pct']}% RAM {m['used_pct']}% TEMP —（API 没有温度 ✓）", file=sys.stderr)
                said_first = True
        except Exception as e:                      # noqa: BLE001
            print(f"[pve-pull] 推送失败: {type(e).__name__}: {e}", file=sys.stderr)
            if not args.interval:
                return 1

        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
