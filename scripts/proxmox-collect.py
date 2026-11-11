#!/usr/bin/env python3
"""SERVER（Proxmox VE 宿主机）采集器 —— 只采**三个状态**：CPU / TEMP / RAM。

背景（2026-11-08，用户定）：
    SERVER（192.0.2.10，墙上节点 id = `mc`）装了 Proxmox VE，而且**完全虚拟化**
    —— 宿主机本身不跑业务，所有东西都在 VM/容器里。
    ⇒ 宿主机只需要回答"这台机器还活着吗、累不累"这一个问题 ✓
    ⇒ **只要三个状态**：CPU% / CPU 温度 / 内存占用 ✓（和墙上其它机器完全一样 ✓）
    ⇒ 不采 VM 列表、不采存储池（那些明细对"宿主机健康"没有增量信息 ✗）

实现要点：
    · **不依赖 psutil / 不调用 pvesh** ✓ —— 纯读 `/proc`（快、稳、零依赖 ✓）
    · 契约和墙上其它机器**完全一致** ✓（node/cpu/mem/disk/net/extra ✓）
    · 时间戳用本地时间 + 时区 ✓

用法：
    python3 proxmox-collect.py --once --print --no-push   # 干跑
    python3 proxmox-collect.py --once                     # 推一帧
    python3 proxmox-collect.py --interval 15              # 每 15 秒
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_NODE = "mc"                      # 墙上 SERVER 的节点 id ✓
DEFAULT_URL = "http://127.0.0.1:8080/api/telemetry"
PING_TARGET = "192.0.2.10"              # 和别的机器同一个参照物 ⇒ 数字可比 ✓


# ─────────────────────────────────────────────────────────── 三个状态
def read_cpu_times() -> tuple[int, int]:
    """(busy, total) 两个累计 jiffies —— /proc/stat 第一行。"""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        parts = f.readline().split()[1:]
    v = [int(x) for x in parts]
    idle = v[3] + (v[4] if len(v) > 4 else 0)          # idle + iowait
    total = sum(v)
    return total - idle, total


def cpu_pct(window: float = 0.6) -> float:
    """CPU 使用率：取两次 /proc/stat 的差值（0.6 秒窗口 ✓ 比 1 秒快又不抖）。"""
    b1, t1 = read_cpu_times()
    time.sleep(window)
    b2, t2 = read_cpu_times()
    dt = t2 - t1
    if dt <= 0:
        return 0.0
    return round((b2 - b1) / dt * 100, 1)


def mem_info() -> dict:
    """内存：MemTotal / MemAvailable（不是 MemFree ✗ —— 那会把 cache 算成占用）。"""
    vals: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(\w+):\s+(\d+)", line)
            if m:
                vals[m.group(1)] = int(m.group(2))
    total_kb = vals.get("MemTotal", 0)
    avail_kb = vals.get("MemAvailable") or vals.get("MemFree", 0)
    used_kb = max(0, total_kb - avail_kb)
    return {
        "used_pct": round(used_kb / total_kb * 100, 1) if total_kb else None,
        "used_mb": int(used_kb / 1024),
        "total_mb": int(total_kb / 1024),
    }


def cpu_temp() -> dict | None:
    """CPU 温度：/sys/class/thermal 取最高 ✓（读不到就是 None —— 不拿 0 冒充 ✓）。"""
    best = None
    for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            v = int(p.read_text().strip()) / 1000.0
        except Exception:
            continue
        if 0 < v < 130 and (best is None or v > best):
            best = v
    if best is None:
        return None
    return {"max": round(best, 1), "sensors": {"thermal_zone": round(best, 1)}}


def root_disk() -> dict:
    """根分区占用（契约要求 ✓ 用 statvfs 不调用 df ✓）。"""
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = max(0, total - free)
    return {
        "used_pct": round(used / total * 100, 1) if total else None,
        "free_gb": round(free / 1024 ** 3, 1),
        "total_gb": round(total / 1024 ** 3, 1),
    }


def net_latency(target: str = PING_TARGET) -> float | None:
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "1", "-n", target],
                             capture_output=True, text=True, timeout=4)
    except Exception:
        return None
    m = re.search(r"time=([0-9.]+)", out.stdout or "")
    return round(float(m.group(1)), 1) if m else None


def platform_label() -> str:
    """是不是 PVE：先读 /etc/pve/.version（**只认纯版本号**），再退回 `pveversion`，最后 os-release。

    ★ 2026-11-10 踩过：这台 PVE 的 /etc/pve/.version 不是 "8.2.4" 而是一大坨 JSON，
      旧代码原样拼进 `platform` ⇒ 状态墙的 mc 卡片上是一段乱码。
    """
    try:
        v = Path("/etc/pve/.version").read_text(encoding="utf-8", errors="replace").strip()
        if re.fullmatch(r"\d+(\.\d+)+", v):
            return f"Proxmox VE {v}"
    except Exception:
        pass
    try:
        out = subprocess.run(["pveversion"], capture_output=True, text=True, timeout=3)
        m = re.search(r"pve-manager/(\d+\.\d+\.\d+)", out.stdout or "")
        if m:
            return f"Proxmox VE {m.group(1)}"
    except Exception:
        pass
    try:
        osr = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)', osr, re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return os.uname().sysname


def uptime_s() -> int | None:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────── 组装
def build_sample(node_id: str) -> dict:
    cpu = {"pct": cpu_pct(), "load1": None, "cores": os.cpu_count()}
    try:
        load = os.getloadavg()[0]
        cpu["load1"] = round(load, 2)
    except Exception:
        pass
    extra: dict = {"uptime_s": uptime_s()}
    temp = cpu_temp()
    if temp:
        cpu["temp_c"] = temp["max"]
        extra["temp_sensors"] = temp["sensors"]
    return {
        "node": node_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "online": True,
        "platform": platform_label(),
        "cpu": cpu,
        "mem": mem_info(),
        "disk": root_disk(),
        "net": {"latency_ms": net_latency()},
        "extra": extra,
    }


def fake_sample(node_id: str) -> dict:
    """开发机自测用（没有 /proc/stat 的 Windows 上也能验证 JSON 形状 ✓）。"""
    return {
        "node": node_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "online": True,
        "platform": "Proxmox VE 8.2.4 (FAKE)",
        "cpu": {"pct": 18.6, "load1": 1.42, "cores": 16, "temp_c": 47.0},
        "mem": {"used_pct": 34.2, "used_mb": 11206, "total_mb": 32768},
        "disk": {"used_pct": 41.0, "free_gb": 295.0, "total_gb": 500.0},
        "net": {"latency_ms": 0.6},
        "extra": {"uptime_s": 864000, "temp_sensors": {"thermal_zone": 47.0}},
    }


# ─────────────────────────────────────────────────────────── 推送
def push(url: str, sample: dict, timeout: float = 8.0) -> int:
    body = json.dumps(sample, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def main() -> int:
    ap = argparse.ArgumentParser(description="SERVER（PVE 宿主机）→ 状态墙：只采 CPU/TEMP/RAM")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--node", default=DEFAULT_NODE, help=f"墙上节点 id（默认 {DEFAULT_NODE}）")
    ap.add_argument("--interval", type=float, default=0.0, help="循环间隔秒（0 = 只采一次）")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--fake", action="store_true", help="假数据（开发机验证 JSON 形状）")
    ap.add_argument("--verbose", action="store_true",
                    help="每帧都打一行日志（默认**只报错** ✓ —— 5 秒一帧全打会把 journal 塞满 ✗）")
    args = ap.parse_args()

    _said_first = False
    while True:
        try:
            sample = fake_sample(args.node) if args.fake else build_sample(args.node)
        except Exception as e:                      # noqa: BLE001
            print(f"[pve-collect] 采集失败: {type(e).__name__}: {e}", file=sys.stderr)
            if not args.interval:
                return 1
            time.sleep(max(1.0, args.interval))
            continue

        if args.do_print:
            print(json.dumps(sample, ensure_ascii=False, indent=2))

        if not args.no_push:
            try:
                code = push(args.url, sample)
                # ★ 默认**只在第一帧**和出错时说话 ✓（用户 2026-11-10：每 5 秒一行把 journal 塞满 ✗）
                if args.verbose or not _said_first:
                    c, m = sample["cpu"], sample["mem"]
                    print(f"[pve-collect] 已推送 {args.node} → {args.url} ({code}) "
                          f"CPU {c['pct']}% TEMP {c.get('temp_c', '—')} RAM {m['used_pct']}%"
                          + ("" if args.verbose else "  （之后只在出错时再报 ✓）"),
                          file=sys.stderr)
                    _said_first = True
            except Exception as e:                  # noqa: BLE001
                print(f"[pve-collect] 推送失败: {type(e).__name__}: {e}", file=sys.stderr)
                if not args.interval:
                    return 1

        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
