#!/usr/bin/env python3
"""**Agent 系统探针** —— 把"这套值守系统自己现在怎么样"变成一帧结构化数据。

给谁用：大屏（SERVICE 面板）。用户 2026-10-16：「最后一轮时刻和其他关于 agent 系统的探针
准备好接口。我要让另一个 agent 并入大屏。」

两条出口（同源，取谁都行）：
  1. `POST 状态墙 /api/telemetry`，`node = svc-agent`（与 svc-opencode / svc-egress 同一条通路、
     同一个 svc- 前缀约定；大屏从 SSE 流里读 `sample.frame`）
  2. 落一份 JSON 到 `~/qyn/agent/logs/agent-probe.json`（大屏那侧也可以直接读文件）

帧的形状（与 svc-egress 一致，大屏按 `items` 逐行渲染，不硬编码字段语义）：

    {
      "schema": "qyn.svc-agent/1",
      "level": "ok" | "warn" | "bad",        # 取各项里最差的
      "headline": "最后一轮 12:46 · 退出码 0",
      "generated_at_ms": 1792...,
      "fresh_for_ms": 300000,                # 超过就该显示"数据陈旧"
      "items": [ {"key": "唤醒链", "value": "下一个 10/17 11:30", "level": "ok"}, … ]
    }

★ **只读**：不碰任何数据文件，不改状态；解析的都是日志与 systemd 的只读查询。
★ 单次失败不抛：某一项取不到就写"取不到"，其余照常 —— 探针本身绝不能成为故障源。
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
from datetime import datetime

# 基目录可用环境变量覆盖 —— 自测要用夹具，且不该碰真机的日志。
AG = os.environ.get("QYN_AGENT_HOME") or os.path.expanduser("~/qyn/agent")
TICK = os.path.join(AG, "logs", "tick.log")
PHONE_LOG = os.path.join(AG, "logs", "phone.log")
INBOX = os.path.join(AG, "phone-state", "inbox.jsonl")
MEDIA = os.path.join(AG, "phone-state", "media")
KERNEL = os.path.join(AG, "current", "AGENT", "qyn.py")
OUT_FILE = os.path.join(AG, "logs", "agent-probe.json")
WALL_POST = os.environ.get("QYN_WALL_POST", "http://127.0.0.1:8080/api/telemetry")
NODE = "svc-agent"
# 一轮"跑了多久算卡住"（秒）。桥那边单轮上限 900s，超过再加余量才算真卡 ——
# 在此之前"正在跑"是**正常状态**，不许报黄（用户 2026-10-18）。
STUCK_SECS = 1200
FRESH_MS = 300_000


def _sh(cmd: str, timeout: int = 20) -> str:
    """跑一条只读命令；失败给空串（探针不该因为一条命令失败就整体挂掉）。"""
    try:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "").strip()
    except Exception:                                  # noqa: BLE001
        return ""


def _tail(path: str, n: int = 400) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except OSError:
        return []


def _unit_active(name: str) -> str:
    return _sh(f"systemctl --user is-active {name}") or "unknown"


def _has_boundary_timer() -> bool:
    """到点唤醒的 boundary 定时器在不在（在 ⇒ 链条还接着）。"""
    return _sh("systemctl --user list-timers --no-pager | grep -c qyn-agent-boundary").strip() not in ("", "0")


# ── 各项探针 ────────────────────────────────────────────────────────────────

def probe_last_round() -> dict:
    """**最后一轮**：时刻 / 模式 / 退出码 / 耗时。

    ★ 配对必须**按行序**：先找到最后一条"跑一轮"，再只在它**之后**找"这一轮退出码"。
      只看模式名会把上一轮的退出码配到这一轮（自测抓到：追加一行"跑一轮"后本该 warn，却报成 bad）。
    """
    lines = _tail(TICK, 600)
    i_start = None
    for i in range(len(lines) - 1, -1, -1):
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\[(\w+)\]\s+跑一轮", lines[i])
        if m:
            i_start, start = i, (m.group(1), m.group(2))
            break
    if i_start is None:
        return {"key": "最后一轮", "value": "没有记录", "level": "warn"}

    rc = None
    for l in lines[i_start + 1:]:
        m2 = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\[(\w+)\]\s+这一轮退出码\s+(-?\d+)", l)
        if m2:
            rc = (m2.group(1), m2.group(2), int(m2.group(3)))
            break

    hhmm = start[0][11:16]
    if rc:
        secs = ""
        try:
            a = datetime.strptime(start[0], "%Y-%m-%d %H:%M:%S")
            b = datetime.strptime(rc[0], "%Y-%m-%d %H:%M:%S")
            secs = f" · {int((b - a).total_seconds())}s"
        except ValueError:
            pass
        # ★ 退出码非 0 要**随时间衰减**（用户 2026-10-19 / 10-20："一次失败不该永久红，也别黄一整晚"）：
        #   · <30 分钟 ⇒ bad（当前故障，立刻看）
        #   · 30~45 分钟 ⇒ warn（刚过去，注意一下）
        #   · >45 分钟 ⇒ ok（历史；**事实仍写在值里** "23:30 · 退出码 1" ✓ 不会被藏起来）
        if rc[2] == 0:
            lv = "ok"
        else:
            try:
                ended = datetime.strptime(rc[0], "%Y-%m-%d %H:%M:%S")
                failed_min = (datetime.now() - ended).total_seconds() / 60
            except ValueError:
                failed_min = 0
            lv = "bad" if failed_min < 30 else ("warn" if failed_min < 45 else "ok")
        val = f"{hhmm} {start[1]} · 退出码 {rc[2]}{secs}"
    else:
        # 有开始没有结束：**这一轮正在跑 ⇒ 正常** ✓
        #   （用户 2026-10-18："接收/调用 agent 中是顺利工作，不该报黄" ✗）
        # 只有"跑得比任何一轮都可能还久"才当卡住 ⇒ 黄。阈值 = 桥那边的 900s 上限 + 余量。
        try:
            run_s = (datetime.now() - datetime.strptime(start[0], "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            run_s = None
        if run_s is None:
            lv, val = "warn", f"{hhmm} {start[1]} · 有开始没结束（时刻读不出）"
        elif run_s <= STUCK_SECS:
            lv, val = "ok", f"{hhmm} {start[1]} · 运行中 {int(run_s)}s"
        else:
            lv, val = "warn", f"{hhmm} {start[1]} · 运行 {int(run_s // 60)} 分（可能卡住）"

    try:
        age_h = (datetime.now() - datetime.strptime(start[0], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        if lv == "ok" and age_h > 30:
            lv = "warn"                  # 一整天没跑过任何一轮（锚点/唤醒都没生效）
    except ValueError:
        pass
    return {"key": "最后一轮", "value": val, "level": lv}


def probe_chain() -> dict:
    """**唤醒链**：下一个到点唤醒的时刻与理由（从 tick.log 的最后一条"已排下一次"）。"""
    lines = _tail(TICK, 400)
    for l in reversed(lines):
        m = re.search(r"已排下一次唤醒：(\S+ \S+)\s*（([^）]*)）", l)
        if m:
            when, why = m.group(1), m.group(2)
            hhmm = when[5:16]
            lv = "ok" if _has_boundary_timer() else "bad"
            extra = "" if lv == "ok" else " · 定时器不在"
            return {"key": "唤醒链", "value": f"下一个 {hhmm}（{why[:22]}）{extra}", "level": lv}
    return {"key": "唤醒链", "value": "没有排出过", "level": "warn"}


def probe_guard() -> dict:
    """**守卫**：最后一次守卫的时刻与结论（空转 / 补跑）。"""
    lines = _tail(TICK, 400)
    for l in reversed(lines):
        if "[guard]" not in l:
            continue
        hhmm = l[11:16]
        if "守卫不做事" in l:
            return {"key": "守卫", "value": f"{hhmm} 空转（边界未到）", "level": "ok"}
        if "补跑一轮" in l:
            return {"key": "守卫", "value": f"{hhmm} 补跑（链条曾断）", "level": "warn"}
        return {"key": "守卫", "value": f"{hhmm} {l[17:60]}", "level": "ok"}
    return {"key": "守卫", "value": "没有记录", "level": "warn"}


def probe_phone() -> dict:
    """**微信通道**：接收方状态 / 游标 / 最后一条消息。"""
    act = _unit_active("qyn-phone.service")
    last = ""
    kind = ""
    try:
        with open(INBOX, encoding="utf-8", errors="replace") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        if lines:
            o = json.loads(lines[-1])
            kind = str(o.get("kind") or "")
            ts = o.get("received_at")
            if ts:
                last = datetime.fromtimestamp(float(ts)).strftime("%m/%d %H:%M")
    except Exception:                                  # noqa: BLE001
        pass
    if act != "active":
        return {"key": "微信通道", "value": f"接收方 {act}", "level": "bad"}
    val = f"接收中 · 最后 {last} {kind}".strip()
    return {"key": "微信通道", "value": val, "level": "ok"}


def probe_poll() -> dict:
    """**接收方轮询**：最后一次长轮询回来的时刻（心跳）。

    ★ 游标 mtime **不能**当心跳（`save_cursor` 值没变就不写）—— 2026-10-17 就是靠这个
      才看不出"接收方是不是还在收"，白翻了一轮日志。
    """
    path = os.path.join(os.path.dirname(INBOX), "poll.heartbeat")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return {"key": "接收方轮询", "value": "没有心跳（接收方没在跑？）", "level": "warn"}
    mins = age / 60
    lv = "ok" if mins < 5 else ("warn" if mins < 15 else "bad")
    val = f"{int(age)} 秒前" if age < 90 else f"{mins:.0f} 分钟前"
    return {"key": "接收方轮询", "value": val, "level": lv}


def probe_last_sent() -> dict:
    """**最后一条发出**：时刻 / 是否送达 / message_id。

    ★ 与「微信通道」（收）成对：只看得到收、看不到发，就会出现"用户说没收到回复、
      而系统这边无从判断"的场面（2026-10-16 实测：翻六处才确认其实发出去了）。
    """
    path = os.path.join(os.path.dirname(INBOX), "sent.jsonl")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
    except OSError:
        return {"key": "最后一条发出", "value": "还没发过", "level": "ok"}
    if not lines:
        return {"key": "最后一条发出", "value": "还没发过", "level": "ok"}
    try:
        o = json.loads(lines[-1])
        when = datetime.fromtimestamp(float(o.get("ts"))).strftime("%m/%d %H:%M")
    except Exception:                                   # noqa: BLE001
        return {"key": "最后一条发出", "value": "流水坏了", "level": "warn"}
    if o.get("ok"):
        return {"key": "最后一条发出",
                "value": f"{when} 已送达 {o.get('message_id')}", "level": "ok"}
    # ★ 失败记录里 `error` 常常是 null（clawbot 只在 stderr 里说原因）——
    #   2026-11-11 早报发失败时墙上只显示"失败（send：None）"，等于没说 ✗。
    why = o.get("error") or f"{o.get('attempts') or '?'} 次都没成（原因见 tick.log）"
    return {"key": "最后一条发出",
            "value": f"{when} 失败（{o.get('stage')}：{why}）", "level": "bad"}


def probe_pending_push() -> dict:
    """**待补发队列**：上一轮没发出去、等下轮重试的内容。

    ★ 2026-11-11 事故后加的：微信通道瞬时失败（DNS 解析失败 ⇒ prepare failed）时，
      正文会存进 `logs/pending-push.txt`，每轮开头先补发。墙上必须看得见"还有东西没发出去"，
      否则"掉线"永远是用户先发现 ✗。
    """
    path = os.path.join(os.path.dirname(INBOX), "..", "logs", "pending-push.txt")
    path = os.path.normpath(path)
    try:
        size = os.path.getsize(path)
        when = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%m/%d %H:%M")
    except OSError:
        return {"key": "待补发", "value": "无", "level": "ok"}
    if size <= 0:
        return {"key": "待补发", "value": "无", "level": "ok"}
    return {"key": "待补发", "value": f"{size} 字节 · {when} 起（每轮重试）", "level": "warn"}


def probe_media() -> dict:
    """**入站媒体**：落盘了几个、多大。"""
    try:
        files = [os.path.join(MEDIA, f) for f in os.listdir(MEDIA)]
        files = [f for f in files if os.path.isfile(f)]
    except OSError:
        files = []
    if not files:
        return {"key": "入站媒体", "value": "0 个", "level": "ok"}
    total = sum(os.path.getsize(f) for f in files)
    newest = max(os.path.getmtime(f) for f in files)
    return {"key": "入站媒体",
            "value": f"{len(files)} 个 · {total / 1024:.0f}KB · 最近 {datetime.fromtimestamp(newest):%m/%d %H:%M}",
            "level": "ok"}


def probe_kernel() -> dict:
    """**内核**：当前 release + 插件注册的工具数。"""
    rel = os.path.basename(os.path.realpath(os.path.join(AG, "current"))) or "?"
    tools = ""
    for l in reversed(_tail(TICK, 400)):
        m = re.search(r"已注册 (\d+) 个工具", l)
        if m:
            tools = f" · 工具 {m.group(1)}"
            break
    exists = os.path.isfile(KERNEL)
    return {"key": "内核", "value": f"{rel}{tools}", "level": "ok" if exists else "bad"}


def probe_data() -> dict:
    """**数据**：注册条目数 / 课次数 / 规划快照的时刻与新鲜度。"""
    def count(path: str, key: str) -> int:
        try:
            with open(path, encoding="utf-8") as fh:
                return len(json.load(fh).get(key) or [])
        except Exception:                              # noqa: BLE001
            return -1

    n_reg = count(os.path.join(AG, "data", "data.json"), "registry")
    n_ses = count(os.path.join(AG, "data", "schedule.json"), "dated_sessions")
    made = ""
    stale = None
    try:
        with open(os.path.join(AG, "data", "plans.json"), encoding="utf-8") as fh:
            plans = json.load(fh).get("plans") or []
        if plans:
            made = str(plans[-1].get("made_at") or "")
    except Exception:                                  # noqa: BLE001
        pass
    # 新鲜度用**内核自己的判据**（同一份实现，别在这儿再写一遍）
    try:
        p = subprocess.run(["python3", KERNEL, "context"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        stale = bool((json.loads(p.stdout).get("last_plan") or {}).get("stale"))
    except Exception:                                  # noqa: BLE001
        stale = None
    if stale is True:
        lv, tag = "warn", "已作废"
    elif stale is False:
        lv, tag = "ok", "新鲜"
    else:
        lv, tag = "ok", "?"
    return {"key": "数据",
            "value": f"注册 {n_reg} · 课次 {n_ses} · 快照 {made[5:16]} {tag}",
            "level": lv}


def build_frame() -> dict:
    items = [probe_last_round(), probe_chain(), probe_guard(),
             probe_phone(), probe_poll(), probe_last_sent(), probe_pending_push(), probe_media(),
             probe_kernel(), probe_data()]
    rank = {"ok": 0, "warn": 1, "bad": 2}
    level = max((i.get("level", "ok") for i in items), key=lambda x: rank.get(x, 0))
    last = items[0].get("value", "")
    return {
        "schema": "qyn.svc-agent/1",
        "level": level,
        "headline": f"最后一轮 {last.split(' ')[0] if last else '—'}",
        "generated_at_ms": int(time.time() * 1000),
        "fresh_for_ms": FRESH_MS,
        "items": items,
    }


def selftest() -> int:
    """夹具自测：日志解析最容易悄悄烂掉，正常与异常两条路径都要盯住。"""
    import shutil
    import tempfile

    global AG, TICK, PHONE_LOG, INBOX, MEDIA, KERNEL, OUT_FILE, _unit_active, _has_boundary_timer
    # 测试缝：探针本体跑在真机上（有 systemd），而自测要能在任何地方跑 ——
    # 把"单元是否 active"这一项替换成固定值，其余逻辑照走。
    _unit_active = lambda name: "active"            # noqa: E731
    _has_boundary_timer = lambda: True              # noqa: E731
    root = tempfile.mkdtemp(prefix="qyn-probe-")
    AG = root
    TICK = os.path.join(root, "logs", "tick.log")
    PHONE_LOG = os.path.join(root, "logs", "phone.log")
    INBOX = os.path.join(root, "phone-state", "inbox.jsonl")
    MEDIA = os.path.join(root, "phone-state", "media")
    KERNEL = os.path.join(root, "current", "AGENT", "qyn.py")
    OUT_FILE = os.path.join(root, "logs", "agent-probe.json")
    for d in (os.path.dirname(TICK), os.path.dirname(INBOX), MEDIA, os.path.dirname(KERNEL)):
        os.makedirs(d, exist_ok=True)
    open(KERNEL, "w").write("# stub\n")
    open(os.path.join(MEDIA, "a.jpg"), "wb").write(b"x" * 1024)
    open(os.path.join(os.path.dirname(INBOX), "poll.heartbeat"), "w").close()

    ok = 0

    def check(cond, label):
        nonlocal ok
        assert cond, "FAIL " + label
        ok += 1
        print("  [ok] " + label)

    # ① 正常一轮
    # ★ 时间戳必须**按"现在"生成**：写死过一次（2026-10-16 12:46），两天后自测就挂在
    #   "超过 30 小时没跑过任何一轮 ⇒ warn" 上 —— 夹具会随时间腐烂，发布闸会拦住（实测踩到）。
    from datetime import timedelta
    _n = datetime.now()
    def _stamp(minutes_ago: int) -> str:
        return (_n - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    # 开始/结束要**恰好差 37 秒**（下面断言耗时），否则算出了耗时会挂
    _start = (_n - timedelta(minutes=21)).strftime("%Y-%m-%d %H:%M:%S")
    _end = (_n - timedelta(minutes=21) + timedelta(seconds=37)).strftime("%Y-%m-%d %H:%M:%S")
    _next, _guard = _stamp(20), _stamp(3)
    _tomorrow = (_n + timedelta(days=1)).strftime("%Y-%m-%d 11:30")
    open(TICK, "w", encoding="utf-8").write(
        f"{_start}  [message] 跑一轮（下一块 09:30-11:30「示例课程A作业」结束）\n"
        "[qyn-time] 已注册 11 个工具；内核=/x\n"
        f"{_end}  [message] 这一轮退出码 0\n"
        f"{_next}  [message] 已排下一次唤醒：{_tomorrow}（下一块 09:30-11:30「示例课程A作业」结束）· 单元 qyn-agent-boundary\n"
        f"{_guard}  [guard] 边界还没到（{_tomorrow} · 下一块 …）—— 守卫不做事\n")
    open(INBOX, "w", encoding="utf-8").write(json.dumps(
        {"kind": "image", "received_at": 1792085223.0, "media_path": "/x/a.jpg"}) + "\n")
    f = build_frame()
    check(f["schema"] == "qyn.svc-agent/1", "schema 对")
    check(f["level"] == "ok", "正常时 level=ok")
    check(f["fresh_for_ms"] == FRESH_MS and f["generated_at_ms"] > 0, "新鲜度字段在")
    keys = [i["key"] for i in f["items"]]
    check(keys == ["最后一轮", "唤醒链", "守卫", "微信通道", "接收方轮询", "最后一条发出",
                   "待补发", "入站媒体", "内核", "数据"], "十项齐全且有序")
    check("退出码 0" in f["items"][0]["value"], "最后一轮读到退出码")
    check("37s" in f["items"][0]["value"], "算出了耗时")
    check(_tomorrow[5:16] in f["items"][1]["value"], "唤醒链读到下一个边界")
    check("空转" in f["items"][2]["value"], "守卫读到空转")
    check("image" in f["items"][3]["value"], "微信通道读到最后一条的类型")
    hb = os.path.join(os.path.dirname(INBOX), "poll.heartbeat")
    os.remove(hb)
    check(build_frame()["items"][4]["level"] == "warn", "没有心跳 ⇒ warn")
    open(hb, "w").close()
    check(build_frame()["items"][4]["level"] == "ok", "心跳新鲜 ⇒ ok")
    check(f["items"][5]["level"] == "ok", "还没发过 ⇒ ok（不是假警报）")
    # 写入一条成功的发出流水后再看
    with open(os.path.join(os.path.dirname(INBOX), "sent.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "ok": True, "peer_id": "p",
                             "message_id": 123, "stage": "send", "attempts": 1}) + "\n")
    f4 = build_frame()
    check("已送达 123" in f4["items"][5]["value"], "读到发出流水的 message_id")
    with open(os.path.join(os.path.dirname(INBOX), "sent.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "ok": False, "stage": "upload",
                             "error": "boom"}) + "\n")
    f4b = build_frame()
    check(f4b["items"][5]["level"] == "bad", "发送失败 ⇒ bad")
    check("1 个" in f["items"][7]["value"], "媒体个数")
    check("工具 11" in f["items"][8]["value"], "内核读到工具数")
    check(all(i.get("level") in ("ok", "warn", "bad") for i in f["items"]), "每项 level 合法")

    # ② 退出码非 0 ⇒ 该项 bad，整帧 bad
    open(TICK, "a", encoding="utf-8").write(
        f"{_stamp(2)}  [boundary] 跑一轮（…）\n"
        f"{_stamp(1)}  [boundary] 这一轮退出码 1\n")
    f2 = build_frame()
    check(f2["items"][0]["level"] == "bad", "退出码非 0 ⇒ bad")
    check(f2["level"] == "bad", "整帧取最差")

    # ②b 退出码 1 的**衰减窗口**（用户 2026-10-20："就这样一直黄着吗" ✗ ⇒ 别黄一整晚）
    open(TICK, "w", encoding="utf-8").write(
        f"{_stamp(41)}  [boundary] 跑一轮（…）\n"
        f"{_stamp(40)}  [boundary] 这一轮退出码 1\n")
    check(build_frame()["items"][0]["level"] == "warn", "40 分钟前的失败 ⇒ warn（注意一下）")
    open(TICK, "w", encoding="utf-8").write(
        f"{_stamp(301)}  [boundary] 跑一轮（…）\n"
        f"{_stamp(300)}  [boundary] 这一轮退出码 1\n")
    check(build_frame()["items"][0]["level"] == "ok", "5 小时前的失败 ⇒ ok（历史，值里仍写着退出码）")

    # ③ 有开始没结束：**刚起的一轮 = 正在跑 ⇒ ok**（不许报假警报 ✗，用户 2026-10-18）
    open(TICK, "a", encoding="utf-8").write(f"{_stamp(0)}  [boundary] 跑一轮（…）\n")
    f3 = build_frame()
    check(f3["items"][0]["level"] == "ok", "正在跑 ⇒ ok（不是警告）")
    # ③b 但跑得超过阈值 ⇒ 真卡住 ⇒ warn
    open(TICK, "w", encoding="utf-8").write(f"{_stamp(30)}  [boundary] 跑一轮（…）\n")
    check(build_frame()["items"][0]["level"] == "warn", "跑太久 ⇒ warn（可能卡住）")

    # ④ 守卫补跑 ⇒ warn
    open(TICK, "a", encoding="utf-8").write(
        f"{_stamp(0)}  [guard] 补跑一轮（链条曾断）\n")
    check(build_frame()["items"][2]["level"] == "warn", "守卫补跑 ⇒ warn")

    # ⑤ 日志全空 ⇒ 不抛异常，项给 warn/取不到
    open(TICK, "w", encoding="utf-8").write("")
    f5 = build_frame()
    check(len(f5["items"]) == 10, "空日志也出十项（不抛）")
    check(f5["items"][0]["level"] == "warn", "空日志 ⇒ 最后一轮 warn")

    shutil.rmtree(root, ignore_errors=True)
    print("  ---- 自测 %d 项全过 ----" % ok)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent 系统探针（只读）")
    ap.add_argument("--json", action="store_true", help="只把这一帧打到 stdout")
    ap.add_argument("--post", action="store_true", help="推给状态墙（node=svc-agent）")
    ap.add_argument("--no-file", action="store_true", help="不落 agent-probe.json")
    ap.add_argument("--selftest", action="store_true", help="用夹具自测（不碰真机数据）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    frame = build_frame()
    body = json.dumps({"node": NODE, "frame": frame}, ensure_ascii=False)

    if not args.no_file:
        try:
            with open(OUT_FILE, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(frame, ensure_ascii=False, indent=1))
        except OSError as exc:
            print(f"（落文件失败：{exc}）", file=sys.stderr)

    if args.post:
        try:
            req = urllib.request.Request(
                WALL_POST, data=body.encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json; charset=utf-8"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                code = resp.status
            if not args.json:
                print(f"已推送 {code} level={frame['level']}", file=sys.stderr)
        except Exception as exc:                       # noqa: BLE001
            print(f"（推送失败：{type(exc).__name__}: {exc}）", file=sys.stderr)
            if not args.json:
                return 1

    if args.json or not args.post:
        print(json.dumps(frame, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
