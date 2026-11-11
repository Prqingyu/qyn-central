#!/usr/bin/env python3
"""LAN 中继 —— 一台服务器，两种用途，都只服务白名单内的中控机。

用途 A（apt 代理）：中控机无外网，借本机 Windows 的 WiFi 出口装软件包。
用途 B（文件投递）：把公钥安装脚本等小文件递给中控机。

同一个 8080 端口，按请求形态自动分流：
  · 绝对 URL（GET http://archive.ubuntu.com/...）→ 代理转发（域名白名单）
  · 绝对 URL 但指向本机自己（http://192.0.2.10:8080/s）→ 当作本机文件
  · 普通路径（GET /s）→ 当作本机文件
这样中控机无论有没有设 http_proxy，两种用法都能正常工作。

⚠️ 安全约束：
  1. 只绑定局域网网卡（不监听校园网 WiFi 侧，不监听 0.0.0.0）；
  2. 只接受白名单客户端（中控机 + 本机自检）；
  3. 代理转发只允许白名单域名（默认仅 Ubuntu/Debian 软件源）；
  4. 文件投递只暴露 tools/relay/ 目录下的文件，且做了路径穿越防护。
用完即停。

放宽白名单要**显式开启**（默认姿态不变）：
  python tools/lan_http_proxy.py                      # 默认，仅软件源
  python tools/lan_http_proxy.py --install-mode        # + nodejs/npm/pypi（装 Node、DSH CLI、pip 包）
  python tools/lan_http_proxy.py --allow-host api.deepseek.com   # 按需放行，可重复

⚠️ --install-mode 是**临时**手段：装完就关，不要长期挂着。

运行：python tools/lan_http_proxy.py
"""

import argparse
import os
import select
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- 收紧策略 ----
GATEWAY = "192.0.2.10"                        # 局域网网关，用于探测本机局域网 IP


def detect_lan_ip() -> str:
    """探测本机在局域网网卡上的 IP。

    向网关发一个 UDP 包（不会真的发出），内核按路由表选源地址 —— 这样拿到的是
    接局域网那块网卡的地址，而不是校园网 WiFi 的地址。之前把地址写死成
    192.0.2.10，结果本机 DHCP 换到 .6 后就绑不上端口了。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((GATEWAY, 80))
        return s.getsockname()[0]
    except OSError:
        return GATEWAY
    finally:
        s.close()


LISTEN_HOST = detect_lan_ip()                  # 仅局域网网卡（自动探测）
LISTEN_PORT = 8080
ALLOWED_CLIENTS = {"127.0.0.1",              # 中控机
                   LISTEN_HOST, "127.0.0.1"}   # 本机（自检用，无外部暴露）

# 默认只放行软件源。放宽必须走 --install-mode / --allow-host（默认姿态不变）。
BASE_ALLOWED_HOSTS = ("ubuntu.com", "debian.org")

# --install-mode 的放行集：装 Node、DSH CLI、Python 包所需
INSTALL_MODE_HOSTS = (
    "nodejs.org",              # Node 官方二进制/源
    "registry.npmjs.org",      # npm 包（含 @deepseek-ai/dsh）
    "pypi.org",                # Python 包索引
    "files.pythonhosted.org",  # Python 包实体
)

ALLOWED_HOSTS = list(BASE_ALLOWED_HOSTS)       # 运行期生效，由 main() 按参数扩展
SELF_HOSTS = {LISTEN_HOST, "127.0.0.1", "localhost"}
RELAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay")
UPSTREAM_TIMEOUT = 30

FORWARD_HEADERS = ("Range", "If-Modified-Since", "If-None-Match", "User-Agent", "Accept")


def host_allowed(host: str, allowed=None) -> bool:
    """上游域名是否放行。`allowed` 缺省取运行期白名单。

    注意匹配语义：`h == host` 或 `host.endswith("." + h)` —— 后者是子域名放行，
    所以 "notubuntu.com" 和 "ubuntu.com.evil.com" 都**不**通过（已由测试钉住）。
    """
    host = (host or "").lower().split("@")[-1].split(":")[0]
    hosts = ALLOWED_HOSTS if allowed is None else allowed
    return any(host == h or host.endswith("." + h) for h in hosts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---- 门禁 ----
    def _deny(self, code: int, msg: str) -> None:
        self.send_error(code, msg)
        sys.stderr.write(f"[relay] DENY {self.client_address[0]} {self.command} {self.path} — {msg}\n")

    def _client_ok(self) -> bool:
        if self.client_address[0] not in ALLOWED_CLIENTS:
            self._deny(403, "client not allowed")
            return False
        return True

    # ---- 分流 ----
    def _dispatch(self, include_body: bool) -> None:
        if not self._client_ok():
            return
        raw = self.path
        if raw.lower().startswith(("http://", "https://")):
            parts = urllib.parse.urlsplit(raw)
            host = (parts.hostname or "").lower()
            port = parts.port
            if host in SELF_HOSTS and port in (None, LISTEN_PORT):
                self._serve_local(parts.path, include_body)
                return
            if parts.scheme != "http":
                self._deny(403, "only plain-http proxying is enabled")
                return
            if not host_allowed(parts.netloc):
                self._deny(403, "upstream host not allowed")
                return
            self._forward_remote(raw, include_body)
        else:
            self._serve_local(raw, include_body)

    # ---- 用途 B：投递本机文件 ----
    def _serve_local(self, path: str, include_body: bool) -> None:
        rel = urllib.parse.urlsplit(path).path.lstrip("/")
        target = os.path.normpath(os.path.join(RELAY_DIR, rel))
        if not target.startswith(RELAY_DIR + os.sep) and target != RELAY_DIR:
            self._deny(403, "path outside relay dir")
            return
        if not os.path.isfile(target):
            self.send_error(404, "File not found")
            return
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    # ---- 用途 A：代理转发到软件源 ----
    def _forward_remote(self, url: str, include_body: bool) -> None:
        req = urllib.request.Request(url, method=self.command)
        for h in FORWARD_HEADERS:
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            resp = urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e  # 保留真实状态码（如 404），让 apt 自行判断
        except Exception as e:
            self.send_error(502, f"upstream error: {e}")
            return
        try:
            body = resp.read() if include_body else b""
        except Exception as e:
            self.send_error(502, f"read error: {e}")
            return
        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length",
                         str(len(body)) if include_body
                         else resp.headers.get("Content-Length", "0"))
        self.end_headers()
        if include_body and body:
            self.wfile.write(body)

    def do_GET(self):
        self._dispatch(True)

    def do_HEAD(self):
        self._dispatch(False)

    # ---- HTTPS CONNECT 隧道 ----
    def do_CONNECT(self):
        if not self._client_ok():
            return
        host, _, port = self.path.partition(":")
        if not host_allowed(host):
            self._deny(403, "upstream host not allowed")
            return
        try:
            upstream = socket.create_connection((host, int(port or 443)), UPSTREAM_TIMEOUT)
        except Exception as e:
            self.send_error(502, f"connect failed: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._relay(self.connection, upstream)

    @staticmethod
    def _relay(a: socket.socket, b: socket.socket) -> None:
        try:
            while True:
                r, _, x = select.select([a, b], [], [a, b], 60)
                if x or not r:
                    return
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        finally:
            try:
                b.close()
            except OSError:
                pass

    def log_message(self, fmt, *args):
        sys.stderr.write("[relay] " + (fmt % args) + "\n")


def resolve_allowed_hosts(allow_host=(), install_mode: bool = False) -> list[str]:
    """按参数算出运行期白名单。

    抽成纯函数是为了可测：**默认姿态必须仍是"仅软件源"**，
    放宽只能由显式参数触发（见 test_lan_http_proxy.py）。
    """
    hosts = list(BASE_ALLOWED_HOSTS)
    if install_mode:
        hosts += [h for h in INSTALL_MODE_HOSTS if h not in hosts]
    for raw in allow_host:
        h = (raw or "").strip().lower()
        if h and h not in hosts:
            hosts.append(h)
    return hosts


def main() -> None:
    ap = argparse.ArgumentParser(
        description="局域网中继：apt 代理 + 文件投递（可选放宽白名单）")
    ap.add_argument("--allow-host", action="append", default=[], metavar="HOST",
                    help="额外放行的上游域名，可重复；如 --allow-host api.deepseek.com")
    ap.add_argument("--install-mode", action="store_true",
                    help="放行包管理器域名（nodejs.org / registry.npmjs.org / pypi.org 等）——临时用，装完即关")
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    args = ap.parse_args()

    ALLOWED_HOSTS[:] = resolve_allowed_hosts(args.allow_host, args.install_mode)

    server = ThreadingHTTPServer((LISTEN_HOST, args.port), Handler)
    server.daemon_threads = True
    print(f"[relay] listening on {LISTEN_HOST}:{args.port}", flush=True)
    print(f"[relay] allowed clients: {sorted(ALLOWED_CLIENTS)}", flush=True)
    print(f"[relay] allowed upstream: {ALLOWED_HOSTS}", flush=True)
    if args.install_mode:
        print("[relay] ⚠️  install-mode 已开启：白名单被放宽，装完请立刻关掉", flush=True)
    print(f"[relay] file drop dir:   {RELAY_DIR}", flush=True)
    print("[relay] Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] stopped", flush=True)


if __name__ == "__main__":
    main()
