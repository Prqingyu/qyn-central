#!/usr/bin/env python3
"""test_wall.py — 状态墙 S1 端到端测试

覆盖 S1 的四条链路 + 两条硬规则：
  1. ingest → 状态层 → 查询（HTTP POST 到落库到读回）
  2. SSE 推送（订阅后即时收到新样本）
  3. 采集器的 HttpSink 与 ingest 端点的契约；上报失败**不能**抛异常
  4. 陈旧度：离线保留最后已知值，并如实告诉页面"这是多久以前的"
  5. ★ 零 CDN：页面上不许有任何外部资源引用（局域网没有外网）
  6. 输入校验：缺 node / 坏 JSON / 空 body 都要被拒

纯 stdlib，无第三方依赖，全程用内存库与随机端口，不碰真实数据与文件。

用法：python CENTRAL/GUI/status-wall/test_wall.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from qynwall.server import WallServer          # noqa: E402
from qynwall.sinks import HttpSink             # noqa: E402
from qynwall.state import StateStore           # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def sample(node="central", cpu=12.3, **kw):
    s = {
        "node": node, "ts": "2026-10-09T00:40:00+08:00", "online": True,
        "platform": "linux",
        "cpu": {"pct": cpu, "load1": 0.42, "cores": 8},
        "mem": {"used_pct": 41.0, "used_mb": 6700, "total_mb": 16384},
        "disk": {"used_pct": 5.0, "free_gb": 220.0, "total_gb": 233.0},
        "net": {"rx_kbps": 120.0, "tx_kbps": 30.0},
        "extra": {},
    }
    s.update(kw)
    return s


def _get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _post(url, payload, timeout=5):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _read_sse_event(resp, timeout=6.0):
    """读到一条 data: 行就返回（心跳是注释行，会被跳过）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = resp.readline()
        if not raw:
            return None
        line = raw.decode("utf-8").strip()
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return None


# ────────────────────────────── 用例 ──────────────────────────────

def case_ingest_roundtrip(base):
    code, _ = _post(base + "/api/telemetry", sample())
    assert code == 202, f"ingest 应返回 202，实际 {code}"
    _, state = _get_json(base + "/api/state")
    nodes = {n["node"]: n for n in state["nodes"]}
    assert "central" in nodes, f"落库后查不到节点: {state}"
    n = nodes["central"]
    assert n["cpu"]["pct"] == 12.3 and n["mem"]["used_mb"] == 6700, n
    assert "stale_seconds" in n and n["stale_seconds"] < 5, n
    assert "received_at" in n, "缺 received_at（陈旧度要靠它算）"
    return f"POST 202 -> 读回一致，陈旧度 {n['stale_seconds']}s"


def case_upsert_semantics(base):
    _post(base + "/api/telemetry", sample(cpu=11.0))
    _post(base + "/api/telemetry", sample(cpu=88.0))
    _, state = _get_json(base + "/api/state")
    hits = [n for n in state["nodes"] if n["node"] == "central"]
    assert len(hits) == 1, f"同一节点应只保留一条最后已知值，实际 {len(hits)} 条"
    assert hits[0]["cpu"]["pct"] == 88.0, "没有更新为最新值"
    return "同节点二次上报 -> 只留一条，值为最新（最后已知值语义）"


def case_staleness_kept():
    """节点离线后要保留最后已知值，并给出越来越大的陈旧度。"""
    store = StateStore(":memory:")
    old = time.time() - 3600
    store.upsert(sample(node="win-dev"), received_at=old)
    got = store.get("win-dev")
    assert got is not None, "离线节点被丢弃了 —— 应保留最后已知值"
    assert 3595 < got["stale_seconds"] < 3610, got["stale_seconds"]
    assert got["cpu"]["pct"] == 12.3, "陈旧样本的字段应原样保留"
    store.close()
    return f"1 小时前的样本仍在，陈旧度 {got['stale_seconds']}s"


def case_sse_push(base):
    resp = urllib.request.urlopen(base + "/api/stream", timeout=8)
    try:
        first = _read_sse_event(resp)
        assert first and first.get("type") == "snapshot", \
            f"连接后应先收到全量快照，实际 {first}"
        _post(base + "/api/telemetry", sample(cpu=55.5))
        got = None
        for _ in range(3):
            ev = _read_sse_event(resp)
            if ev and ev.get("type") == "sample":
                got = ev
                break
        assert got is not None, "POST 之后没有收到 sample 推送"
        assert got["node"]["cpu"]["pct"] == 55.5, got
        return f"快照 -> 新样本即时推送（cpu={got['node']['cpu']['pct']}）"
    finally:
        resp.close()


def case_broadcaster_bounded(base):
    """慢客户端不能把内存吃光：队列有界，丢弃要计数。"""
    wall = WallServer(StateStore(":memory:"), host="127.0.0.1", port=0)
    try:
        q = wall.broadcaster.subscribe()
        for i in range(100):
            wall.broadcaster.publish({"node": "x", "i": i})
        assert q.qsize() <= 32, f"队列未受界：{q.qsize()}"
        assert wall.broadcaster.dropped >= 60, \
            f"丢弃未计数：dropped={wall.broadcaster.dropped}"
        return f"队列封顶 {q.qsize()}，丢弃如实计数 {wall.broadcaster.dropped}"
    finally:
        wall.broadcaster.unsubscribe(q)


def case_http_sink_contract(base):
    """采集器的 HttpSink 与 ingest 端点的契约。"""
    sink = HttpSink(base + "/api/telemetry", timeout=5)
    sink.start("win-dev")
    sink.emit(sample(node="win-dev", cpu=7.7))
    assert sink.failures == 0, f"上报失败: {sink.failures}"
    _, state = _get_json(base + "/api/state")
    hit = [n for n in state["nodes"] if n["node"] == "win-dev"]
    assert hit and hit[0]["cpu"]["pct"] == 7.7, state
    return "HttpSink.emit -> 服务端落库（中文与浮点均无损）"


def case_http_sink_failure_tolerated():
    """上报失败不能抛异常 —— 网络抖一下就让采集器退出是最糟的行为。"""
    sink = HttpSink("http://127.0.0.1:1/api/telemetry", timeout=1)
    sink.emit(sample())            # 不该抛
    assert sink.failures == 1, sink.failures
    sink.emit(sample())
    assert sink.failures == 2, "失败次数没有累加"
    return "连不上时只记失败次数，不抛异常（下一轮重试）"


def case_input_validation(base):
    """缺 node / 坏 JSON 都要被拒，且**错误响应本身**必须是可读的。

    ⚠️ 这里回归的是一个真 bug：把中文错误信息塞进 `send_error` 的 reason 会因
    HTTP 状态行是 latin-1 而抛 UnicodeEncodeError，**直接掐断连接** ——
    客户端只看到 RemoteDisconnected，看不出真实原因。所以断言错误响应是
    UTF-8 的 JSON body，而不是"连接莫名其妙断了"。
    """
    code, body = _post(base + "/api/telemetry", {"cpu": {"pct": 1.0}})   # 缺 node
    assert code == 422, f"缺 node 应 422，实际 {code}"
    payload = json.loads(body)
    assert payload.get("ok") is False, f"错误响应不是 JSON: {body[:120]!r}"
    assert "node" in payload.get("error", ""), f"错误信息丢了: {payload}"

    req = urllib.request.Request(base + "/api/telemetry", data=b"{not json",
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("坏 JSON 竟然被接受")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"坏 JSON 应 400，实际 {e.code}"
        assert b"bad json" in e.read(), "400 的说明文字丢了"
    _, state = _get_json(base + "/api/state")
    assert all(n["node"] != "" for n in state["nodes"]), "写入了无 node 的脏数据"
    return "缺 node -> 422(UTF-8 JSON body)；坏 JSON -> 400；无脏数据落库"


def case_zero_cdn(base):
    """★ 局域网没有外网：页面不许引用任何外部资源。"""
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        assert r.status == 200
        html = r.read().decode("utf-8")
    assert "状态墙" in html, "页面内容异常"

    external = []
    for attr in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html):
        if attr.startswith(("http://", "https://", "//")):
            external.append(attr)
    for m in re.findall(r'@import[^;]+;', html):
        external.append(m.strip())
    for m in re.findall(r'url\(\s*["\']?(https?:|//)', html):
        external.append(m)
    assert not external, f"页面引用了外部资源（局域网会加载失败）: {external}"

    assert "<script src=" not in html, "存在外部脚本标签"
    return f"页面 {len(html)} 字节，零外部资源引用（src/href/@import/url()）"


def run() -> int:
    store = StateStore(":memory:")
    wall = WallServer(store, host="127.0.0.1", port=0)
    port = wall.start()
    base = f"http://127.0.0.1:{port}"
    print("=" * 66)
    print("  状态墙 S1 端到端测试（内存库 + 随机端口，不碰真实数据）")
    print("=" * 66)
    print(f"服务: {base}")
    print("-" * 66)

    cases = [
        ("ingest 落库并读回", lambda: case_ingest_roundtrip(base)),
        ("最后已知值（upsert）语义", lambda: case_upsert_semantics(base)),
        ("离线保留最后值 + 陈旧度", case_staleness_kept),
        ("SSE 即时推送", lambda: case_sse_push(base)),
        ("推送队列有界且丢弃计数", lambda: case_broadcaster_bounded(base)),
        ("HttpSink 与 ingest 契约", lambda: case_http_sink_contract(base)),
        ("上报失败被容忍", case_http_sink_failure_tolerated),
        ("输入校验拒绝脏数据", lambda: case_input_validation(base)),
        ("零 CDN 硬规则", lambda: case_zero_cdn(base)),
    ]
    results = []
    for name, fn in cases:
        try:
            results.append((name, True, fn()))
        except AssertionError as exc:
            results.append((name, False, str(exc)))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    wall.stop()
    store.close()

    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"       {detail}")
    failed = [r for r in results if not r[1]]
    print("-" * 66)
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
