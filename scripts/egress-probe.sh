#!/usr/bin/env bash
# 出口探测：中控机到模型端点通不通。每 5 分钟一次，记日志 + 连续失败计数。
#
# 为什么要它：opencode.ai 是**间歇性**连不通的（实测同一分钟内 TCP TIMEOUT，
# 几分钟后 HTTP 200）。出口一断，Agent 那一轮直接 TRANSPORT 错误死掉 ——
# 而它自己发不出告警（模型就是断掉的那条路）。所以探测必须独立于模型。
#
# ★ 结果去哪（用户 2026-10-15 定的）：
#   **不发手机**。这类"我们自己的服务信息"放 SERVICE —— 即大屏状态墙的
#   `POST /api/telemetry`，`node = svc-egress`（和 `svc-opencode` 同一条通路、
#   同一个 `svc-` 前缀约定；大屏 SERVICE 面板从 SSE 流里读 sample.frame）。
#   手机通道只留给"用户自己的事"（作业、安排、要他决定的事），不塞系统噪声。
#
# ★ 判据（2026-10 加固）：以前只在「连续失败 == 3」时记那行，而真机的实际故障形态是
#   **单次失败**（13:05:36 / 14:28:06 / 16:29:06 三次都是"连续失败=1"），那行一次没出现过。
#   现在**首次失败就记**，连续 >= 3 次升级记录，之后每小时再提一次。
#
# 状态文件统一放 $AG/logs/（以前散在 $AG 根；检测到旧位置会自动搬）。
set -uo pipefail

AG="$HOME/qyn/agent"
NODE_BIN="$AG/node/bin/node"
PROBE="$AG/current/CENTRAL/scripts/probe-egress.cjs"
LOG="$AG/logs/egress.log"
STATE="$AG/logs/egress.state"       # 连续失败计数
LAST="$AG/logs/egress.last"         # 上一次结论
WALL_POST="${QYN_WALL_POST:-http://127.0.0.1:8080/api/telemetry}"
WALL_NODE="svc-egress"
PY="${QYN_PYTHON:-python3}"              # 服务类样本统一 svc- 前缀
HOST="${QYN_EGRESS_HOST:-opencode.ai}"
APATH="${QYN_EGRESS_PATH:-/zen/go/v1/models}"

mkdir -p "$AG/logs"
ts() { date '+%F %T'; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; }

# 旧位置（$AG 根）的状态文件搬进 logs/
for f in egress.state egress.last; do
    if [ -f "$AG/$f" ] && [ ! -e "$AG/logs/$f" ]; then
        mv -f "$AG/$f" "$AG/logs/$f" 2>/dev/null || true
    fi
done

[ -x "$NODE_BIN" ] || { log "node 不在 $NODE_BIN（探测没法跑）"; exit 1; }
[ -f "$PROBE" ] || { log "探测脚本不在 $PROBE（current 指向对吗？）"; exit 1; }

out="$("$NODE_BIN" "$PROBE" "$HOST" "$APATH" 2>&1)"
if printf '%s' "$out" | grep -q "HTTPS $HOST.*HTTP 200"; then ok=yes; else ok=no; fi

fails=0
[ -f "$STATE" ] && fails="$(cat "$STATE" 2>/dev/null || echo 0)"
case "$fails" in ''|*[!0-9]*) fails=0 ;; esac
if [ "$ok" = yes ]; then fails=0; else fails=$((fails + 1)); fi
printf '%s' "$fails" > "$STATE"

prev="$(cat "$LAST" 2>/dev/null || echo unknown)"
if [ "$ok" != "$prev" ] || [ $((fails % 12)) -eq 0 ]; then
    {
        printf '%s  出口=%s  连续失败=%s\n' "$(ts)" "$ok" "$fails"
        if [ "$ok" = no ]; then printf '%s\n' "$out" | sed 's/^/    /'; fi
    } >> "$LOG"
fi
printf '%s' "$ok" > "$LAST"

if [ "$ok" = no ]; then
    if [ "$fails" -eq 1 ]; then
        log "!! 出口断了（首次失败）：$HOST$APATH —— Agent 那一轮会 TRANSPORT 报错"
    elif [ "$fails" -ge 3 ] && { [ "$fails" -eq 3 ] || [ $((fails % 12)) -eq 0 ]; }; then
        log "!! 出口连续失败 $fails 次（约 $((fails * 5)) 分钟）—— 不是抖动，去看网络/中继"
    fi
fi

# ── 推到状态墙的 SERVICE（大屏读它）────────────────────────────────────────
# 帧形状：headline + items（大屏按 items 逐行渲染，不硬编码字段语义）。
# 推失败**不影响探测本身**：墙没起、网络不通都只是少一帧，下一轮再推。
post_wall() {
    # ★ 去抖（用户 2026-10-19）：连续 1~2 次失败只是**抖动**（黄 warn），≥3 次（约 15 分钟）才算真断（红）。
    #   依据：23:27 实测 —— TCP/TLS 全 TIMEOUT，但**对照 www.baidu.com 是 HTTP 200**
    #   ⇒ 内网没断，是 opencode 那条链短时抖动；一次 5 分钟的抖动把整块点红 ✗ 太吵。
    if [ "$ok" = yes ]; then
        level=ok; head="出口通"
    elif [ "$fails" -lt 3 ]; then
        level=warn; head="出口抖动（连续 $fails 次）"
    else
        level=critical; head="出口断（连续 $fails 次）"
    fi
    now_ms=$(( $(date +%s) * 1000 ))
    body=$(printf '{"node":"%s","frame":{"schema":"qyn.svc-egress/1","level":"%s","headline":"%s",' \
        "$WALL_NODE" "$level" "$head"
        printf '"generated_at_ms":%s,"fresh_for_ms":900000,' "$now_ms"
        printf '"items":[{"key":"出口","value":"%s","level":"%s"},' "$ok" "$level"
        printf '{"key":"连续失败","value":"%s"},' "$fails"
        printf '{"key":"端点","value":"%s%s"},' "$HOST" "$APATH"
        printf '{"key":"检查时刻","value":"%s"}]}}' "$(ts)")
    # ★ 不用 curl：中控机上**没有 curl**（实测「（没有 curl，推不了 SERVICE）」⇒ 一直没上墙）。
    #   用 python3（机器上一定有），帧先落文件再 POST，避免在命令行里拼 JSON。
    frame_file="$AG/logs/egress.frame.json"
    printf '%s' "$body" > "$frame_file"
    code=$("$PY" -c 'import json,sys,urllib.request as u
d=json.load(open(sys.argv[2],encoding="utf-8"))
r=u.Request(sys.argv[1],data=json.dumps(d,ensure_ascii=False).encode("utf-8"),method="POST",
            headers={"Content-Type":"application/json; charset=utf-8"})
print(u.urlopen(r,timeout=15).status)' "$WALL_POST" "$frame_file" 2>/dev/null || echo 000)
    case "$code" in
        2*) : ;;
        *)  log "  （推 SERVICE 失败：${code}，不影响探测；下一轮再推）" ;;
    esac
}
post_wall

echo "egress=$ok fails=$fails"
[ "$ok" = yes ] && exit 0 || exit 1
