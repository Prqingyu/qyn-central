#!/usr/bin/env python3
"""状态墙 · 服务入口。

组装「状态层 + 展示层」，本身不含业务逻辑 —— 和 collect.py 一个风格。

用法：
    # 本地开发（内存库，不落盘）
    python CENTRAL/GUI/status-wall/serve.py --port 8080 --db :memory:
    # 中控机（落盘，systemd 托管）
    ~/qyn/status-wall/.venv/bin/python serve.py --host 127.0.0.1 --port 8080 \
        --db ~/qyn/data/wall.db
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台默认 GBK，打非 ASCII 会直接崩（项目踩过）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from qynwall.server import WallServer   # noqa: E402
from qynwall.state import StateStore    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="信息中心状态墙 · 服务")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认只绑本机；中控机也是本机页面）")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "wall.db"),
                    help="状态库路径；:memory: 表示不落盘")
    args = ap.parse_args()

    store = StateStore(args.db)
    wall = WallServer(store, host=args.host, port=args.port)
    port = wall.start()
    print(f"[serve] 状态墙已启动 {wall.url}")
    print(f"[serve] 状态库: {args.db}")
    print("[serve] 采集器推送: "
          f"python CENTRAL/GUI/status-wall/collect.py --sink http --url {wall.url}/api/telemetry")
    print("[serve] Ctrl-C 停止")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[serve] 停止中…")
    finally:
        wall.stop()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
