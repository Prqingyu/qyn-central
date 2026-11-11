#!/usr/bin/env python3
"""**规划与待办 → 推给状态墙**（`node = agent-plan`）。

用户 2026-10-17：「改成推送体系」。

原先大屏是轮询：页面每 60 秒拉 `/api/agent`，桥对规划再缓存 60 秒 ⇒ 规划改一次最坏 ~2 分钟
才上屏。现在由 systemd **path 单元**监听 `~/qyn/agent/data/*.json`，数据一变就调这个脚本：

    内核 data/*.json 变化 → 本脚本（只读内核）→ POST /api/telemetry → 状态墙 SSE → 大屏

帧的形状见 `CENTRAL/GUI/SCREEN-PROBES.md` 的「推送体系」一节（schema `qyn.agent-plan/1`）。

★ **只读**：只调内核的 `wall` / `sessions` / `context`（这三个都不写盘），不碰任何数据文件。
★ 单次失败不抛：推送失败下一轮重试；组帧缺项就少给，别让大屏白屏。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

AG = os.environ.get("QYN_AGENT_HOME") or os.path.expanduser("~/qyn/agent")
KERNEL = os.path.join(AG, "current", "AGENT", "qyn.py")
WALL_POST = os.environ.get("QYN_WALL_POST", "http://127.0.0.1:8080/api/telemetry")
NODE = "agent-plan"
FRESH_MS = 300_000          # 5 分钟：path 单元漏事件时 timer 兜底会补上
DAYS = 7                    # 课表/日程的窗口（屏幕只显示近几天 ✓）
# ★★ 待办（DUE）的窗口要**大得多**（用户 2026-10-23："12 月的 示例平台作业DDL 为什么没渲染进 DUE"✗）：
#   以前 due 和 sessions 共用 `DAYS = 7` ✗ ⇒ 12-28 的 示例平台作业（lead_days=14、还剩 66 天）
#   被内核的 7 天窗口挡在外面，DUE 里根本没有它 ✗。
#   待办本来就是"远期也要记住"的东西 ⇒ 单独用大窗口（≈13 个月，够跨一学年）。
DUE_DAYS = 400


def _run_kernel(*args: str) -> dict:
    """跑一条**只读**内核命令，返回 JSON；失败给 {}（大屏少一块数据，也好过整帧没有）。"""
    try:
        p = subprocess.run(["python3", KERNEL, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        return json.loads(p.stdout)
    except Exception:                                   # noqa: BLE001
        return {}


def _trim_session(s: dict) -> dict:
    """课次**整份透传**（含 water —— 页面据此标「（课·水）」）。"""
    return dict(s)


def _trim_due(x: dict) -> dict:
    """待办**整份透传**（含 gist/urgency/lead_days —— 页面据此上色、显示详情）。

    ★ 曾经用白名单剪字段，结果推送态比轮询态少了一堆东西：AGENT 时段行没有详细任务白字、
      不上色、不跟着 DDL 闪（大屏那边反馈过）。帧才 ~6KB、上限 64KB ⇒ 不值得剪。
    """
    return dict(x)


def _due_sort_key(x: dict):
    """DDL **严格按日期升序**（用户 2026-10-23："顺序乱了，和显示多少条无关"）。

    内核给的 `due` 是按 urgency 分档排的 ✗ —— 12-28 的 示例平台作业会排在 10-27 前面 ✗。
    这里只按 `date`（YYYY-MM-DD 字符串比较 = 时间顺序 ✓）升序；**没有 DDL 日期的排最后** ✓。
    同一天的保持内核原顺序（`sorted` 稳定 ✓）。
    """
    d = str(x.get("date") or "").strip()
    return (0, d, 0) if d else (1, "", 0)


def _trim_slot(s: dict) -> dict:
    """规划时段**整份透传**（gist/urgency/ddl_in_minutes/flags —— 页面的详情行/上色/闪烁都靠它们）。"""
    return dict(s)


def _fit(frame: dict) -> dict:
    """体积兜底：状态墙单帧上限 64KB。

    真机实测整份透传约 6-12KB，远没到上限；只有异常膨胀时才丢最大的 `gist`（冗余正文）。
    """
    if len(json.dumps(frame, ensure_ascii=False)) <= 48 * 1024:
        return frame
    for s in frame.get("plan", {}).get("slots") or []:
        s.pop("gist", None)
    for x in frame.get("time", {}).get("due") or []:
        x.pop("gist", None)
    return frame


def build_frame() -> dict:
    wall = _run_kernel("wall", "--days", str(DAYS))
    ses = _run_kernel("sessions", "--days", str(DAYS))
    # ★ 待办单独再取一次（大窗口）：远期 DDL 也必须进 DUE ✓（课表仍用 DAYS 的小窗口 ✓）
    due_src = _run_kernel("sessions", "--days", str(DUE_DAYS))
    ctx = _run_kernel("context")

    lp = (ctx.get("last_plan") or {}) if isinstance(ctx, dict) else {}
    stale = lp.get("stale")
    slots = [_trim_slot(s) for s in (wall.get("slots") or [])]
    problems = wall.get("problems") or []

    # 档位：内核说规划有问题 ⇒ bad；规划作废 ⇒ warn；否则 ok
    if wall.get("ok") is False or problems:
        level = "bad"
    elif stale:
        level = "warn"
    else:
        level = "ok"

    now = ses.get("now") or wall.get("now") or {}
    first = slots[0] if slots else None
    if first:
        head = "%s %s · 下一块 %s %s" % (str(now.get("date") or "")[5:].replace("-", "/"),
                                         now.get("weekday") or "", first.get("time") or "",
                                         str(first.get("task") or "")[:28])
    else:
        head = "没有排任何时段"

    return _fit({
        "schema": "qyn.agent-plan/1",
        "generated_at_ms": int(time.time() * 1000),
        "fresh_for_ms": FRESH_MS,
        "level": level,
        "headline": head,
        "plan": {
            "created_at": wall.get("created_at") or lp.get("created_at") or "",
            "stale": bool(stale) if stale is not None else None,
            "focus": wall.get("focus") or "",
            "order": wall.get("order") or [],
            "slots": slots,
            # ★ 提醒（用户 2026-11-08）：**不是时段**，独立于规划 —— 大屏只显示"几点提醒什么"
            "reminders": [
                {"at": str(r.get("at") or ""), "date": str(r.get("date") or ""),
                 "time": str(r.get("time") or ""), "text": str(r.get("text") or "")}
                for r in (wall.get("reminders") or [])][:4],
            "problems": problems,
        },
        "time": {
            "now": {k: now.get(k) for k in ("date", "time", "weekday", "phase") if now.get(k)},
            "due": [_trim_due(x) for x in sorted(
                ((due_src or {}).get("due") or ses.get("due") or []), key=_due_sort_key)],
            "sessions": [_trim_session(s) for s in (ses.get("sessions") or [])],
        },
    })


def selftest() -> int:
    """夹具自测：把内核调用换成假数据，验证组帧与档位判断（不碰真机、不碰数据）。"""
    global _run_kernel
    ok = 0

    def check(cond, label):
        nonlocal ok
        assert cond, "FAIL " + label
        ok += 1
        print("  [ok] " + label)

    def fake(*args):
        if args[0] == "wall":
            return {"ok": True, "created_at": "2026-10-18 00:10:00",
                    "focus": "先清周末硬截止", "order": ["示例课程H 报告"],
                    "slots": [{"date": "2026-10-18", "time": "08:30-11:50",
                               "task": "示例课程H 第二次实验报告",
                               "why": "上午整块", "passed": False, "gist": "x" * 500,
                               "urgency": "later", "ddl_in_minutes": 2880,
                               "flags": []}],
                    "problems": []}
        if args[0] == "sessions":
            near = {"now": {"date": "2026-10-18", "time": "00:10", "weekday": "日", "phase": "凌晨"},
                    "due": [{"task": "示例课程H 报告", "date": "2026-10-18", "why": "还剩 19 小时",
                             "urgency": "soon", "lead_days": 3, "gist": "y" * 500}],
                    "sessions": [{"date": "2026-10-18", "time": "14:00-15:40",
                                  "course": "示例课程B", "location": "示例教室X1", "gist": "z" * 500}]}
            # ★ 大窗口（待办）才返回的**远期**条目：12-28 的 示例平台作业（用户 2026-10-23 的案子）
            #   故意放在**最前面**、再塞一条无日期的 ⇒ 验证"严格按日期排、无日期排最后" ✓
            if "--days" in args and int(args[args.index("--days") + 1]) > DAYS:
                near["due"] = [{
                    "task": "示例平台作业", "date": "2026-12-28", "due_time": "09:00",
                    "lead_days": 14, "urgency": "normal", "why": "还剩 66 天"},
                    {"task": "没有 DDL 日期的复习", "urgency": "later", "why": "没有 DDL 日期"},
                ] + near["due"]
            return near
        return {"last_plan": {"created_at": "2026-10-18 00:10:00", "stale": False}}

    _run_kernel = fake
    f = build_frame()
    check(f["schema"] == "qyn.agent-plan/1", "schema 对")
    check(f["level"] == "ok", "干净时 ok")
    check(f["plan"]["slots"][0]["task"].startswith("示例课程H"), "规划时段带出来")
    check(f["plan"]["slots"][0].get("gist"), "整份透传：时段带 gist（详情行靠它）")
    check(f["plan"]["slots"][0].get("urgency") == "later", "整份透传：时段带 urgency（上色靠它）")
    check(f["time"]["due"][0]["lead_days"] == 3, "待办带 lead_days")
    check(f["time"]["due"][0].get("gist"), "整份透传：待办带 gist")
    check(f["time"]["sessions"][0]["course"] == "示例课程B", "课次带出来")
    # ★ 远期 DDL 必须进 DUE（用户 2026-10-23）：12-28 的 示例平台作业在大窗口里 ✓
    check(any("示例平台作业" in str(x.get("task") or "") for x in f["time"]["due"]),
          "远期 DDL（12-28）也进 DUE")
    # ★ DDL **严格按日期升序**、无日期的排最后（用户 2026-10-23："顺序乱了"）
    tasks = [str(x.get("task") or "") for x in f["time"]["due"]]
    check(tasks[0].startswith("示例课程H 报告"), "最早的 DDL 排第一（内核给的顺序不可信 ✗）")
    check(tasks[-1].startswith("没有 DDL 日期"), "没有 DDL 日期的排最后")
    check(tasks.index("示例平台作业") > tasks.index("示例课程H 报告"),
          "12-28 排在 10-18 之后")
    check(all(str(s.get("date") or "") <= "2026-10-25" for s in f["time"]["sessions"]),
          "课表仍按小窗口（不把远期课次塞进日程）")
    check("示例课程H" in f["headline"], "headline 指向下一块")

    # 规划有问题 ⇒ bad
    def fake_bad(*args):
        if args[0] == "wall":
            return {"ok": False, "problems": [{"kind": "unscheduled", "task": "X"}],
                    "slots": [], "created_at": "t"}
        if args[0] == "sessions":
            return {"now": {"date": "2026-10-18"}, "due": [], "sessions": []}
        return {"last_plan": {"stale": False}}

    _run_kernel = fake_bad
    check(build_frame()["level"] == "bad", "规划有问题 ⇒ bad")

    # 规划作废 ⇒ warn
    def fake_stale(*args):
        if args[0] == "wall":
            return {"ok": True, "problems": [], "slots": [], "created_at": "t"}
        if args[0] == "sessions":
            return {"now": {"date": "2026-10-18"}, "due": [], "sessions": []}
        return {"last_plan": {"stale": True}}

    _run_kernel = fake_stale
    check(build_frame()["level"] == "warn", "规划作废 ⇒ warn")

    # 内核全挂 ⇒ 不抛，仍出合法帧
    _run_kernel = lambda *a: {}
    f4 = build_frame()
    check(f4["schema"] == "qyn.agent-plan/1" and f4["plan"]["slots"] == [], "内核挂了也不抛")
    check(len(json.dumps(f4, ensure_ascii=False)) < 64 * 1024, "帧在 64KB 上限内")

    print("  ---- 自测 %d 项全过 ----" % ok)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="规划与待办 → 状态墙（只读）")
    ap.add_argument("--json", action="store_true", help="只把帧打到 stdout")
    ap.add_argument("--post", action="store_true", help="推给状态墙（node=agent-plan）")
    ap.add_argument("--selftest", action="store_true", help="夹具自测")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    frame = build_frame()
    body = json.dumps({"node": NODE, "frame": frame}, ensure_ascii=False)

    if args.post:
        try:
            req = urllib.request.Request(
                WALL_POST, data=body.encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json; charset=utf-8"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                code = resp.status
            if not args.json:
                print(f"已推送 {code} level={frame['level']} slots={len(frame['plan']['slots'])}"
                      f" due={len(frame['time']['due'])}", file=sys.stderr)
        except Exception as exc:                        # noqa: BLE001
            print(f"（推送失败：{type(exc).__name__}: {exc}）", file=sys.stderr)
            if not args.json:
                return 1

    if args.json or not args.post:
        print(json.dumps(frame, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
