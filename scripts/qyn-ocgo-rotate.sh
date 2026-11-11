#!/usr/bin/env bash
# ── OpenCode GO 账号轮切（用户 2026-10-21："固定 15 天切换"）──────────────────
# 前提：**一个账号只能有一个 GO 订阅** ⇒ 两个账号必须轮着用 ✓
# 本脚本负责**中控机侧**：把"当前应生效账号"的 KEY 写进 `agent.env` 的
#   OPENCODE_GO_API_KEY= 行。Agent（tick / 聊天）每次运行都现读 agent.env
#   （桥的 agent_env() 也是每轮现读 ✓）⇒ **不需要重启任何服务** ✓。
#
# ★ 与 Windows 侧 `CENTRAL/scripts/qyn-opencode-post.py`（换 Cookie）**共用同一个
#   anchor 和 15 天周期** ✓ —— 两边同时生效，不会一边新账号一边旧账号。
# ★ 凭据只存在 `$AG/ocgo-accounts.env`（600 ✓，机器私有 ✓），不进仓库 ✓。
#
# 用法：
#   qyn-ocgo-rotate.sh             # 按当前日期对齐（幂等：已经是这个账号就什么都不做）
#   qyn-ocgo-rotate.sh --dry-run   # 只打印"现在应该用哪个账号"，不改任何文件
set -uo pipefail

AG="${QYN_AGENT_HOME:-$HOME/qyn/agent}"
ENVF="$AG/agent.env"
ACCF="$AG/ocgo-accounts.env"
LOG="$AG/logs/ocgo-rotate.log"
mkdir -p "$AG/logs"

[ -f "$ACCF" ] || { echo "缺账号档案：$ACCF" >&2; exit 1; }
[ -f "$ENVF" ] || { echo "缺 $ENVF" >&2; exit 1; }
set -a; . "$ACCF"; set +a

DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

anchor="${OCGO_ROTATE_ANCHOR:?档案里缺 OCGO_ROTATE_ANCHOR}"
period="${OCGO_ROTATE_PERIOD_DAYS:-15}"
# ★★ 2026-11-05 修：这里原来用 `date -u`（UTC 基准）✗，而 Windows 推送 / 降级源用的是
#   **本地日期**（Python `date.today()`）⇒ 每天本地 00:00–08:00（UTC 还是前一天）
#   墙显示的是**下一个**账号、Agent 还在用**上一个** ⇒ 墙 OC 卡变红"本月已用尽"✗
#   （用户 2026-11-05 报"OC 卡死" ✓ 就是这个）。
#   ⇒ 三处统一用**本地时间** ✓（用户所在时区才是"第几天"的口径 ✓）。
now_s=$(date +%s)
anc_s=$(date -d "$anchor" +%s 2>/dev/null) || { echo "anchor 解析失败：$anchor" >&2; exit 1; }
days=$(( (now_s - anc_s) / 86400 )); [ "$days" -lt 0 ] && days=0
idx=$(( (days / period) % 2 ))

if [ "$idx" -eq 0 ]; then
    key="${OCGO_KEY_1:-}"; label="${OCGO_LABEL_1:-账号1}"
else
    key="${OCGO_KEY_2:-}"; label="${OCGO_LABEL_2:-账号2}"
fi
[ -n "$key" ] || { echo "账号 $idx 没有配 KEY（$ACCF）" >&2; exit 1; }
# ★ 防御：档案若是 CRLF（实测 2026-11-05 有一行是 ✗），key 尾部会带 \r
#   ⇒ HTTP 头变成 `Bearer oc_sk_…\r` ⇒ apiusage 报 Invalid header value ⇒ 守卫失效 ✗
key="${key%$'\r'}"; label="${label%$'\r'}"

cur="$(grep -E '^OPENCODE_GO_API_KEY=' "$ENVF" | head -1 | cut -d= -f2- | tr -d '\r')"
if [ "$cur" = "$key" ]; then
    echo "第 ${days} 天 · 账号 ${idx}（${label}）已经是生效账号 ✓（无需切换）"
    exit 0
fi

# ★★ 目标账号**额度不足就不切**（确定性规则，2026-11-05）：
#   轮切到"本月已用尽"的账号 ⇒ Agent 立刻跑不动 ✗（实测 WS01 月额度 60/60 ✗）。
#   判据只用 apiusage 的 `level`（那边算好了套娃 gate ✓），不自己解释额度。
PKG="$HOME/qyn/opencode-fallback/pkg"
if [ -d "$PKG" ]; then
    lv="$(OPENCODE_GO_API_KEY="$key" APIUSAGE_GO_COOKIE="$HOME/.apiusage-disabled.cookie" \
          PYTHONIOENCODING=utf-8 python3 -c "
import json, subprocess, sys
p = subprocess.run([sys.executable, '-m', 'apiusage', 'panel'], cwd='$PKG',
                   capture_output=True, text=True, timeout=120)
for line in (p.stdout or '').splitlines():
    if line.strip().startswith('{'):
        print(json.loads(line).get('level') or ''); break
" 2>/dev/null)"
    if [ "$lv" = "critical" ]; then
        echo "第 ${days} 天 ⇒ 目标账号 ${idx}（${label}）**额度不足**（level=critical）⇒ 保持当前账号 ✓"
        echo "$(date '+%F %T')  第 ${days} 天 ⇒ 目标账号 ${idx}（${label}）level=critical ⇒ 跳过切换（保持当前）" >> "$LOG"
        exit 0
    fi
fi

# ★★ 2026-11-08（用户 2026-11-08「有信息入站但是没有信息出站」）：
#   **额度够 ≠ 订阅有效**。WS01 的 level 检查是过的、但订阅失效 ⇒ 切过去之后
#   每一轮模型调用都在第一步 403(HttpError AUTH)，收件箱进得来、微信一条都出不去，
#   整整两天没人发现。所以切之前**用一次真请求探活**，401/403 就不切。
#   （curl 不可用时探活返回 000 ⇒ 视为未知，按原逻辑继续 —— 不因为探活工具缺失卡住轮切）
probe_code() {
    # 真机没装 curl（实测），用 python3；★ 必须用 /chat/completions：
    #   /models 对失效订阅也回 200 ⇒ 探不出来 ✗；★ 必须带 User-Agent，否则 Cloudflare 1010 ✗。
    python3 - "$1" <<'PY' 2>/dev/null || echo 000
import json, sys, urllib.error, urllib.request
key = sys.argv[1]
req = urllib.request.Request(
    "https://opencode.ai/zen/go/v1/chat/completions",
    data=json.dumps({"model": "deepseek-v4.1-flash",
                     "messages": [{"role": "user", "content": "hi"}],
                     "max_tokens": 1}).encode(),
    headers={"Authorization": "Bearer " + key,
             "Content-Type": "application/json",
             "User-Agent": "dsh/1.0",
             "x-opencode-session": "qyn-projectqyn"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("000")
PY
}
pcode="$(probe_code "$key")"
if [ "$pcode" = "401" ] || [ "$pcode" = "403" ]; then
    echo "第 ${days} 天 ⇒ 目标账号 ${idx}（${label}）**探活失败**（HTTP $pcode，订阅无效）⇒ 保持当前账号 ✓"
    echo "$(date '+%F %T')  第 ${days} 天 ⇒ 目标账号 ${idx}（${label}）probe HTTP $pcode ⇒ 跳过切换（保持当前）" >> "$LOG"
    exit 0
fi

if [ "$DRY" -eq 1 ]; then
    echo "DRY-RUN：第 ${days} 天 ⇒ 应切到账号 ${idx}（${label}）  key=${key:0:12}…  （目标额度 level=${lv:-未知}，探活 HTTP ${pcode:-000}）"
    exit 0
fi

cp -p "$ENVF" "$ENVF.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$ENVF" "$key" <<'PY'
import re, sys
p, key = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
if re.search(r"^OPENCODE_GO_API_KEY=.*$", s, re.M):
    s = re.sub(r"^OPENCODE_GO_API_KEY=.*$", "OPENCODE_GO_API_KEY=" + key, s, flags=re.M)
else:
    s = s.rstrip("\n") + "\nOPENCODE_GO_API_KEY=" + key + "\n"
open(p, "w", encoding="utf-8").write(s)
PY
chmod 600 "$ENVF"
echo "$(date '+%F %T')  第 ${days} 天 ⇒ 切到账号 ${idx}（${label}） key=${key:0:12}…" >> "$LOG"
echo "已切到账号 ${idx}（${label}）· 第 ${days} 天（agent.env 已更新，Agent 下一次运行即生效 ✓）"
