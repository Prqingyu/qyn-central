#!/usr/bin/env python3
"""OpenCode GO 额度的**降级数据源**（中控机侧，纯 API Key，不需要 Cookie）。

为什么需要它：主源是 Windows 上的 `CENTRAL/scripts/qyn-opencode-post.py`（有 Cookie 能力），
Windows 关机/注销时主源就断了 —— 本脚本用**账号级 Key** 顶上同一份契约的一帧。

★★ 2026-10-22 修：以前它用自己那份 10-15 的 apiusage 快照 + 自己 env 里的旧 key，
   **完全不跟账号轮切** ✗ ⇒ 用户看到屏上写 `（按 Key 直查）`、数字还是另一个账号的 ✗。
   现在：
     · 用**真 apiusage**（`~/qyn/opencode-fallback/pkg`，从仓库 `OPENCODEGO/apiusage` 同步）
     · 账号/Key **跟中控机的轮切档案** `~/qyn/agent/ocgo-accounts.env` 走（同一个 anchor ✓）
     · 和主源一样跑**两次**：当前生效账号 + 另一个账号（仅展示月额度 ✓）
     · 工作区名一律写成**账号标签**（`WS02` / `WS01`），绝不留 `（按 Key 直查）` ✗
     · 帧上带 `fallback: true`（屏上 RES 行据此显示 `TOK` ✓）

铁律：**绝不跟主源打架** —— 只在墙上的 svc-opencode 已经陈旧时才推。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

WALL = "http://127.0.0.1:8080"
NODE = "svc-opencode"
STALE_AFTER = 300                      # 主源超过这么久没更新才顶上（主源 60 秒一帧）
HOME = os.path.expanduser("~")
ACCF = os.path.join(HOME, "qyn/agent/ocgo-accounts.env")
PKG_DIR = os.path.join(HOME, "qyn/opencode-fallback/pkg")     # 里面放真 apiusage 包
FRAME_TIMEOUT = 150


def log(msg: str) -> None:
    try:
        with open(os.path.join(HOME, "qyn/agent/logs/ocgo-fallback.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")
    except OSError:
        pass


def read_accounts():
    """读轮切的账号档案（KEY/LABEL/anchor/period）；读不到返回 None ✓。"""
    cfg = {}
    try:
        with open(ACCF, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except OSError:
        return None
    keys = []
    for i in (1, 2):
        key = cfg.get(f"OCGO_KEY_{i}") or ""
        if key:
            keys.append({"id": i - 1, "label": cfg.get(f"OCGO_LABEL_{i}") or f"WS0{i}",
                         "key": key})
    if not keys:
        return None
    return {
        "anchor": cfg.get("OCGO_ROTATE_ANCHOR") or "",
        "period": int(cfg.get("OCGO_ROTATE_PERIOD_DAYS") or 15),
        "accounts": keys,
    }


def active_account(cfg):
    """与 Windows / 中控脚本**同一个公式** ✓。"""
    from datetime import date, datetime
    try:
        anchor = datetime.strptime(cfg["anchor"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        anchor = date.today()
    days = max(0, (date.today() - anchor).days)
    idx = (days // cfg["period"]) % len(cfg["accounts"])
    return cfg["accounts"][idx]


def pick_other(cfg, acct):
    for a in cfg["accounts"]:
        if a is not acct and a.get("key"):
            return a
    return None


def fetch_frame(key: str):
    env = dict(os.environ)
    env["OPENCODE_GO_API_KEY"] = key
    env["APIUSAGE_GO_COOKIE"] = os.path.join(HOME, ".apiusage-disabled.cookie")  # 不存在 ⇒ 纯 Key
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run([sys.executable, "-m", "apiusage", "panel"], cwd=PKG_DIR,
                              env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=FRAME_TIMEOUT)
    except Exception as exc:                       # noqa: BLE001
        log(f"取数失败：{type(exc).__name__}: {exc}")
        return None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def wall_sample():
    try:
        with urllib.request.urlopen(WALL + "/api/state", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:                               # noqa: BLE001
        return None
    for n in data.get("nodes", []):
        if n.get("node") == NODE:
            return n
    return None


def push(frame: dict) -> int:
    frame["fallback"] = True          # 标记来源（屏上 RES 显示 TOK ✓）
    body = json.dumps({"node": NODE, "frame": frame}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(WALL + "/api/telemetry", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def annotate(frame: dict, label: str, in_use: bool) -> None:
    for w in frame.get("workspaces") or []:
        nm = str(w.get("name") or "").strip()
        if not nm or "按 Key 直查" in nm or nm.lower() in ("key", "(key)"):
            w["name"] = label
        w["in_use"] = in_use


def main(argv) -> int:
    force = "--force" in argv
    cfg = read_accounts()
    if cfg is None:
        log("缺账号档案，无法跟随轮切")
        return 1
    if not force:
        s = wall_sample()
        if s is not None:
            age = s.get("stale_seconds")
            if isinstance(age, (int, float)) and age < STALE_AFTER:
                return 0                    # 主源还活着 ⇒ 什么都不做 ✓
    acct = active_account(cfg)
    frame = fetch_frame(acct["key"])
    if not frame or frame.get("schema") != "apiusage.panel/1":
        log(f"当前账号 {acct['label']} 取不到帧")
        return 1
    # ★★ 额度不足就换另一个当"当前"（2026-11-05，和主源**同一条确定性规则** ✓）：
    #   轮切目标是"本月已用尽"的账号 ⇒ 卡片会变红、而 Agent 其实在用另一个号 ⇒ 像卡死 ✗
    swapped = None
    if str(frame.get("level") or "") == "critical":
        alt = pick_other(cfg, acct)
        if alt is not None:
            alt_frame = fetch_frame(alt["key"])
            if (alt_frame and alt_frame.get("schema") == "apiusage.panel/1"
                    and str(alt_frame.get("level") or "") != "critical"
                    and alt_frame.get("workspaces")):
                log(f"账号 {acct['label']} 额度不足（critical）⇒ 当前改用 {alt['label']}")
                swapped, acct, frame = acct, alt, alt_frame
    annotate(frame, acct["label"], True)
    other = swapped or pick_other(cfg, acct)
    if other is not None:
        sec = fetch_frame(other["key"])
        if sec and sec.get("schema") == "apiusage.panel/1" and sec.get("workspaces"):
            annotate(sec, other["label"], False)
            frame["workspaces"] = list(frame.get("workspaces") or []) + list(sec["workspaces"])
    try:
        code = push(frame)
    except Exception as exc:                        # noqa: BLE001
        log(f"推送失败：{type(exc).__name__}: {exc}")
        return 1
    log(f"已顶一帧 {code} 账号={acct['label']}" + (f" +{other['label']}" if other else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
