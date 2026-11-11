"""展示层 —— HTTP 服务：静态页面 + 状态查询 + SSE 推送 + ingest。

**纯 stdlib**（http.server / json / queue），零第三方依赖、零 CDN。

为什么不用 FastAPI（STATUS_WALL.md §5.5 写的是"FastAPI 或纯 stdlib"）：
  中控机没有外网、没有 pip。S1 只需要"读状态 + 推事件"两件事，
  stdlib 完全够用；依赖越少，离线环境越可靠。将来真要换框架，换的也只是这一层。

路由：
  GET  /                 静态页面（web/index.html）
  GET  /api/state        全部节点的最后已知值（含 stale_seconds）
  GET  /api/stream       SSE：新样本即时下发 + 15s 心跳
  POST /api/telemetry    ingest：装不了 MQTT 库的节点走这条路（MC 服务器、STM32）

MQTT 是主通道（retained + LWT，离线检测白送）；HTTP ingest 是兜底。
两者最终都落到同一个 StateStore，所以展示层只认一种数据形态。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .state import StateStore

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
COLLECTOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "collector")
HEARTBEAT_SECONDS = 15
SUBSCRIBER_QUEUE_MAX = 32      # 有界：慢客户端不能把内存吃光
MAX_BODY_BYTES = 64 * 1024


class Broadcaster:
    """极简发布订阅。每个 SSE 连接一个**有界**队列，满了就丢并计数。

    为什么有界 + 计数：无界队列在慢客户端上会无限增长，最后把进程撑死；
    静默丢弃又会让"页面为什么不刷新"变成悬案。丢掉要能被看见。
    """

    def __init__(self) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self.published = 0
        self.dropped = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, sample: dict) -> None:
        with self._lock:
            subs = list(self._subs)
            self.published += 1
        for q in subs:
            try:
                q.put_nowait(sample)
            except queue.Full:
                with self._lock:
                    self.dropped += 1

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


class WallServer:
    """状态墙 HTTP 服务。`port=0` 让内核分配端口（测试用），`start()` 返回实际端口。"""

    def __init__(self, store: StateStore, host: str = "127.0.0.1", port: int = 8080,
                 web_dir: str | None = None):
        self.store = store
        # ★ 启动时清一次过期节点（用户 2026-10-17："清理干净测试节点" ——
        #   自测/演示留下的节点原先会永久赖在墙上 ✗，实测就有一个 `svc-egress-selftest` 留了 40 小时）。
        #   默认保留 30 天；真节点一直在推，`received_at` 一直被刷新，不受影响。
        try:
            dropped = self.store.prune()
            if dropped:
                print("[wall] 清理过期节点:", ", ".join(dropped), flush=True)
        except AttributeError:
            pass          # 测试用的替身存储可能没有 prune，不因此起不来
        self.broadcaster = Broadcaster()
        self.web_dir = web_dir or WEB_DIR
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 数据入口（HTTP ingest 与将来的 MQTT 桥共用）----

    def ingest(self, sample: dict) -> dict:
        result = self.store.upsert(sample)
        self.broadcaster.publish(self.store.get(result["node"]))
        return result

    # ---- 生命周期 ----

    def start(self) -> int:
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="wall-http", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _make_handler(wall: WallServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "qynwall"

        def log_message(self, fmt, *args):        # 默认往 stderr 打字，太吵
            if os.environ.get("QYNWALL_ACCESS_LOG"):
                super().log_message(fmt, *args)

        def end_headers(self):
            """统一放行跨源读取 —— 状态墙是**数据源**，屏幕壳在另一个端口。

            屏幕壳跑在 :8090、状态墙在 :8080，**不同端口就是不同源**，
            浏览器默认会挡掉跨源的 fetch / EventSource（实测症状是"页面一直空着、
            控制台一行 CORS"）。所以在这里统一加一行放行。
            服务只绑内网/本机，外部本来也进不来，风险可接受。
            """
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        _REASONS = {400: "Bad Request", 404: "Not Found", 411: "Length Required",
                    413: "Payload Too Large", 422: "Unprocessable Entity",
                    500: "Internal Server Error"}

        def _fail(self, code: int, message: str):
            """统一的错误响应。

            ⚠️ **绝不能把 message 塞进 `send_error` 的 reason**：HTTP 状态行按
            latin-1 编码，中文会抛 UnicodeEncodeError 并**直接掐断连接** ——
            客户端只看到 RemoteDisconnected，完全看不出真实原因（实测踩到）。
            所以 reason 固定用 ASCII，说明文字走 UTF-8 的 JSON body。
            """
            body = json.dumps({"ok": False, "error": message},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(code, self._REASONS.get(code, "Error"))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---- GET ----
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._serve_index()
            if path == "/api/state":
                return self._serve_json({
                    "nodes": wall.store.all(),
                    "subscribers": wall.broadcaster.subscriber_count,
                    "published": wall.broadcaster.published,
                    "dropped": wall.broadcaster.dropped,
                })
            if path == "/api/stream":
                return self._serve_stream()
            if path.startswith("/collector/"):
                # 只认 collector/ 目录里的文件名（basename 顺手挡掉目录穿越）
                return self._serve_collector(os.path.basename(path))
            return self._fail(404, "not found")

        # ---- POST ----
        def do_POST(self):
            if self.path.split("?", 1)[0] == "/api/telemetry":
                return self._ingest()
            return self._fail(404, "not found")

        # ---- 具体处理 ----
        def _serve_index(self):
            index = os.path.join(wall.web_dir, "index.html")
            if not os.path.isfile(index):
                return self._fail(500, "web/index.html 不存在")
            with open(index, "rb") as f:        # 每次读盘：开发期改页面不用重启
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_json(self, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_collector(self, name: str):
            """把采集器脚本发给别的机器（**引导通道**）。

            为什么由状态墙来发：目标机器（某台 Windows 机 这类）常常没装 Python、没有 git，
            但能 HTTP 到这台机。一条 `irm` 就拿到脚本 —— 不用配 SMB 凭据、不用拷 U 盘。
            （S4 的 exporter 做正经之后，这条通道收编进那里。）
            """
            p = os.path.join(COLLECTOR_DIR, name)
            if not os.path.isfile(p):
                return self._fail(404, "collector not found")
            with open(p, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_stream(self):
            q = wall.broadcaster.subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                # 反代/中间层会缓冲 SSE，必须显式禁用（参考项目在 Caddy 下踩过）
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.close_connection = True
                # 先推一次全量快照：页面不必等第一个事件就能画出来
                self._sse({"type": "snapshot", "nodes": wall.store.all()})
                last_beat = time.time()
                while True:
                    try:
                        sample = q.get(timeout=1.0)
                        self._sse({"type": "sample", "node": sample})
                    except queue.Empty:
                        pass
                    if time.time() - last_beat >= HEARTBEAT_SECONDS:
                        # 心跳注释行：让中间层和浏览器都知道连接还活着
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_beat = time.time()
            except (BrokenPipeError, ConnectionResetError):
                pass                                  # 页面关掉/切走，正常
            finally:
                wall.broadcaster.unsubscribe(q)

        def _sse(self, obj: dict):
            data = json.dumps(obj, ensure_ascii=False)
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        def _ingest(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._fail(400, "bad content-length")
            if length <= 0:
                return self._fail(411, "empty body")
            if length > MAX_BODY_BYTES:
                return self._fail(413, "body too large")
            raw = self.rfile.read(length)
            try:
                sample = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return self._fail(400, f"bad json: {exc}")
            if not isinstance(sample, dict):
                return self._fail(400, "sample must be a JSON object")
            try:
                wall.ingest(sample)
            except ValueError as exc:
                return self._fail(422, str(exc))
            body = b'{"ok":true}'
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
