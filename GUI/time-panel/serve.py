#!/usr/bin/env python3
"""TIME AGENT 面板的**只读 HTTP 桥**。

为什么需要它：屏幕壳是浏览器里的页面，跑不了 Python；而时间管理的内核是一个 CLI
（`AGENT/qyn.py`）。这个桥只做一件事 —— 调内核的**只读**命令，把 JSON 转成 HTTP。

★★ 两条红线（都不许越）：
  1. **只读、零写副作用**：只调 `sessions` / `anomalies` / `context` / `brief` / `review` / `wall`
     （内核文档写明这些不写盘）。绝不调 apply / mark / sync / plan-snapshot ——
     写操作只属于时间管理 Agent，桥不许碰。
  2. **不自己算日期**：屏幕上"今天几号、周几、还剩多久"全部用内核给的
     （项目铁律：日期由内核算，别处心算必错）。桥只做转发 + 缓存。
  3. **不直接读 `AGENT/data/*.json`**：那是 Agent 的私有存储，`plan` 正文没有 schema 校验、
     随时可能改键；而且红线本身就禁止外部读写它。要什么就调内核的只读命令。

用法：
    python3 serve.py --port 8091 --kernel ~/qyn/agent/current/AGENT/qyn.py
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_KERNEL = os.path.expanduser("~/qyn/agent/current/AGENT/qyn.py")
CACHE_TTL = 15.0          # 秒：内核结果缓存这么久，页面轮询不会反复拉起进程
KERNEL_TIMEOUT = 30       # 秒：内核很轻（纯 stdlib），但别让它把服务拖死


class KernelError(RuntimeError):
    pass


# ── 对话（用户 2026-10-15）──────────────────────────────────────────────
# 墙上要能跟时间管理 Agent 说话。这一层只做一件事：把用户的话**交给 Agent 跑一轮**
# —— 和 systemd 的 `qyn-agent.service` 用**完全同一条命令、同一套环境**（同一个 profile、
# 同一个 DSH_HOME、同一个仓库根），把 Agent 说的话拿回来。
# ★ 红线仍然成立：桥自己**绝不调内核的写命令**。Agent 要改数据，是它自己经内核写。
# ★ 同一时刻只允许一轮：Agent 很重（会调模型、可能改数据），用锁串行化。
CHAT_LOCK = threading.Lock()
AGENT_RUN_TIMEOUT = 900          # 秒，和 qyn-agent.service 的 TimeoutStartSec 一致
CHAT_SESSION_FILE = os.path.expanduser("~/qyn/chat-session.txt")   # 墙上对话复用的会话 id

# ★★ 跨通道全局锁 + 幂等 + 忙时 429（用户 2026-10-20 的审查结论）────────────────
#  现状：三个入口各锁各的 —— 墙上对话（本进程 threading.Lock）、微信来消息、07:00 定时
#  （qyn-agent-tick.sh 自己一把 flock）；内核又没有锁 ⇒ 撞在一起会**并发跑同一个 Agent**
#  ⇒ 丢更新 / 重复登记 ✗。
#  这里统一成**同一个 flock 文件**（tick.sh 也指向它）：
#   · 谁先拿到谁跑；拿不到**立刻忙**（不排队、不自动重试 ✓，客户端自己决定要不要再来）
#   · 幂等：同一个 `client_msg_id` 重复到达 ⇒ 直接回上次结果，**不重复跑** ✓
GLOBAL_LOCK_FILE = os.path.expanduser("~/qyn/agent/logs/agent-run.lock")
GLOBAL_BUSY_RETRY_AFTER = 60          # 忙时告诉客户端"多少秒后再来"
CHAT_IDEM_FILE = os.path.expanduser("~/qyn/agent/logs/chat-idem.json")
CHAT_IDEM_TTL = 24 * 3600             # 去重窗口（24 小时）
CHAT_IDEM_MAX = 200                   # 最多记多少条（防文件无限长）


def global_lock_acquire():
    """**非阻塞**拿跨通道 flock。拿到返回打开的文件对象（调用方负责 close 释放），忙则 None。"""
    try:
        os.makedirs(os.path.dirname(GLOBAL_LOCK_FILE), exist_ok=True)
    except OSError:
        pass
    f = open(GLOBAL_LOCK_FILE, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


# ── 一键重排（用户 2026-11-08：「将重排写成一个接口 可以一键发起重排」）──────────
#   入口在大屏按钮上，动作**复用已有管道**：桥只负责"启动那一轮"，规划本身还是
#   `tick.sh replan` 做的（内核写、快照、推送大屏都是它的事）—— 同一件事只有一份实现。
#   ★ 为什么不让桥自己 spawn agent：单元自带环境/日志/超时，桥重启不会打断正在跑的那轮；
#     而且 `systemctl start` 天然做到"已经在跑就不重复起"（用户连点也只跑一轮）。
REPLAN_UNIT = "qyn-agent-replan.service"
REPLAN_START_WAIT = 5.0       # 秒：start --no-block 之后等它真的进 active，免得页面以为"秒完"


def _systemctl(*args: str, timeout: float = 12.0):
    """调 systemctl --user（带正确的 XDG_RUNTIME_DIR，桥不是登录 shell 起的环境）。"""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True, timeout=timeout, env=env)


def replan_status() -> dict:
    """**只查询**不触发：给页面轮询用（按钮点完要能知道什么时候排完了）。"""
    try:
        p = _systemctl("show", REPLAN_UNIT, "-p", "ActiveState", "-p", "SubState",
                       "-p", "ExecMainStartTimestamp", "-p", "ExecMainExitTimestamp",
                       "-p", "ExecMainStatus")
    except (OSError, subprocess.TimeoutExpired) as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if p.returncode != 0:
        return {"ok": False,
                "error": (p.stderr or "").strip()[:300] or f"systemctl 退出码 {p.returncode}"}
    fields = {}
    for line in (p.stdout or "").splitlines():
        k, _, v = line.partition("=")
        fields[k.strip()] = v.strip()
    active = fields.get("ActiveState") or "unknown"
    running = active in ("active", "activating", "reloading")
    try:
        code = int(fields.get("ExecMainStatus") or 0)
    except ValueError:
        code = 0
    return {"ok": True, "unit": REPLAN_UNIT, "active": active,
            "state": "running" if running else "idle",
            "started_at": fields.get("ExecMainStartTimestamp") or "",
            "finished_at": fields.get("ExecMainExitTimestamp") or "",
            "exit_status": code}


def replan_start() -> tuple[dict, int]:
    """发起一轮重排。返回 (payload, http_code)。

    三种结果，页面据此显示不同文案：
      · 已经在跑（或刚起来）⇒ 202 + state=running/already（**连点也只跑一轮**）
      · 另一轮占着锁（墙对话/手机/定时）⇒ **429 + busy**（不排队、不重试 —— 问用户要不要等）
      · 启动失败 ⇒ 500 + error
    """
    st = replan_status()
    if st.get("ok") and st.get("state") == "running":
        return {"ok": True, "state": "running", "already": True,
                "started_at": st.get("started_at") or ""}, 202
    # 锁是**非阻塞**试探：拿到就立刻放开，只是为了确认"没有别的轮在跑"
    lockf = global_lock_acquire()
    if lockf is None:
        return {"ok": False, "busy": True,
                "error": "另一轮还在跑（墙/手机/定时共用同一把锁），稍后再点"}, 429
    try:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        lockf.close()
    try:
        p = _systemctl("start", "--no-block", REPLAN_UNIT)
    except (OSError, subprocess.TimeoutExpired) as e:   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}, 500
    if p.returncode != 0:
        return {"ok": False,
                "error": (p.stderr or "").strip()[:300] or f"systemctl 退出码 {p.returncode}"}, 500
    # 等它真的进 active（--no-block 只是入队；不等的话页面第一次轮询可能读到 idle ⇒ 以为秒完）
    waited = 0.0
    while waited < REPLAN_START_WAIT:
        time.sleep(0.25)
        waited += 0.25
        now = replan_status()
        if now.get("ok") and now.get("state") == "running":
            return {"ok": True, "state": "running", "unit": REPLAN_UNIT,
                    "started_at": now.get("started_at") or ""}, 202
    return {"ok": True, "state": "started", "unit": REPLAN_UNIT}, 202


def idem_get(mid: str):
    """按 client_msg_id 取上次的结果（过期/没有 ⇒ None）。"""
    if not mid:
        return None
    try:
        with open(CHAT_IDEM_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    rec = (data or {}).get(mid) if isinstance(data, dict) else None
    if not isinstance(rec, dict):
        return None
    if time.time() - float(rec.get("ts") or 0) > CHAT_IDEM_TTL:
        return None
    payload = rec.get("payload")
    return payload if isinstance(payload, dict) else None


def idem_put(mid: str, payload: dict) -> None:
    """记下 client_msg_id → 结果。原子写（tmp + replace），失败不影响主流程。"""
    if not mid:
        return
    try:
        with open(CHAT_IDEM_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[mid] = {"ts": time.time(), "payload": payload}
    now = time.time()
    items = [(k, v) for k, v in data.items()
             if isinstance(v, dict) and now - float(v.get("ts") or 0) <= CHAT_IDEM_TTL]
    items.sort(key=lambda kv: -(float(kv[1].get("ts") or 0)))
    data = dict(items[:CHAT_IDEM_MAX])
    tmp = CHAT_IDEM_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, CHAT_IDEM_FILE)
    except OSError:
        pass

# ── 图片（用户 2026-10-20："纯文字版接入图片能力"）────────────────────────
# 屏幕把图按 base64 塞进 **text/plain 的 JSON 字符串**（仍不触发 CORS 预检）；
# 桥解码落盘，再把 DSH 的 `@路径` 引用拼到提示词最前面。
# 依据：DSH 把 **`@` 前缀当作"用户显式引用的路径"** ⇒ Agent 拿到就能用 read_image 打开。
# 于是课表截图 / 作业照片 / 通知截图都能直接丢进墙上的对话框。
CHAT_UPLOAD_DIR = os.path.expanduser("~/qyn/chat-uploads")
CHAT_MAX_IMAGES = 3
CHAT_MAX_IMAGE_BYTES = 8 * 1024 * 1024          # 单张上限 8MB
ALLOWED_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def save_chat_images(images) -> list:
    """把屏幕传来的图片落盘，返回**绝对路径**列表（给 `@路径` 用）。

    内容只信 base64 本身：文件名只用来自取后缀，不在白名单里就按 .png 存
    （避免用请求里的文件名拼出奇怪路径）。
    """
    import base64
    import time as _time
    if not isinstance(images, list):
        return []
    os.makedirs(CHAT_UPLOAD_DIR, exist_ok=True)
    saved = []
    for i, it in enumerate(images[:CHAT_MAX_IMAGES]):
        if not isinstance(it, dict):
            continue
        b64 = str(it.get("b64") or "")
        if not b64:
            continue
        ext = os.path.splitext(os.path.basename(str(it.get("name") or "")))[1].lower()
        if ext not in ALLOWED_IMAGE_EXT:
            ext = ".png"
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:                       # noqa: BLE001
            continue
        if not raw or len(raw) > CHAT_MAX_IMAGE_BYTES:
            continue
        path = os.path.join(CHAT_UPLOAD_DIR, f"{_time.strftime('%Y%m%d-%H%M%S')}-{i}{ext}")
        with open(path, "wb") as f:
            f.write(raw)
        saved.append(path)
    return saved


def _chat_session_id() -> str:
    try:
        with open(CHAT_SESSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_chat_session_id(sid: str) -> None:
    try:
        with open(CHAT_SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(sid)
    except OSError:
        pass


def agent_env() -> dict:
    """复刻 qyn-agent.service 的环境（含从 agent.env 读的凭据）。"""
    home = os.path.expanduser("~")
    env = dict(os.environ)
    env.update({
        "DSH_HOME": os.path.join(home, "qyn/.dsh"),
        "QYN_DSH_HOME": os.path.join(home, "qyn/.dsh"),
        "QYN_REPO_ROOT": os.path.join(home, "qyn/agent/current"),
        "QYN_DSH_CLI": os.path.join(home, "qyn/agent/cli/node_modules/.bin/dsh"),
        "PATH": os.path.join(home, "qyn/agent/node/bin") + ":/usr/local/bin:/usr/bin:/bin",
    })
    try:                                  # 凭据文件是 chmod 600 的 KEY=VALUE
        with open(os.path.join(home, "qyn/agent/agent.env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def run_agent_turn(text: str, images=None) -> str:
    """把用户的话交给 Agent（headless 一轮），返回它说的话。

    ★★ **会话复用**（2026-10-15 实测确认）：headless 应用的参数是 **`--session-id <id>`**
    （`--resume` 是 tui 的，headless 会报 `unknown option` ✗）。**同一个会话 ⇒ 墙上对话
    才有"多轮记忆"**。第一次没有 id 就先跑一轮，从 `--json` 的 `session` 事件里取 id 存下来，
    之后一直带它。会话是按**工作目录**分桶的，而 `current` 是软链 ⇒ 换 release 后旧 id 可能
    失效，那时自动清掉重来一次（自愈）。
    ★ 用 `--json` 而不是解析 stdout：事件里的 `final` 就是最终答复，比滤工具日志干净得多。
    """
    env = agent_env()
    sid = _chat_session_id()
    # ★★ 输入输出由**内核组件**决定（用户 2026-10-26）：
    #   Agent 只负责"读信息 → 产出回复文本"，**不需要知道也不许关心**信息从哪条通道来 ✓。
    #   三条入站通道（墙 chat / 微信 / 定时）的路由全在管道里确定性完成：
    #     · 本桥 = 墙通道 ⇒ 把 `final` 文本原样返回给聊天框（下面 return ✓）
    #     · tick.sh = 微信/定时通道 ⇒ 它自己把 `final` 用 clawbot 发出去 ✓
    #   ⇒ 这里**不写任何通道提示** ✗（靠 prompt 约束是不可靠的 ✗）。
    # ★ 图片：先落盘，再把 `@路径` 拼在提示词最前面（用户 2026-10-20）。
    #   用**独立变量** prompt，别改 text —— 下面会话失效时要用原始 text 重跑一轮，
    #   若直接改 text，重试会把 `@路径` 叠成两份 ✗。
    prompt = text
    paths = save_chat_images(images)
    if paths:
        prompt = "".join("@" + p + "\n" for p in paths) + "\n" + text
    args = [env["QYN_DSH_CLI"], "--profile", "qyn", "--json"]
    if sid:
        args += ["--session-id", sid]
    # ★ cwd 用 realpath：headless 的 assertAdoptable 要求 `header.cwd === cwd`，
    #   而会话头里记的是**解析后的 release 路径**（`current` 是软链）。不解析就采纳不了。
    cwd = os.path.realpath(env["QYN_REPO_ROOT"])
    with CHAT_LOCK:
        proc = subprocess.run(
            args, input=prompt, cwd=cwd, env=env,
            capture_output=True, text=True, timeout=AGENT_RUN_TIMEOUT,
        )
    final, new_sid = "", ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "session":
            # 实测形状：{"type":"session","sessionId":"session-…","cwd":"…"}
            got = e.get("sessionId") or e.get("session_id")
            if not got and isinstance(e.get("session"), dict):
                got = e["session"].get("id")
            if isinstance(got, str) and got:
                new_sid = got
        elif e.get("type") == "final":
            final = str(e.get("text") or final)
    if new_sid:
        _save_chat_session_id(new_sid)
    if not final:
        # 会话 id 失效（换 release / 被清理）⇒ 清掉重来一次，自愈
        if sid and proc.returncode != 0:
            _save_chat_session_id("")
            return run_agent_turn(text, images)
        # 兜底：非 JSON 输出时按老办法滤掉工具日志
        lines = [l for l in (proc.stdout or "").splitlines()
                 if l.strip() and not l.lstrip().startswith(("[", "{"))]
        final = "\n".join(lines).strip() or (proc.stderr or "").strip()[-600:] or "（Agent 没有输出）"
    return final


def run_kernel(kernel: str, *args: str) -> dict:
    """跑一条**只读**内核命令，返回解析后的 JSON。"""
    cmd = [sys.executable, kernel, *args]
    proc = subprocess.run(
        cmd, cwd=os.path.dirname(kernel) or ".",
        capture_output=True, text=True, timeout=KERNEL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise KernelError(
            f"内核退出码 {proc.returncode}: {(proc.stderr or '').strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise KernelError(f"内核输出不是 JSON: {e}") from e


class TimeSource:
    """带缓存的内核读取器（多线程下加锁，避免同一时刻重复拉起内核）。"""

    AGENT_TTL = 15.0      # 秒：与 CACHE_TTL 对齐 —— 原先 60 秒，加上页面 60 秒轮询，
                          # 规划改一次最坏要 ~2 分钟才上屏（用户 2026-10-17 报"墙上没更新"）。
                          # 它要跑 3 条内核命令（约 0.9 秒），15 秒一次 = 每分钟 4 次，可接受。

    def __init__(self, kernel: str, days: int) -> None:
        self.kernel = kernel
        self.days = days
        self._lock = threading.Lock()
        self._at = 0.0
        self._payload: dict = {}
        self._agent_at = 0.0
        self._agent: dict = {}

    def get(self, force: bool = False) -> dict:
        with self._lock:
            if not force and (time.time() - self._at) < CACHE_TTL and self._payload:
                return self._payload
            self._payload = self._build()
            self._at = time.time()
            return self._payload

    def _build(self) -> dict:
        out: dict = {
            "source": "time-agent",
            "kernel": self.kernel,
            "updated_at": time.time(),
            "ok": True,
        }
        try:
            out["sessions"] = run_kernel(self.kernel, "sessions", "--days", str(self.days))
            out["anomalies"] = run_kernel(self.kernel, "anomalies")
        except Exception as e:                      # noqa: BLE001 —— 任何失败都要变成可显示的状态
            out["ok"] = False
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    # ── AGENT 面板（规划）────────────────────────────────────────────────
    def get_agent(self, force: bool = False) -> dict:
        with self._lock:
            if not force and (time.time() - self._agent_at) < self.AGENT_TTL and self._agent:
                return self._agent
            self._agent = self._build_agent()
            self._agent_at = time.time()
            return self._agent

    def _build_agent(self) -> dict:
        """把内核的**规划**整理成大屏要的形状。

        ★ 主数据源是内核的 **`wall`**（大屏只读视图，2026-10-15 由 AGENT 侧新增）：
          它把规划快照的完整结构给出来，**每块带 `why`（为什么排这儿）/ `gist` / `urgency`**，
          而且 docstring 就是契约（只读零副作用；`why`/`gist` 缺省省略不写空串；`task` 不截断）。
          —— 比之前"从 `context.last_plan` 拼、再按任务名接 `gist`"稳得多：
          `context.last_plan` 是**给模型看的**（最多 12 块、且读回时丢 `why`）。
        ★ 规则与"规划新鲜度"仍从 `context` 取（`wall` 只给规划本身）：
          `planning_rules` 是"为什么这样排"的政策层，`stale`/`age_days` 用来提示规划过期。
        """
        out: dict = {"source": "time-agent", "updated_at": time.time(), "ok": True}
        try:
            w = run_kernel(self.kernel, "wall")
            ctx = run_kernel(self.kernel, "context")
            lp = ctx.get("last_plan") or {}
            # 余量（"还剩 15 小时"）只在 context.active[].why 里 —— 大屏要用它判"紧急且 12 小时内"。
            # 按任务名接上；接不上就留空，页面自然退回"白色"（不猜）。
            left_by_task = {a.get("task"): (a.get("why") or "")
                            for a in (ctx.get("active") or [])}
            slots = []
            for s in (w.get("slots") or []):
                slots.append({
                    "date": s.get("date"),
                    "time": s.get("time"),
                    "task": s.get("task"),
                    "why": s.get("why") or "",          # ★ 真理由：为什么排这儿
                    "gist": s.get("gist") or "",        # 详细任务（可执行要点）
                    "urgency": s.get("urgency") or "",
                    "left": left_by_task.get(s.get("task"), ""),   # 内核算的"还剩 X"
                    "passed": s.get("passed"),
                    # ★ DDL 与这块的关系（内核算的机械事实）：大屏据此标红/闪烁
                    "ddl": s.get("ddl"),
                    "ddl_in_minutes": s.get("ddl_in_minutes"),
                    "flags": s.get("flags") or [],
                })
            out["now"] = w.get("now") or {}
            out["plan"] = {
                "created_at": w.get("created_at") or lp.get("created_at"),
                "triggered_by": w.get("triggered_by") or lp.get("triggered_by"),
                "focus": w.get("focus") or lp.get("focus"),
                "horizon": w.get("horizon") or lp.get("horizon"),
                "order": w.get("order") or lp.get("order") or [],
                "stale": lp.get("stale"),
                "age_days": lp.get("age_days"),
                "slots": slots,
                # ★ 整份规划的把关结论（内核 `wall` 给的，与 brief 同口径）：
                #   ok=false ⇒ 面板整块标红；problems 逐条说明卡在哪。
                "ok": w.get("ok", True),
                "problems": w.get("problems") or [],
            }
            out["rules"] = [
                {"id": r.get("id"), "kind": r.get("kind"), "condition": r.get("condition"),
                 "body": r.get("body"), "source": r.get("source")}
                for r in (ctx.get("planning_rules") or [])
            ]
            # ★ 触发重排的判据（用户 2026-10-15 定的策略）：
            #   计划表**最上面那条**（最早的那个时段）如果已经**不在活跃注册表**里
            #   ⇒ 它已完成/已取消，而计划表还挂着它 ⇒ **该按需重排整个计划表**了。
            #   桥只**报告**这个事实；重排本身由 Agent 做（墙上给一个"按需重排"按钮，
            #   点了就把话交给 Agent —— 与对话同一条通路）。
            active_tasks = {a.get("task") for a in (ctx.get("active") or [])}
            top = slots[0] if slots else {}
            out["top_task"] = top.get("task") or ""
            out["top_done"] = bool(top.get("task")) and (top.get("task") not in active_tasks)
        except Exception as e:                      # noqa: BLE001
            out["ok"] = False
            out["error"] = f"{type(e).__name__}: {e}"
            return out
        # 软偏好警告（brief 的把关产物）与"有 DDL 却排不上"的任务：取不到就空着，不影响主体
        try:
            out["warnings"] = (run_kernel(self.kernel, "brief") or {}).get("warnings") or []
        except Exception:                           # noqa: BLE001
            out["warnings"] = []
        try:
            rv = run_kernel(self.kernel, "review") or {}
            out["unscheduled"] = [
                {"task": u.get("task"), "why": u.get("why")}
                for u in (rv.get("unscheduled") or [])
            ]
        except Exception:                           # noqa: BLE001
            out["unscheduled"] = []
        return out


def make_handler(src: TimeSource):
    class Handler(BaseHTTPRequestHandler):
        server_version = "qyn-time-panel/0.1"

        def _send(self, code: int, body: bytes, ctype: str, extra_headers=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # 屏幕壳在另一个端口（8090），必须放行 CORS
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, code: int = 200, extra_headers=None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8", extra_headers)

        def do_GET(self) -> None:                   # noqa: N802
            # ★ 这一行曾被一次"删换行"的编辑挤进上一行的注释里 ⇒ 每个请求都 NameError，
            #   而 ast.parse 查不出来（语法依然合法）。所以改完桥**必须真发一次请求**。
            path = urlparse(self.path).path
            if path == "/health":
                self._json({"ok": True, "kernel": src.kernel})
                return
            if path == "/api/time":
                qs = parse_qs(urlparse(self.path).query)
                force = qs.get("force", ["0"])[0] in ("1", "true")
                payload = src.get(force=force)
                payload["age_s"] = round(time.time() - src._at, 1)
                self._json(payload)
                return
            if path == "/api/replan":
                self._json(replan_status())
                return
            if path == "/api/agent":
                qs = parse_qs(urlparse(self.path).query)
                force = qs.get("force", ["0"])[0] in ("1", "true")
                payload = src.get_agent(force=force)
                payload["age_s"] = round(time.time() - src._agent_at, 1)
                self._json(payload)
                return
            self._json({"ok": False, "error": "not found"}, code=404)

        def do_POST(self) -> None:                  # noqa: N802
            """对话：把用户说的话交给 Agent，回它的话。

            ★ 请求体用 **text/plain**（不是 JSON）：这样浏览器不会发 CORS 预检请求
              （页面在 :8090、桥在 :8091，是跨源）。响应仍然是 JSON。
            """
            path = urlparse(self.path).path
            if path == "/api/replan":
                # ★ 一键重排（用户 2026-11-08）。请求体可以为空：这个入口没有参数 ——
                #   "重排什么"由 Agent 自己按注册表/课表/当前时间判断（桥不代替它想）✗。
                payload, code = replan_start()
                extra = {"Retry-After": str(GLOBAL_BUSY_RETRY_AFTER)} if payload.get("busy") else None
                print("[time-panel] 一键重排 → %s" % (payload,), file=sys.stderr)
                self._json(payload, code=code, extra_headers=extra)
                return
            if path != "/api/chat":
                self._json({"ok": False, "error": "not found"}, code=404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            text = raw.decode("utf-8", "replace").strip()
            images = []
            mid = ""            # client_msg_id（可选）：同一个 id 重复到达只跑一次
            # ★ 对话现在**总是** JSON 字符串（body 仍是 text/plain ⇒ 不触发 CORS 预检）；
            #   纯文字也走这条，才能带上 `client_msg_id` 做幂等 ✓（用户 2026-10-20）
            if text.startswith("{"):
                try:
                    obj = json.loads(text)
                except ValueError:
                    obj = None
                if isinstance(obj, dict):
                    images = obj.get("images") or []
                    mid = str(obj.get("client_msg_id") or "").strip()[:64]
                    text = str(obj.get("text") or "").strip()
            if not text and not images:
                self._json({"ok": False, "error": "空消息"}, code=400)
                return
            # ① 幂等：同一个 client_msg_id ⇒ 直接回上次的结果（**不重复跑 Agent** ✓）
            cached = idem_get(mid)
            if cached is not None:
                print(f"[time-panel] 对话 重复请求（client_msg_id={mid[:12]}…）⇒ 回上次结果",
                      file=sys.stderr)
                self._json(dict(cached, duplicate=True))
                return
            # ② 跨通道互斥：墙 / 手机 / 07:00 共用**同一把 flock**，谁先拿到谁跑；
            #    拿不到 ⇒ 立刻 429（**不排队、不自动重试** ✓）
            lockf = global_lock_acquire()
            if lockf is None:
                print("[time-panel] 对话 忙：另一轮还在跑 ⇒ 429（不排队、不自动重试）",
                      file=sys.stderr)
                self._json({"ok": False, "busy": True,
                            "error": "另一轮还在跑（墙/手机/定时共用同一把锁），稍后再发"},
                           code=429,
                           extra_headers={"Retry-After": str(GLOBAL_BUSY_RETRY_AFTER)})
                return
            try:
                print(f"[time-panel] 对话 → Agent: {text[:60]!r} 图片 {len(images)} 张",
                      file=sys.stderr)
                try:
                    reply = run_agent_turn(text, images)
                    payload = {"ok": True, "reply": reply}
                except subprocess.TimeoutExpired:
                    payload = {"ok": False, "error": "Agent 超时（15 分钟）"}
                except Exception as e:              # noqa: BLE001
                    payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            finally:
                try:
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                lockf.close()
            # ③ 结果（成功/失败都算）记进幂等表 ⇒ 同 id 再来直接回这一份 ✓
            idem_put(mid, payload)
            self._json(payload)

        def log_message(self, fmt: str, *args) -> None:
            print(f"[time-panel] {self.address_string()} {fmt % args}", file=sys.stderr)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="TIME AGENT 只读 HTTP 桥")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--kernel", default=DEFAULT_KERNEL)
    ap.add_argument("--days", type=int, default=3, help="取未来几天的课次/待办")
    args = ap.parse_args()

    kernel = os.path.expanduser(args.kernel)
    if not os.path.isfile(kernel):
        print(f"[time-panel] 找不到内核: {kernel}", file=sys.stderr)
        return 2

    src = TimeSource(kernel, args.days)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(src))
    print(f"[time-panel] {args.host}:{args.port}  内核={kernel}  天数={args.days}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[time-panel] 退出", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
