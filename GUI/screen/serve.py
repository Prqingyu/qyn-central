#!/usr/bin/env python3
"""屏幕壳 · 静态服务（纯 stdlib，零第三方依赖）。

为什么需要一个服务、而不是让浏览器直接开 `file://`：
  面板以后要 `fetch` 各数据源的 HTTP 接口，而 `file://` 页面的源是 "null"，
  会被浏览器的 CORS 挡掉。**从一开始就用 http://127.0.0.1:8090 供页面**，
  以后接数据源不用改这一层。

它现在只做一件事：把 `web/` 目录静态供出去。聚合/代理留到接数据源时再说 ——
按架构约定，**屏幕只负责显示，数据源各自出接口**。

用法：
    python CENTRAL/GUI/screen/serve.py                # 127.0.0.1:8090
    python CENTRAL/GUI/screen/serve.py --port 8091
"""
from __future__ import annotations

import argparse
import json
import os
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# ★ 心跳文件（2026-10-26 排查"自动轮切失效"时加的）：
#   页面每 3 秒把「当前面板 / 是否被鼠标压住 / 键盘锁定」报上来 ⇒ 远程就能确认
#   **轮切到底有没有在动**，不用靠肉眼盯屏 ✗（之前只能猜 ✗）。
BEAT_FILE = os.path.expanduser("~/qyn/logs/screen-beat.json")


class Handler(SimpleHTTPRequestHandler):
    """静态处理器 + 禁用缓存。

    开发期会反复改页面：带上 Last-Modified 的话浏览器会拿旧文件，
    而"改了没生效"是最费时间的假故障。屏幕是常驻页面，本来也不该缓存。
    """

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):                          # noqa: N802（http.server 的命名约定）
        if self.path.startswith("/beat"):
            q = parse_qs(urlparse(self.path).query)
            rec = {
                "ts": time.time(),
                "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                "tab": (q.get("tab") or [""])[0],
                "hover": (q.get("hover") or [""])[0],
                "kbd": (q.get("kbd") or [""])[0],
                "client": self.client_address[0],
            }
            try:
                os.makedirs(os.path.dirname(BEAT_FILE), exist_ok=True)
                tmp = BEAT_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(rec, fh, ensure_ascii=False)
                os.replace(tmp, BEAT_FILE)     # 原子写：读的人永远看到完整 JSON ✓
            except OSError:
                pass
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, fmt, *args):        # 默认每请求一行，太吵
        if os.environ.get("QYN_SCREEN_ACCESS_LOG"):
            super().log_message(fmt, *args)


def main() -> int:
    ap = argparse.ArgumentParser(description="信息中心 · 屏幕壳静态服务")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认只绑本机）")
    ap.add_argument("--port", type=int, default=8090, help="端口（默认 8090）")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=WEB_DIR))
    httpd.daemon_threads = True
    print(f"[screen] 屏幕壳已启动 http://{args.host}:{args.port}/", flush=True)
    print(f"[screen] 页面目录: {WEB_DIR}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[screen] 停止", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
