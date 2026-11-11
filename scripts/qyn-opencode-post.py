#!/usr/bin/env python3
"""把 OpenCode 余额（`apiusage panel` 的一帧）推到大屏状态墙。

为什么需要它：`apiusage` 只能在**这台 Windows** 上跑（OpenCode 凭据在这里），
而大屏在中控机 Linux 上 —— 两边靠 **Windows 主动 POST** 打通，和 win-dev / mc 的
采集器走**同一条通路**（`POST /api/telemetry`）。

用法（挂开机自启，用 pythonw 跑 ⇒ 不弹黑框）：

    pythonw CENTRAL\\scripts\\qyn-opencode-post.py              # 常驻，每 60 秒一帧
    pythonw CENTRAL\\scripts\\qyn-opencode-post.py --once       # 只推一帧（调试用）
    pythonw CENTRAL\\scripts\\qyn-opencode-post.py --interval 30

设计要点：
* **必须用 pythonw 跑**：python.exe 是控制台程序，每轮都会闪一个黑框
  （2026-10-15 修 `QYN-ApiUsage-*` 计划任务时踩过这个坑）。
* 取数仍用 python.exe（要看 stdout）—— 由本进程 subprocess 起，窗口继承隐藏的控制台。
* 单帧 1.5–3 秒，60 秒一帧足够（契约也建议 30–60 秒，额度本来就是 5 分钟粒度）。
* 推失败**不退出**：墙重启、断网都自愈，下一轮再推。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

# ★ Windows 控制台默认是 GBK ⇒ 打印中文/`✓` 会 UnicodeEncodeError 直接把推送打断 ✗
#   （实测：并入第二个账号后崩在 verbose 的那句 print 上，明明数据已经取到了）
#   统一改成 UTF-8 + 不可编码字符替换，保证"打印"永远不会弄死"推送"✓
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

APIC_DIR = r"D:\path\to\repo"
WALL_POST = "http://127.0.0.1:8080/api/telemetry"
NODE = "svc-opencode"          # 服务类样本用 svc- 前缀，和机器节点（central/mc/win-dev）区分开
FRAME_TIMEOUT = 120            # 单帧取数超时（秒）
# ★★ Windows：spawn 子进程必须带 CREATE_NO_WINDOW。
#   2026-10-15 实测踩坑：本进程用 pythonw 跑（自己没有窗口 ✓），但取数要 spawn `python.exe`
#   —— 那是**控制台程序** ⇒ 每帧都会弹一个黑框 ✗（实测每 64 秒一次）。
#   加了这个标志才是真的全程无窗口（同时仍能拿到 stdout）。
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _python_exe() -> str:
    """取数要用 python.exe（要看 stdout）；本脚本若是 pythonw 起的，得换回 python。"""
    exe = sys.executable or "python.exe"
    if exe.lower().endswith("pythonw.exe"):
        return exe[:-len("pythonw.exe")] + "python.exe"
    return exe


# ★★ 账号轮切（用户 2026-10-21："固定 15 天切换"）──────────────────────────────
#  前提：**一个账号只能有一个 GO** ⇒ 两个账号必须轮着用 ✓
#  做法：本机私有档案 `~/.apiusage/accounts.json`（含两套 Cookie，**明文不进仓库** ✓）
#        + 同一个 anchor 日期；当前账号 = accounts[(距 anchor 天数 // 15) % 账号数]
#        ⇒ **确定性计算，不需要定时器** ✓（推送本身每 60 秒跑一次，顺手把 Cookie 换成当前账号的）
#  与中控机侧（`CENTRAL/scripts/qyn-ocgo-rotate.sh` 换 Agent 的 Key）**用同一个 anchor** ✓
ACCOUNTS_FILE = os.path.expanduser("~/.apiusage/accounts.json")
COOKIE_FILE = os.path.expanduser("~/.apiusage/opencode_go.cookie")
ROTATE_LOG = os.path.expanduser("~/.apiusage/rotate.log")
POST_LOG = os.path.expanduser("~/.apiusage/post.log")


def _log(msg: str) -> None:
    """常驻进程的运行日志（pythonw 没控制台 ⇒ 必须落盘，否则出问题查不到 ✗）。"""
    try:
        with open(POST_LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")
    except OSError:
        pass


def _log_rotate(msg: str) -> None:
    try:
        with open(ROTATE_LOG, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")
    except OSError:
        pass


def load_accounts():
    """读轮切档案；没有/格式不对 ⇒ None（退回单账号行为 ✓，不影响推送）。"""
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict):
        return None
    accts = cfg.get("accounts")
    if not isinstance(accts, list) or not accts:
        return None
    return cfg


def active_account(cfg: dict) -> dict:
    """按时钟算"现在该用哪个账号"（确定性 ✓，和机器是否开机无关 ✓）。"""
    from datetime import date, datetime
    period = int(cfg.get("period_days") or 15)
    accts = cfg["accounts"]
    try:
        anchor = datetime.strptime(str(cfg.get("anchor") or ""), "%Y-%m-%d").date()
    except ValueError:
        anchor = date.today()
    days = max(0, (date.today() - anchor).days)
    return accts[(days // period) % len(accts)]


def apply_rotation(verbose: bool = False):
    """把当前账号的 Cookie 写进 apiusage 的凭据文件 —— **只在真的变了才写** ✓。"""
    cfg = load_accounts()
    if not cfg:
        return None
    acct = active_account(cfg)
    want = str(acct.get("cookie") or "").strip()
    if not want:
        return acct
    try:
        with open(COOKIE_FILE, encoding="utf-8") as fh:
            cur = fh.read().strip()
    except OSError:
        cur = ""
    if cur == want:
        return acct
    try:
        if cur:
            with open(COOKIE_FILE + ".bak-" + time.strftime("%Y%m%d-%H%M%S"),
                      "w", encoding="utf-8") as fh:
                fh.write(cur)
        with open(COOKIE_FILE, "w", encoding="utf-8") as fh:
            fh.write(want)
        _log_rotate(f"切到账号 {acct.get('id')} · {acct.get('label')}")
        if verbose:
            print(f"轮切：切到账号 {acct.get('id')} · {acct.get('label')}")
    except OSError as exc:
        _log_rotate(f"写 cookie 失败：{exc}")
    return acct


def probe_env(acct) -> dict:
    """给 `apiusage panel` 的环境：**用当前账号的 Key**，不吃 Cookie ✓
    （用户 2026-10-21："所以基于 cookie 的就必须手动不断更新？" ⇒ 不必 ✓）
    理由：**一个账号只有一个 GO** ⇒ 工作区维度本来就无意义 ✓；
    Key 是账号级、**不会过期** ✓ ⇒ 15 天轮切只换 Key 就够了 ✓，永不手动刷新 ✓。
    · `OPENCODE_GO_API_KEY` 优先级最高（credentials.py 里 env 覆盖 profile ✓）
    · `APIUSAGE_GO_COOKIE` 指到一个**不存在的路径** ⇒ 连 401 都不会去试 ✓（旧的 cookie 文件留着备用）
    """
    env = dict(os.environ)
    env["APIUSAGE_GO_COOKIE"] = os.path.expanduser("~/.apiusage/opencode_go.cookie.disabled")
    key = str((acct or {}).get("key") or "").strip()
    if key:
        env["OPENCODE_GO_API_KEY"] = key
    return env


def one_frame(env=None) -> dict | None:
    """跑一次 `apiusage panel`，返回那一帧 JSON（失败返回 None）。

    `env` 用来传**当前账号的 Key**（见 `probe_env()`）⇒ 纯 Key 模式 ✓ 不依赖 Cookie ✓
    """
    proc = subprocess.run(
        [_python_exe(), "-m", "apiusage", "panel"],
        cwd=APIC_DIR, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=FRAME_TIMEOUT,
        creationflags=_NO_WINDOW, env=env,
    )
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def push(frame: dict) -> int:
    """把一帧推给状态墙，返回 HTTP 状态码。"""
    body = json.dumps({"node": NODE, "frame": frame}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WALL_POST, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def pick_other(cfg, acct):
    """轮切档案里**另一个**（当前没在用、但配了 key）的账号；没有就 None ✓。"""
    if not cfg or acct is None:
        return None
    aid = str(acct.get("id") or "")
    for a in cfg.get("accounts") or []:
        if str(a.get("id") or "") == aid:
            continue
        if str(a.get("key") or "").strip():
            return a
    return None


def fill_name(ws: dict, label: str) -> None:
    """Key 模式下 apiusage 给的是占位名 ⇒ 用账号标签盖掉 ✓（只在没真名时）。"""
    if not label:
        return
    nm = str(ws.get("name") or "").strip()
    if not nm or nm in ("（按 Key 直查）", "(key)", "key"):
        ws["name"] = label


def run_once(verbose: bool) -> bool:
    # ★ 先轮切：选出"固定 15 天一轮"的当前账号 ✓
    cfg = load_accounts()
    acct = active_account(cfg) if cfg else None
    if acct is not None:
        apply_rotation(verbose)     # 顺手把 cookie 文件也保持在这个账号（留着备用 ✓）
    try:
        frame = one_frame(probe_env(acct))          # ★ 纯 Key 模式（Key 不过期 ✓ 不用维护 Cookie）
    except Exception as exc:                       # noqa: BLE001
        if verbose:
            print(f"取数失败：{type(exc).__name__}: {exc}")
        return False
    if not frame:
        if verbose:
            print("取数失败：没有拿到 JSON 帧")
        return False
    if frame.get("schema") != "apiusage.panel/1":
        if verbose:
            print(f"契约版本不匹配（{frame.get('schema')!r}）—— 跳过，等对方升级")
        return False
    # ★★ 额度不足就不当"当前账号"（2026-11-05 用户报"OC 卡死"✗）：
    #   轮切到"本月已用尽"的账号 ⇒ 卡片变红、而 Agent 其实还在用另一个号 ⇒ 看着像卡死 ✓。
    #   规则（确定性）：轮切目标的 `level == critical` ⇒ 换另一个健康账号顶上当"当前"✓，
    #   目标账号仍作为"备选"展示（它的月额度一目了然 ✓）。
    swapped = None
    if acct is not None and str(frame.get("level") or "") == "critical":
        alt = pick_other(cfg, acct)
        if alt is not None:
            try:
                alt_frame = one_frame(probe_env(alt))
            except Exception:                      # noqa: BLE001
                alt_frame = None
            if (alt_frame and alt_frame.get("schema") == "apiusage.panel/1"
                    and str(alt_frame.get("level") or "") != "critical"
                    and alt_frame.get("workspaces")):
                _log(f"账号 {acct.get('label')} 额度不足（critical）⇒ 当前账号改用 {alt.get('label')}")
                if verbose:
                    print(f"轮切目标 {acct.get('label')} 额度不足 ⇒ 当前账号改用 {alt.get('label')}（备选仍展示）")
                swapped, acct = acct, alt
                frame = alt_frame
    # ★ Key 模式下帧里**没有真工作区名**（账号级，不分工作区）——
    #   apiusage 会给个占位名 `（按 Key 直查）` ✗ ⇒ 用**账号标签**盖掉 ✓
    if acct is not None:
        for w in (frame.get("workspaces") or []):
            fill_name(w, str(acct.get("label") or acct.get("id") or ""))
            w["in_use"] = True          # ★ 打标记：这是"当前正在用"的账号（屏上优先显示它 ✓）
    # ★★ 另一个账号（用户 2026-10-21）：再跑一次 apiusage（换它的 key），
    #   把它的工作区**追加**进同一帧 ⇒ 屏上展开时能显示"不被使用的账号"的月额度 + 月刷新 ✓
    #   取不到就只推主账号（不报错 ✓ 屏上 WS2 行自然不出现 ✓）。
    other = swapped or pick_other(cfg, acct)
    if other is not None:
        try:
            sec = one_frame(probe_env(other))
        except Exception as exc:                   # noqa: BLE001
            sec = None
            if verbose:
                print(f"另一账号取数失败（只推主账号）：{type(exc).__name__}: {exc}")
        if sec and sec.get("schema") == "apiusage.panel/1" and sec.get("workspaces"):
            olabel = str(other.get("label") or other.get("id") or "")
            extra = []
            for w in sec["workspaces"]:
                fill_name(w, olabel)
                w["in_use"] = False
                extra.append(w)
            frame["workspaces"] = list(frame.get("workspaces") or []) + extra
            if verbose:
                print(f"已并入另一账号「{olabel}」的 {len(extra)} 个工作区（仅用于展示 ✓）")
        elif verbose:
            print("另一账号没有可用的工作区数据（只推主账号）")
    try:
        code = push(frame)
    except Exception as exc:                       # noqa: BLE001
        if verbose:
            print(f"推送失败：{type(exc).__name__}: {exc}")
        return False
    if verbose:
        print(f"已推送 {code}  level={frame.get('level')}  {frame.get('headline')}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="把 OpenCode 余额推到大屏状态墙")
    ap.add_argument("--once", action="store_true", help="只推一帧就退出")
    ap.add_argument("--interval", type=int, default=60, help="常驻时的间隔秒数（默认 60）")
    ap.add_argument("--verbose", action="store_true", help="打印每一轮结果（pythonw 下看不到）")
    args = ap.parse_args()

    if args.once:
        return 0 if run_once(True) else 1
    # ★ 常驻模式下**任何异常都不许静默退出**（2026-10-22 实测：进程某次死掉，
    #   pythonw 没控制台 ⇒ 完全查不到原因 ✗）。写日志到 ~/.apiusage/post.log。
    while True:
        try:
            run_once(args.verbose)
        except Exception as exc:                   # noqa: BLE001
            _log(f"轮次异常：{type(exc).__name__}: {exc}")
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
