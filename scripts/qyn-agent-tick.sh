#!/usr/bin/env bash
# 跟着规划走的"到点唤醒"驱动器 + 微信来消息即唤醒。
#
#     qyn-agent-tick.sh morning     # 每天锚点（07:00）：跑一轮，然后安排下一个边界
#     qyn-agent-tick.sh afternoon   # 午报（14:00）：同样的规划，但带上"下午/晚上还有哪几节课"
#     qyn-agent-tick.sh replan      # 大屏「一键重排」按钮：重排 + 更屏，不发微信
#     qyn-agent-tick.sh boundary    # 到点被唤醒（transient timer 调的）：跑一轮，再安排下一个
#     qyn-agent-tick.sh guard       # 每 30 分钟的守卫：边界没到就什么都不做（不调模型）
#     qyn-agent-tick.sh message     # 手机来消息（systemd path 单元调的）：读收件箱 → 跑一轮 → 推进游标
#
# 为什么不用固定定时器（用户 2026-10-15）：
#   "不建议单纯使用定时器，而是建议用跟随系统规划的下一时间点 —— 现在是三点，
#    PLAN 的下一项是 4-5 点做一个作业，那么到了 5 点就自动触发更新。"
#   所以唤醒时刻由内核的 `next-tick`（读规划快照算出来的）决定，本脚本负责排下一次。
#
# 到点那一轮的语义（用户定：默认用户没做完）写在下面的提示词里；内核只管"什么时候叫醒"。
set -uo pipefail

MODE="${1:-boundary}"
AG="$HOME/qyn/agent"
LOG="$AG/logs/tick.log"
REPO="$AG/current"
PY="${QYN_PYTHON:-python3}"
CURSOR_FILE="$AG/logs/inbox.cursor"
CLAWBOT="${QYN_CLAWBOT:-$AG/bin/clawbot}"
SENT_FILE="$AG/phone-state/sent.jsonl"          # 出站记录（clawbot send 每发一条写一行）
# ★★ 待补发（2026-11-11 事故：早报发失败后**永远丢了**）──────────────────────
#   微信通道会瞬时失败（实测 DNS："Temporary failure in name resolution" ⇒ prepare failed）。
#   失败就把正文存这里，**每轮开头先补发**（守卫每 31 分钟跑一次 ⇒ 天然的重试器）。
PENDING_PUSH="$AG/logs/pending-push.txt"
flush_pending_push() {
    [ -s "$PENDING_PUSH" ] || return 0
    local bytes
    bytes="$(wc -c < "$PENDING_PUSH" 2>/dev/null || echo 0)"
    if [ "${QYN_TICK_DRY:-0}" = "1" ]; then
        say "★ DRY：有待补发的消息（$bytes 字节）"
        return 0
    fi
    if "$CLAWBOT" send --text - < "$PENDING_PUSH" >>"$LOG" 2>&1; then
        say "✓ 补发成功（$bytes 字节）"
        rm -f "$PENDING_PUSH"
    else
        say "✗ 补发仍失败（$bytes 字节，留着下轮再试）"
    fi
}

# 单元里的环境在 transient timer / path 单元里不继承 ⇒ 这里自己兜住
export DSH_HOME="$HOME/qyn/.dsh"
export QYN_DSH_HOME="$HOME/qyn/.dsh"
export QYN_REPO_ROOT="$REPO"
export QYN_DSH_CLI="${QYN_DSH_CLI:-$AG/cli/node_modules/.bin/dsh}"
export PATH="$AG/node/bin:$AG/bin:$PATH"
# ★ 凭据必须**导出**给子进程（dsh）：`. file` 只设 shell 变量、不导出 ⇒
#   `systemd-run` 起的 transient 单元（到点唤醒）没有 EnvironmentFile，
#   dsh 就会报 MISSING_CREDENTIAL: OPENCODE_GO_API_KEY is not set（实测 23:25 那一轮）。
if [ -f "$AG/agent.env" ]; then
    set -a
    . "$AG/agent.env"
    set +a
fi

say() { printf '%s  [%s] %s\n' "$(date '+%F %T')" "$MODE" "$*" >> "$LOG"; }
kernel() { (cd "$REPO" && "$PY" AGENT/qyn.py "$@"); }
jget() { printf '%s' "$1" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print($2)" 2>/dev/null; }

# ── 提醒（用户 2026-11-08）：到点**直接投递**，不经模型 ────────────────────
#   为什么不让 Agent 发：提醒就是一句话（"取快递"），让模型去理解纯属浪费；
#   更糟的是它会把这句当任务去排期，然后被课间/整块规则搬到别的时间 ✗
#   （用户：「我想明天中午取快递 但是由于审查系统被扔到下午了」）。
#   这里只做确定性的事：内核说该说了 ⇒ 发微信 ⇒ 记 fired_at（避免重复打扰）。
#   到点那一轮由 `next-tick` 的 kind=reminder 负责叫醒（不依赖有没有排块）。
_reminders_due() {
    local due_json n line txt key
    due_json="$(kernel reminders --due 2>>"$LOG")"
    n="$(jget "$due_json" "len(d.get('reminders') or [])")"
    [ "${n:-0}" -gt 0 ] || return 0
    if [ "${QYN_TICK_DRY:-0}" = "1" ]; then
        say "★ DRY：有 $n 条提醒到点（本应发微信）"
        return 0
    fi
    while IFS=$'\t' read -r txt key; do
        [ -n "$txt" ] || continue
        if printf '%s' "$txt" | "$CLAWBOT" send --text - >>"$LOG" 2>&1; then
            say "⏰ 已投递提醒：$txt"
            kernel reminder-fired --key "$key" >>"$LOG" 2>&1 \
                || say "✗ 提醒已发但标记失败：$key"
        else
            say "✗ 提醒投递失败：$txt（下轮会再试）"
        fi
    done < <(printf '%s' "$due_json" | "$PY" -c "
import json, sys
for r in (json.load(sys.stdin).get('reminders') or []):
    print(str(r.get('text') or '') + chr(9) + str(r.get('key') or ''))
" 2>>"$LOG")
    return 0
}
# ★ 放在最前面：提醒的时效性最强，别等模型跑完再发（一轮可能两分钟）
_reminders_due

# ★ 上一轮没发出去的东西，先补发（失败不阻塞本轮）
flush_pending_push

# ── 同一时刻只允许一轮 ──────────────────────────────────────────────────
# ★★ 2026-10-20：改成**跨通道共享**的锁文件 —— 墙上的对话（桥）也用同一个文件 flock，
#   否则"聊天 / 微信来消息 / 07:00 定时"会各锁各的、并发跑同一个 Agent（内核无锁）✗。
# ★★ 2026-10-29：从"拿不到就跳过"改成**排队等待**。
#   实测事故：13:13:47「登记」触发一轮 → 13:13:49 的「示例课程E作业的题目」+ 一张作业图
#   触发第二轮 → 撞锁被跳过 ⇒ 那两条**一直没人处理**（游标停在 110/112），
#   要等到下一个边界（当晚 22:30）才有人理。消息不是丢了，是**排在队外**了。
#   现在最多等 10 分钟（service 的 TimeoutStartSec=15min，装得下）：
#   前一轮结束就接着跑，真的等不到才放弃。
exec 9>"$AG/logs/agent-run.lock"
if ! flock -w 600 9; then say "另一轮跑了 10 分钟还没结束，本次让路（消息留在收件箱，下次一定会读到）"; exit 0; fi

# ── 微信来消息：先把收件箱读出来（簿记由脚本做，模型只管"怎么处理"）──
MSGS_TEXT=""; NEW_CURSOR=""
if [ "$MODE" = "message" ]; then
    cursor="$(cat "$CURSOR_FILE" 2>/dev/null || true)"
    if [ -n "$cursor" ]; then
        page="$("$CLAWBOT" inbox --json --since "$cursor" 2>>"$LOG")"
    else
        page="$("$CLAWBOT" inbox --json 2>>"$LOG")"       # 首次：从头读（不漏掉之前没人理的）
    fi
    # ★ 媒体：图片/文件现在会被接收方取回来并落盘（2026-10-16 补的入站媒体）。
    #   只拼 kind+text 的话，Agent 手里只有"来了一张图"却不知道图在哪 —— 只能干看着。
    MSGS_TEXT="$(printf '%s' "$page" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
out = []
for m in (d.get("messages") or []):
    line = "- %s: %s" % (m.get("kind"), m.get("text") or "")
    if m.get("media_path"):
        line += ("\n  \u21b3 \u56fe/\u6587\u4ef6\u5df2\u53d6\u56de\u5e76\u843d\u76d8\uff1a%s"
                 "\uff08%s \u5b57\u8282\uff0c%s\uff09\u2014\u2014 **\u7528\u8bfb\u56fe\u5de5\u5177\u6253\u5f00\u5b83**\uff0c"
                 "\u4e0d\u8981\u731c\u5185\u5bb9" % (m["media_path"], m.get("media_bytes"), m.get("media_kind")))
    elif m.get("media_error"):
        line += ("\n  \u21b3 \u6709\u5a92\u4f53\u4f46\u6ca1\u53d6\u56de\u6765\uff08%s\uff09\u2014\u2014 "
                 "\u5982\u5b9e\u544a\u8bc9\u7528\u6237\u53d6\u4e0d\u5230\uff0c\u4e0d\u8981\u731c\u5185\u5bb9" % m["media_error"])
    out.append(line)
print("\n".join(out))
')"
    NEW_CURSOR="$(jget "$page" "d.get('next_cursor') or ''")"
    say "读到消息：$(printf '%s' "$MSGS_TEXT" | grep -c . || echo 0) 条；游标 ${cursor:-（首次）} → ${NEW_CURSOR:-无}"
    if [ -z "$MSGS_TEXT" ]; then
        # 文件动了但没有新消息（例如接收方只是刷了 mtime）⇒ 不花 token
        [ -n "$NEW_CURSOR" ] && printf '%s' "$NEW_CURSOR" > "$CURSOR_FILE"
        say "没有新消息，不跑模型"
        exit 0
    fi
fi

# ── 问内核：下一个边界是哪一刻 ──
tick_json="$(kernel next-tick 2>>"$LOG")"
if [ -z "$tick_json" ]; then say "内核 next-tick 没有输出，退出"; exit 1; fi
next_at="$(jget "$tick_json" "d.get('next_at','')")"
next_why="$(jget "$tick_json" "d.get('why','')")"
just_ended="$(jget "$tick_json" "((d.get('just_ended') or {}).get('time','') + ' ' + (d.get('just_ended') or {}).get('task','')).strip()")"
[ -n "$next_at" ] || { say "内核没给出 next_at，退出"; exit 1; }

# ── guard：边界还没到就什么都不做（这一条保证守卫不花 token）──
now_epoch="$(date +%s)"
next_epoch="$(date -d "$next_at" +%s 2>/dev/null || echo 0)"
if [ "$MODE" = "guard" ]; then
    if [ "$next_epoch" -gt "$now_epoch" ]; then
        say "边界还没到（$next_at · $next_why）—— 守卫不做事"
        exit 0
    fi
    say "边界已过（$next_at · $next_why）但链条没接上 —— 补跑一轮"
fi

# ── 组提示词：每一轮的**语义** ──
# ★★ 不写通道（用户 2026-10-26）："Agent 不需要管哪来的信息，那是内核组件负责的；
#    Agent 只负责回复信息，输入输出走什么是内核决定的。" ✓
#   ⇒ 这里只说"这一轮要干什么"；回哪条通道由下面**管道**按 MODE 确定性决定 ✓。
# ── 今天的课表（时间 + 教室）—— **机械注入**，不靠模型记得去查 ──
# ★ 用户 2026-11-06："这两条带上课表信息 这样方便我直接到对应课室"。
#   早报/午报的 prompt 里直接给出行，模型只需要原样放在回复最前面。
TODAY_CLASSES="$(kernel sessions --days 1 2>>"$LOG" | "$PY" -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('（课表读取失败）'); raise SystemExit
rows = d.get('sessions') or []
today = (d.get('now') or {}).get('date') or ''
out = []
for x in rows:
    if x.get('date') != today:
        continue
    t = str(x.get('periods') or x.get('time') or '')
    loc = x.get('location') or ''
    now_hm = str((d.get('now') or {}).get('time') or '')
    end = t.split('-')[-1].strip()
    passed = ' · 已过' if (now_hm and end and len(end) == 5 and end < now_hm) else ''
    out.append('- ' + t + ' ' + str(x.get('course') or '') + ('（' + loc + '）' if loc else '') + passed)
print('\n'.join(out) if out else '（今天没有课）')
" 2>>"$LOG" || true)"

case "$MODE" in
  replan)
    PROMPT="用户在大屏上按了「一键重排」。按现在的时间把今天和接下来几天重新排一遍：
读注册表与课表 → 重排 → 存快照 → brief。
★ 这一轮**不发微信**，结果（规划）直接显示在大屏上；回复里也写清这次改了什么
（哪几件挪了位置、为什么），万一排不下就说明卡在哪。"
    ;;
  morning)
    PROMPT="早上好。跑今天的规划：读一遍注册表与课表，重排今天，存快照，然后 brief 给用户。
今天有硬截止、有冲突、或需要用户决定的事，就一并说清；其余保持简洁。

★ 回复**最前面**必须先放今天要上的课（一行一节，时间 + 课名 + 教室，原样用下面这段，别改写）：
$TODAY_CLASSES
然后是今天的规划。"
    ;;
  afternoon)
    PROMPT="下午好。跑下午这一段：读一遍注册表与课表，重排剩下的今天，存快照，然后 brief 给用户。
今天还有硬截止没排上的、或需要用户决定的事，就一并说清；其余保持简洁。

★ 回复**最前面**必须先放今天剩下的课（一行一节，时间 + 课名 + 教室，原样用下面这段，别改写）：
$TODAY_CLASSES
然后是下午到晚上的安排。"
    ;;
  message)
    PROMPT="手机刚发来新消息（下面是从收件箱读出来的原文）：
$MSGS_TEXT
★ 若某条带「图/文件已取回并落盘」，**先用读图工具打开那个文件**再处理 —— 图里可能是
作业题号、截止日期、通知截图；**没打开就不要猜内容**。
按用户说的做（该登记的登记、该改的改）；要回他就把回复写出来，一条消息说一件事、纯文本、手机一屏读完。
不需要回就只做该做的事。"
    ;;
  *)
    PROMPT="规划里的一块时间到了${just_ended:+（刚结束：$just_ended）}。
★ **默认用户没做完**：不要把它标记完成，把它往后挪到合适的空档，重排剩余时间，存新快照。
★ 默认**不回复**；只有「排不下 / 今天到期的活没排上 / 需要用户决定」时才写一句话。"
    ;;
esac

say "跑一轮（$next_why）"
# ★★ 输入输出由**内核组件**决定（用户 2026-10-26）：
#   "Agent 不需要管哪来的信息，那是内核组件负责的；Agent 只负责回复信息，
#    信息的输入输出走什么是内核决定的，这是确定性的。"
#   ⇒ 管道（本脚本）负责全部 I/O：
#     ① 用 `--json` 取**最终答复文本**（事件 final ✓，不解析日志 ✓）
#     ② 按 MODE **确定性**决定发给哪条通道：message/morning ⇒ 微信；边界/守卫 ⇒ 丢弃 ✓
#     ③ Agent 自己**没有发送能力**（PATH 里摘掉 $AG/bin ⇒ clawbot 不可用 ✓）
#        ★ 但实测 2026-10-26 22:19 它用**绝对路径**照样发出去了 ✗ ⇒ PATH 只是第一道；
#          第二道是下面的**重复抑制**（发过就不再发 ✓）
#     ④ **失败重试**：`dsh: TRANSPORT: Connection error`（22:25 实测 ✗）⇒ 自动重试一次；
#        仍失败且是消息轮 ⇒ 10 分钟后自动重跑（游标没推进 ⇒ 不会漏消息 ✓）
ROUND_OUT="$(mktemp)"
# ★ 只数 **成功** 的发送：Agent 自己发失败（ok=false）不算"已发"，
#   否则管道会因为它跳过自己的发送（2026-11-10 20:00 实测：那条回复彻底没出去）
sent_before="$(grep -c '"ok": true' "$SENT_FILE" 2>/dev/null || echo 0)"
# ★ Agent 的运行 PATH：**不含** $AG/bin（clawbot 就在那）⇒ 它只能产出文本，发不了消息 ✓
AGENT_PATH="$AG/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
attempt=1
while :; do
    PATH="$AGENT_PATH" "$QYN_DSH_CLI" --profile qyn --json > "$ROUND_OUT" 2>> "$LOG" <<< "$PROMPT"
    rc=$?
    if [ "$rc" -eq 0 ] || [ "$attempt" -ge 2 ]; then break; fi
    attempt=$((attempt + 1))
    say "★ 上一轮失败（退出码 $rc）⇒ 第 $attempt 次尝试（网络抖动自愈 ✓）"
    sleep 5
done
reply="$("$PY" - "$ROUND_OUT" <<'PY'
import json, sys
final = ""
try:
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == "final":
                final = str(e.get("text") or final)
except OSError:
    pass
print(final.strip())
PY
)"
rm -f "$ROUND_OUT"
[ -n "$reply" ] && printf '%s\n' "$reply" >> "$LOG"
say "这一轮退出码 $rc"

# ── ★ 出站：**由管道确定性发送**（不依赖 Agent 记得发 ✓）──
#   路由表（固定规则，不看 prompt、不看 Agent 的措辞）：
#     message  ⇒ 发微信 ✓（微信来 ⇒ 回微信）
#     morning  ⇒ 发微信 ✓（早报是发给手机的）
#     边界/守卫 ⇒ **内核报了问题才发** ✓（排不下 / 今天到期的活没排上 / 需要用户决定）
#                 —— 以前这一步靠 Agent 自己判断并调 clawbot ✗；现在它没有发送能力，
#                    改由管道读**内核的 problems** 决定 ⇒ 确定性 ✓
#   QYN_TICK_DRY=1 ⇒ 只记日志不发（测试用 ✓）
if [ "$rc" -eq 0 ] && [ -n "$reply" ]; then
    sent_after="$(grep -c '"ok": true' "$SENT_FILE" 2>/dev/null || echo 0)"
    AGENT_SENT=$((sent_after - sent_before))
    if [ "$AGENT_SENT" -gt 0 ]; then
        # ★ 第二道防线（2026-10-26 实测：Agent 用**绝对路径**调 clawbot 照样发出去 ✗）：
        #   它已经发过 ⇒ **管道不再发**，否则用户收到两遍 ✗
        say "⚠ 本轮 Agent 自己发了 $AGENT_SENT 条 ⇒ 管道跳过发送（避免重复 ✓）"
    fi
    SEND=0; WHY=""
    if [ "$AGENT_SENT" -eq 0 ]; then
    case "$MODE" in
        message) SEND=1; WHY="微信来消息 ⇒ 回微信" ;;
        morning) SEND=1; WHY="早报 ⇒ 发手机" ;;
        afternoon) SEND=1; WHY="午报 ⇒ 发手机" ;;
        replan) SEND=0; WHY="大屏按钮 ⇒ 只更屏，不发微信" ;;
        *)
            probs="$(kernel wall --days 1 2>>"$LOG" | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('problems') or []))" 2>/dev/null || echo 0)"
            if [ "${probs:-0}" -gt 0 ]; then
                # ★ 去重（确定性）：6 小时内已经发过一次 ⇒ 不再发，免得守卫每 30 分钟刷屏 ✗
                last_ts="$("$PY" -c "
import json, os
p = os.path.expanduser('$SENT_FILE')
try:
    lines = [l for l in open(p, encoding='utf-8') if l.strip()]
    print(int(float(json.loads(lines[-1]).get('ts', 0))))
except Exception:
    print(0)
" 2>/dev/null || echo 0)"
                age=$(( $(date +%s) - ${last_ts:-0} ))
                if [ "$age" -gt 21600 ]; then
                    SEND=1; WHY="内核报了 $probs 个规划问题（6 小时内首报）"
                fi
            fi
            ;;
    esac
    if [ "$SEND" -eq 1 ]; then
        if [ "${QYN_TICK_DRY:-0}" = "1" ]; then
            say "★ DRY：本应把 ${#reply} 字发到微信（$WHY）"
        elif _send_out="$(printf '%s' "$reply" | "$CLAWBOT" send --text - 2>&1)"; then
            printf '%s\n' "$_send_out" >> "$LOG"
            say "已发回微信（${#reply} 字 · $WHY）"
        else
            printf '%s\n' "${_send_out:-（无输出）}" >> "$LOG"
            # ★ 存下来，下轮开头自动补发（否则这条内容就永远丢了 ✗）
            printf '%s' "$reply" > "$PENDING_PUSH"
            say "✗ 发回微信失败（${#reply} 字）—— 已存为待补发：$(printf '%s' "${_send_out:-}" | tail -1 | cut -c1-120)"
        fi
    else
        say "本轮不发（$MODE · 内核无问题）—— 回复留在日志里"
    fi
    fi   # ← if AGENT_SENT -eq 0
fi

# ── ★ 消息轮失败 ⇒ 10 分钟后自动重试（否则用户会一直等不到回复 ✗）──
#   实测 2026-10-26 22:25：`dsh: TRANSPORT: Connection error` ⇒ exit 1 ⇒ 无回复 ✗
#   游标**没推进** ✓（只有 rc=0 才推进 ✓）⇒ 重跑不会漏、也不会重复处理 ✓
if [ "$MODE" = "message" ] && [ "$rc" -ne 0 ] && [ "${QYN_TICK_DRY:-0}" != "1" ]; then
    if systemd-run --user --collect --quiet --on-active=10min \
         --unit="qyn-agent-retry-$(date +%s)" /bin/bash "$0" message >>"$LOG" 2>&1; then
        say "★ 本轮失败 ⇒ 已排 10 分钟后自动重试（游标未推进 ✓ 不会漏）"
    else
        say "✗ 排重试失败（下一条消息/边界会再跑）"
    fi
fi

# ── 消息轮：**成功之后**才推进游标（失败就下次重读，不会漏）──
if [ "$MODE" = "message" ] && [ "$rc" -eq 0 ] && [ -n "$NEW_CURSOR" ]; then
    printf '%s' "$NEW_CURSOR" > "$CURSOR_FILE"
    say "游标已推进到 $NEW_CURSOR"
fi

# ── 跑完再问一次内核（规划可能变了），排下一次唤醒 ──
tick_json="$(kernel next-tick 2>>"$LOG")"
next_at="$(jget "$tick_json" "d.get('next_at','')")"
next_why="$(jget "$tick_json" "d.get('why','')")"
if [ -z "$next_at" ]; then say "跑完没拿到 next_at —— 靠每日锚点与守卫兜底"; exit "$rc"; fi

# 边界已过（例如刚跑完时点已经过去）⇒ 往前推一分钟，避免立刻又醒
if [ "$(date -d "$next_at" +%s 2>/dev/null || echo 0)" -le "$now_epoch" ]; then
    next_at="$(date -d "$next_at 1 minute" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "$next_at")"
fi

unit="qyn-agent-boundary-$(date -d "$next_at" '+%Y%m%d%H%M' 2>/dev/null || date '+%Y%m%d%H%M')"
# ★ 先把**所有**旧的到点唤醒单元撤掉：单元名带时间戳，只停同名的那个等于没停 ——
#   每次重排都会多留一个到点唤醒（实测 22:00 与 23:25 同时挂着），白烧一轮。
for u in $(systemctl --user list-units --all --no-legend 'qyn-agent-boundary-*.timer' 2>/dev/null | awk '{print $1}'); do
    systemctl --user stop "$u" >/dev/null 2>&1
done
for u in $(systemctl --user list-units --all --no-legend 'qyn-agent-boundary-*.service' 2>/dev/null | awk '{print $1}'); do
    systemctl --user reset-failed "$u" >/dev/null 2>&1
done
if systemd-run --user --collect --on-calendar="$next_at" --unit="$unit" \
        /bin/bash "$REPO/CENTRAL/scripts/qyn-agent-tick.sh" boundary >> "$LOG" 2>&1; then
    say "已排下一次唤醒：$next_at（$next_why）· 单元 $unit"
else
    say "排下一次唤醒失败（$next_at）—— 靠每日锚点与守卫兜底"
fi
exit "$rc"
