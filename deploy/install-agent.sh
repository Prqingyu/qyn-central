#!/usr/bin/env bash
# 在真机（中控机 CENTRAL）上装配 DSH 时间管理 Agent。
#
# 用法（在 release 根目录，即含 AGENT/ .dsh/ AGENTS.md 的那层）：
#     bash CENTRAL/deploy/install-agent.sh
# 或从别处指定 release 根：
#     bash CENTRAL/deploy/install-agent.sh /path/to/release
#
# 只动这两处，**不碰**已有的状态墙部署（~/qyn/current、wall.db、qyn-*.service）：
#     ~/qyn/agent/     ← Agent 的 release / 数据 / 日志 / CLI
#     ~/qyn/.dsh/      ← DSH_HOME（AGENTS.md、profiles/qyn、skills）
#
# 可重复执行（幂等）：再跑一次就是发一个新 release 并把 current 指过去，数据不动。
#
# ★ 顺序保证（2026-10 加固）：所有校验（node / CLI / 装配 / 内核自检 / PHONE 自检 /
#   全套测试 / 单元安装）**都在切 current 之前**；current 是最后一步原子切换。
#   任何一个 die 都会经 EXIT trap 把 current 切回旧 release 并打印旧路径 ——
#   校验失败绝不等于「线上跑坏版本」（真机曾出过 10-14 13:26:28 起动 → 13:26:51 FAILURE：
#   current 早切了，坏的 release 被 07:00 的定时器用上了）。
#   数据外置段**先备份再判断**：只补真机缺的文件，同名不同内容绝不覆盖
#   （真机数据是唯一真源；默认 die 要求人工比对，可用 QYN_DATA_DIVERGENCE=keep 放行）。
#   ★ 另外在切 current 之前做一次**数据完整性硬断言**（逐文件 md5 清单 before/after）：
#   部署只要改动或删除了任何一个已有数据文件就 die —— 2026-10-15 的事故就是"没人检查"。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "${1:-$SELF_DIR/..}" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
BASE="$HOME/qyn"
AG="$BASE/agent"
DH="$BASE/.dsh"
NODE_VER="${QYN_NODE_VER:-v24.18.0}"
DSH_VER="${QYN_DSH_VER:-0.2.0-rc.2}"
# 同名不同内容时的姿态：die（默认，要求人工比对）/ keep（放行差异但**仍然不覆盖**真机数据）
DIVERGE="${QYN_DATA_DIVERGENCE:-die}"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()  { printf '   ✓ %s\n' "$*"; }
die() { printf '   ✗ %s\n' "$*" >&2; exit 1; }

[ -f "$SRC/AGENT/qyn.py" ] || die "release 根不对（缺 AGENT/qyn.py）：$SRC"
[ -f "$SRC/AGENTS.md" ]    || die "release 根不对（缺 AGENTS.md）：$SRC"
case "$DIVERGE" in die|keep) ;; *) die "QYN_DATA_DIVERGENCE 只能是 die 或 keep：$DIVERGE" ;; esac

# ---- 切换/回滚：current 在最后一步才切；任何非零退出都回到 OLD ----
# 用 readlink（原样目标，如 "releases/2026..."）而不是 readlink -f：回滚时按**原样**重建软链；
# 用 -f 的话，current 缺失时它会返回 .../agent/current 自己，回滚就会造出自指软链。
OLD_RAW=""
if [ -L "$AG/current" ]; then
  OLD_RAW="$(readlink "$AG/current" 2>/dev/null || true)"
fi
CUR_SWITCHED=no
CLEANUP_DIR=""      # 临时沙箱（9/10 用），成功失败都清理
on_exit() {
  rc=$?
  if [ -n "$CLEANUP_DIR" ]; then rm -rf "$CLEANUP_DIR" 2>/dev/null; fi
  [ "$rc" -eq 0 ] && return 0
  if [ "$CUR_SWITCHED" = yes ]; then
    if [ -n "$OLD_RAW" ]; then
      ln -sfn "$OLD_RAW" "$AG/current" 2>/dev/null || true
      printf '\n   ↩ 回滚 current -> %s\n' "$OLD_RAW"
    else
      rm -f "$AG/current"
      printf '\n   ↩ 回滚：本来没有 current，已删除\n'
    fi
  fi
  printf '   ✗ 部署失败（退出码 %s）—— 线上仍是 %s\n' "$rc" "${OLD_RAW:-（无旧版本）}"
  return 0
}
trap on_exit EXIT

# ---- 数据完整性硬断言（2026-10-15 真机数据被删事故后加）----
# ~/qyn/agent/data 是唯一真源。部署允许"补缺失"（首次播种：包内快照填真机缺的文件），
# 但**绝不允许**改动或删除任何已有文件。0/10 记一份逐文件 md5 清单，10/10 切 current
# **之前**再比一次：有已有文件被改/被删 → die（current 因此不会切）。
# 清单与 diff 留在 $AG/logs/ 供事后查：data-integrity-<ts>.{before,after,diff}
data_manifest() {  # $1 = 清单输出文件
  ( cd "$AG/data" 2>/dev/null || exit 0
    find . -type f -print | LC_ALL=C sort | xargs -r md5sum | LC_ALL=C sort
  ) > "$1" 2>/dev/null || true
}
assert_data_intact() {
  before="$AG/logs/data-integrity-$TS.before"
  after="$AG/logs/data-integrity-$TS.after"
  dfile="$AG/logs/data-integrity-$TS.diff"
  data_manifest "$after"
  printf '   · 数据汇总哈希 before=%s after=%s（%s，%s 个文件）\n' \
    "$(md5sum "$before" | cut -d' ' -f1)" "$(md5sum "$after" | cut -d' ' -f1)" \
    "$AG/data" "$(wc -l < "$after" | tr -d ' ')"
  if cmp -s "$before" "$after"; then
    ok "数据完整性断言：逐文件 md5 与部署前完全一致（真机数据一个字节都没动）"
    return 0
  fi
  diff -u "$before" "$after" > "$dfile" 2>&1 || true
  changed="$(grep -c '^-' "$dfile" 2>/dev/null || true)"
  added="$(grep -c '^+' "$dfile" 2>/dev/null || true)"
  changed=$((changed > 0 ? changed - 1 : 0))   # 减掉 --- 表头
  added=$((added > 0 ? added - 1 : 0))         # 减掉 +++ 表头
  if [ "$changed" -gt 0 ]; then
    sed -n '1,30p' "$dfile" >&2
    die "数据完整性断言失败：部署改动/删除了真机已有数据（$changed 个）—— 清单 diff 在 $dfile"
  fi
  ok "数据完整性断言：已有文件零改动，仅新增 $added 个（首次播种补缺失）"
}

say "0/10 目录"
mkdir -p "$AG/releases" "$AG/data" "$AG/logs" "$AG/bin" "$AG/phone-state" "$DH/profiles" "$DH/skills"
ok "$AG（release/数据/日志/命令入口/手机状态）"
ok "$DH（DSH_HOME）"
BEFORE_MANIFEST="$AG/logs/data-integrity-$TS.before"
data_manifest "$BEFORE_MANIFEST"
ok "数据完整性基准：$AG/data 共 $(wc -l < "$BEFORE_MANIFEST" | tr -d ' ') 个文件，汇总哈希 $(md5sum "$BEFORE_MANIFEST" | cut -d' ' -f1)"

say "1/10 落 release：$TS（先不切 current）"
DEST="$AG/releases/$TS"
if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  # 排除运行时垃圾；data 单独处理（见下一步）
  tar -C "$SRC" \
      --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
      --exclude='.runtime' --exclude='node_modules' \
      --exclude='AGENT/data' --exclude='*.bak' \
      -cf - . | tar -C "$DEST" -xf -
  if [ -d "$SRC/AGENT/data" ]; then
    mkdir -p "$DEST/AGENT/data"
    tar -C "$SRC/AGENT/data" --exclude='*.bak' --exclude='__pycache__' -cf - . \
      | tar -C "$DEST/AGENT/data" -xf -
  fi
fi
ok "$DEST（current 还没动，仍是 ${OLD_RAW:-（无）}）"

say "2/10 数据外置（release 换代不丢状态；先备份，绝不覆盖真机数据）"
SEED="$DEST/AGENT/data"          # 发布包里的开发机快照：**只用来补缺失**，不覆盖
BK="$AG/logs/data-$TS.tar.gz"    # 部署前快照（要还原就 tar -xzf 这一份）
mkdir -p "$AG/logs"
if tar -C "$AG/data" --exclude='*.bak' --exclude='__pycache__' -czf "$BK" . 2>/dev/null; then
  ok "部署前快照 $BK（$(stat -c %s "$BK") 字节）"
else
  die "备份真机数据失败（$AG/data → $BK）—— 先别部署"
fi
# 快照轮转：只留最近 QYN_DATA_BACKUPS（默认 5）份
keep="${QYN_DATA_BACKUPS:-5}"
mapfile -t _bks < <(ls -1t "$AG/logs"/data-*.tar.gz 2>/dev/null || true)
_n=0
for _b in "${_bks[@]:-}"; do
  [ -n "$_b" ] || continue
  _n=$((_n + 1))
  [ "$_n" -gt "$keep" ] || continue
  rm -f "$_b"
  printf '   · 清理旧快照 %s\n' "$_b"
done
if [ -L "$SEED" ]; then
  ok "包内 AGENT/data 已是软链，无需播种"
elif [ -d "$SEED" ]; then
  # ---- 第一遍：只分类，**一个字节都不写** ----
  MISSING=""
  added=0; kept=0; conflict=0
  while IFS= read -r rel; do
    rel="${rel#./}"
    [ -n "$rel" ] || continue
    if [ -e "$AG/data/$rel" ]; then
      if cmp -s "$SEED/$rel" "$AG/data/$rel"; then
        kept=$((kept + 1))
      else
        conflict=$((conflict + 1))
        printf '   ! 同名不同内容（真机版保留，绝不覆盖）：%s（真机 %s 字节 / 包内 %s 字节）\n' \
          "$rel" "$(stat -c %s "$AG/data/$rel")" "$(stat -c %s "$SEED/$rel")"
        if [ "$DIVERGE" = die ]; then
          printf '     ---- diff（< 真机 / > 包内，最多 40 行）----\n'
          diff -u "$AG/data/$rel" "$SEED/$rel" 2>&1 | head -40 || true
        fi
      fi
    else
      added=$((added + 1))
      MISSING="$MISSING$rel"$'\n'
    fi
  done < <(cd "$SEED" && find . -type f ! -name '*.bak' ! -name '*.pyc' -print)
  # 有冲突且没显式放行 → 在这里停：连"补缺失"都不做，失败的部署不留半截状态
  if [ "$conflict" -gt 0 ] && [ "$DIVERGE" = die ]; then
    die "真机数据与发布包快照有 $conflict 个同名不同内容的文件 —— 拒绝覆盖，请人工比对（上面是 diff）。
      真机数据是唯一真源；确认无碍后可用 QYN_DATA_DIVERGENCE=keep 复跑（仍然不会覆盖真机数据）。
      本次一个字节都没写（补缺失也没做），真机数据保持原样。"
  fi
  # ---- 第二遍：只补缺失 ----
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    mkdir -p "$AG/data/$(dirname "$rel")"
    cp -p "$SEED/$rel" "$AG/data/$rel"
    ok "补缺失 $rel"
  done <<< "$MISSING"
  ok "逐文件处理：补缺失 $added / 一致 $kept / 同名不同 $conflict（真机已有文件一个字节都没被覆盖）"
  if [ "$conflict" -gt 0 ]; then
    printf '   ! 已按 QYN_DATA_DIVERGENCE=keep 放行 %s 个差异文件：真机已有数据一个字节都没动\n' "$conflict"
  fi
else
  ok "包内没有 AGENT/data 快照，跳过播种"
fi
# 删掉 release 里的快照目录：只可能是本步骤刚铺的，且必须证明它在 release 内部
if [ -L "$SEED" ]; then
  rm -f "$SEED"
elif [ -d "$SEED" ]; then
  case "$(readlink -f "$SEED")" in
    "$(readlink -f "$DEST")"/*) rm -rf "$SEED" ;;
    *) die "拒绝删除不在 release 里的路径：$SEED" ;;
  esac
fi
ln -sfn "../../../data" "$DEST/AGENT/data"
[ "$(readlink -f "$DEST/AGENT/data")" = "$AG/data" ] \
  || die "AGENT/data 软链没指到 $AG/data（实际 $(readlink -f "$DEST/AGENT/data")）"
ok "AGENT/data -> ../../../data（指向 $AG/data）"

say "3/10 Node（用户本地，不需要 sudo）"
NODE_BIN=""
if [ -x "$AG/node/bin/node" ]; then
  NODE_BIN="$AG/node/bin/node"; ok "已有 $($NODE_BIN --version)"
else
  if command -v node >/dev/null 2>&1; then
    NODE_BIN="$(command -v node)"; ok "用系统 node $($NODE_BIN --version)"
  else
    mkdir -p "$AG/node-dl"
    URL="https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-x64.tar.xz"
    echo "   下载 $URL"
    if command -v wget >/dev/null 2>&1; then wget -q -O "$AG/node-dl/node.tar.xz" "$URL"
    else curl -fsSL -o "$AG/node-dl/node.tar.xz" "$URL"; fi
    tar -C "$AG/node-dl" -xf "$AG/node-dl/node.tar.xz"
    rm -rf "$AG/node"; mv "$AG/node-dl/node-$NODE_VER-linux-x64" "$AG/node"
    rm -rf "$AG/node-dl"
    NODE_BIN="$AG/node/bin/node"
    ok "装好 $($NODE_BIN --version) → $AG/node"
  fi
fi
export PATH="$(dirname "$NODE_BIN"):$PATH"

say "4/10 DSH CLI（共享，不放 release 里）"
CLI_BIN="$AG/cli/node_modules/.bin/dsh"
if [ -x "$CLI_BIN" ]; then
  ok "已有 CLI"
else
  NPM="$(dirname "$NODE_BIN")/npm"
  [ -x "$NPM" ] || NPM="$(command -v npm || true)"
  [ -n "$NPM" ] || die "找不到 npm（node 装得不完整？）"
  "$NPM" install --prefix "$AG/cli" "@deepseek-ai/dsh@$DSH_VER" >/dev/null 2>&1 \
    || die "npm install 失败（看看网络或换 QYN_DSH_VER）"
  ok "装好 @deepseek-ai/dsh@$DSH_VER"
fi
[ -x "$CLI_BIN" ] || die "CLI 可执行文件不在：$CLI_BIN"

say "4.5/10 同步技能到 DSH_HOME"
# ★ 技能是 Agent 每轮**按需加载**的规则（水课跟课程、课表整段替换闸、锚定规矩…）。
#   原先这里只建目录、不拷 ⇒ 真机上的技能停在 2026-10-16，之后所有技能改动
#   **一次都没生效**（实测：10-21 查出来时还是 10-16 的版本）。
#   只同步 skills（profiles/sessions/agents 是运行态，不能覆盖）。
if [ -d "$DEST/.dsh/skills" ]; then
  mkdir -p "$DH/skills"
  for d in "$DEST"/.dsh/skills/*/; do
    [ -d "$d" ] || continue
    n="$(basename "$d")"
    mkdir -p "$DH/skills/$n"
    cp -a "$d". "$DH/skills/$n/" 2>/dev/null || true
  done
  ok "技能已同步 -> $DH/skills（$(ls -1 "$DH/skills" 2>/dev/null | wc -l) 个）"
else
  printf '   ! release 里没有 .dsh/skills（deploy-agent.ps1 的 items 里有 .dsh 吗？）\n'
fi

# ★ AGENTS.md 同样是"每轮都在上下文里"的规矩 —— 实测它和技能一样会停在旧版
#   （2026-10-22 查出来真机还是 10-15 的，这几天加的规矩它根本没看到）。
if [ -f "$DEST/AGENTS.md" ]; then
  cp -a "$DEST/AGENTS.md" "$DH/AGENTS.md"
  ok "AGENTS.md 已同步 -> $DH/AGENTS.md（$(wc -c < "$DH/AGENTS.md") 字节）"
fi

say "5/10 装配 DSH_HOME 并验收（复用彩排脚本，一套逻辑）"
# ★ 这里用 $DEST 而不是 $AG/current：current 要到 10/10 才切，验收的必须是**新 release 本体**
cd "$DEST"
QYN_DSH_HOME="$DH" QYN_DSH_CLI="$CLI_BIN" PATH="$(dirname "$NODE_BIN"):$PATH" \
  python3 AGENT/dsh/rehearsal/rehearse.py

say "6/10 内核自检"
QYN_DSH_HOME="$DH" python3 AGENT/qyn.py validate
python3 AGENT/scripts/smoke_qyn.py 2>&1 | tail -3

say "7/10 手机通道（PHONE）：命令入口 + 状态外置 + 真机自检"
PH="$DEST/PHONE"
[ -f "$PH/main.py" ] || die "release 里没有 PHONE/main.py（deploy-agent.ps1 的 items 里有 PHONE 吗？）"

# 状态外置：与 AGENT/data 同一套路 —— release 换代不丢。里面是微信凭证、长轮询游标、
# 收件箱、去重表、独占锁。发布包**不带** state（打包脚本有排除 + 凭证断言），这里是防御性处理。
if [ -d "$PH/state" ] && [ ! -L "$PH/state" ]; then
  cp -a "$PH/state/." "$AG/phone-state/" 2>/dev/null || true
  rm -rf "$PH/state"
fi
ln -sfn ../../../phone-state "$PH/state"
ok "state -> $AG/phone-state（换代不丢）"

# 命令入口：中控机没有 pip（externally-managed），而 PHONE 是零第三方依赖的，
# 所以不装成 Python 包 —— 给一个走 current 软链的包装脚本即可，换 release 自动用新代码。
cat > "$AG/bin/clawbot" <<'CLAWBOT'
#!/usr/bin/env bash
# 手机通道命令入口（由 CENTRAL/deploy/install-agent.sh 生成；手改会在下次部署被覆盖）
exec python3 "$HOME/qyn/agent/current/PHONE/main.py" "$@"
CLAWBOT
chmod +x "$AG/bin/clawbot"
ok "$AG/bin/clawbot"

# 真机自检：在**与开发机不同的 Python（中控机 3.14）**上跑一遍。零依赖、不联网、只用临时目录。
( cd "$PH" && python3 tests/run_all.py ) || die "PHONE 自检未通过（上面就是原因）"

say "8/10 单元安装（幂等；已安装必须与 release 逐字节一致）+ 日志收紧"
UNIT_SRC="$DEST/CENTRAL/scripts/systemd"
UNIT_DST="$HOME/.config/systemd/user"
# 单元全在这里装（含 07:00 早报 / 14:00 午报）：以前只装 qyn-phone.service，其余靠手工 cp ——
# 于是 qyn-agent.service 的 WorkingDirectory 长期停在 .../current（仓库已改成稳定父目录
# %h/qyn/agent 却没装上），每个 release 都新建一个工作区（曾攒到 11 个）。
UNIT_UNITS="qyn-agent.service qyn-agent.timer qyn-agent-web.service qyn-egress-probe.service qyn-egress-probe.timer qyn-ocgo-rotate.service qyn-ocgo-rotate.timer qyn-ocgo-fallback.service qyn-ocgo-fallback.timer qyn-agent-rearm.service qyn-agent-rearm.timer qyn-agent-message.service qyn-agent-inbox.path qyn-phone.service qyn-agent-probe.service qyn-agent-probe.timer qyn-agent-plan-publish.service qyn-agent-plan-publish.path qyn-agent-plan-publish.timer qyn-agent-noon.service qyn-agent-noon.timer qyn-agent-replan.service"
# 内容变化时允许 enable 的（timer 与 Web UI）；其余只在变化时提示人工处理。
# ★ 手机通道**只装不启**：启了就会和旧机器上的接收方同时跑同一个微信账号
#   （同一账号只允许一个接收方，见 PHONE/INTERFACE.md「部署」）。切换由人来做。
UNIT_AUTO="qyn-agent.timer qyn-egress-probe.timer qyn-ocgo-rotate.timer qyn-ocgo-fallback.timer qyn-agent-web.service qyn-agent-rearm.timer qyn-agent-inbox.path qyn-agent-probe.timer qyn-agent-plan-publish.path qyn-agent-plan-publish.timer qyn-agent-noon.timer"
# ★★ 防复发（2026-10-25 中控重启后 collect / screen-policy 没起来 ✗）：
#   任何单元**不许**写 `After=default.target` —— 它是 `WantedBy=default.target`（被 target 拉起），
#   再排到 target 之后就成**顺序环**，systemd 会丢掉一条边 ⇒ 开机时依赖它的单元直接不启动 ✗。
for u in $UNIT_UNITS; do
  s="$UNIT_SRC/$u"
  if [ -f "$s" ] && grep -qE '^[[:space:]]*After=.*default\.target' "$s"; then
    die "单元 $u 里写了 After=default.target ⇒ 会造成顺序环（改成 After=network.target）"
  fi
done
mkdir -p "$UNIT_DST"
CHANGED=" "
for u in $UNIT_UNITS; do
  s="$UNIT_SRC/$u"; d="$UNIT_DST/$u"
  [ -f "$s" ] || die "release 里缺单元：$s（deploy-agent.ps1 的 items 里有 CENTRAL\\scripts 吗？）"
  if [ -f "$d" ] && cmp -s "$s" "$d"; then
    ok "未变  $u"
    continue
  fi
  if [ -f "$d" ]; then
    cp -p "$d" "$d.bak-qyn"
    printf '   · 内容变化，旧版留档 %s.bak-qyn\n' "$u"
  fi
  cp -p "$s" "$d"
  chmod 644 "$d"
  CHANGED="$CHANGED$u "
  ok "已更新 $u"
done
systemctl --user daemon-reload || true
for u in $UNIT_UNITS; do
  case "$CHANGED" in *" $u "*) ch=yes ;; *) ch=no ;; esac
  case " $UNIT_AUTO " in
    *" $u "*)
      if [ "$(systemctl --user is-enabled "$u" 2>/dev/null || true)" != enabled ]; then
        if systemctl --user enable "$u" >/dev/null 2>&1; then ok "已启用 $u"
        else printf '   ! 启用 %s 失败，人工：systemctl --user enable %s\n' "$u" "$u"; fi
      fi
      # ★ 新装的 **timer / path** 光 enable 不会立刻生效（那要等下次开机）—— 实测：
      #   qyn-agent-probe.timer 装上了却一直 inactive；qyn-agent-plan-publish.path 同理
      #   （只补了 .timer 没补 .path，于是数据变了也不推、部署验证直接失败）。
      case "$u" in
        *.timer|*.path)
          if ! systemctl --user is-active --quiet "$u" 2>/dev/null; then
            if systemctl --user start "$u" >/dev/null 2>&1; then ok "已启动 $u"
            else printf '   ! 启动 %s 失败，人工：systemctl --user start %s\n' "$u" "$u"; fi
          fi
          ;;
      esac
      if [ "$ch" = yes ] && systemctl --user is-active --quiet "$u" 2>/dev/null; then
        systemctl --user restart "$u" || die "重启 $u 失败"
        ok "已重启 $u（内容变化且原本在跑）"
      fi
      ;;
    *)
      # ★ 接收方（`qyn-phone.service`）：只要**在跑**就重启 —— 判据是"在跑"，
      #   不是"单元文件变了"。要更新的恰恰是**代码**，单元文件常常一个字没动；
      #   上一版把这段写在 `if [ "$ch" = yes ]` 里面，于是**永远不重启**。
      #   实测（2026-10-16）：接收方从 16:18:56 起跑旧代码，16:45 部署的入站留痕与
      #   16:50 修好的发送流水**一次都没生效**，直到人工重启才发现。
      #   重启是安全的：接口承诺"至少一次"，游标已落盘，最坏重投一条。
      #   "只装不启"的本意只是**不要在新机器上擅自启用**（同一微信账号只允许一个接收方）。
      if [ "$u" = "qyn-phone.service" ]; then
        if systemctl --user is-active --quiet "$u" 2>/dev/null; then
          systemctl --user restart "$u" || die "重启 $u 失败"
          ok "已重启 $u（部署后加载新代码；未启用、未改动其启用状态）"
        else
          printf '   · %s 没在跑，未启用（同一微信账号只允许一个接收方，切换由人来做）\n' "$u"
        fi
      elif [ "$ch" = yes ]; then
        printf '   ! %s 内容已更新但**未重启**（同一个微信账号只允许一个接收方，切换由人来做）\n' "$u"
        printf '     人工：systemctl --user restart %s\n' "$u"
      fi
  esac
done
# 断言：装上的必须与 release 里的一模一样（防部分拷贝/磁盘满）
for u in $UNIT_UNITS; do
  cmp -s "$UNIT_SRC/$u" "$UNIT_DST/$u" || die "已安装单元与 release 不一致：$u"
done
ok "15 个单元与 release 逐字节一致（qyn-agent.{service,timer} / qyn-agent-web.service / qyn-egress-probe.{service,timer} / qyn-agent-rearm.{service,timer} / qyn-agent-message.service / qyn-agent-inbox.path / qyn-phone.service / qyn-agent-probe.{service,timer} / qyn-agent-plan-publish.{service,path,timer}）"
for f in "$UNIT_SRC"/*.service "$UNIT_SRC"/*.timer "$UNIT_SRC"/*.path; do
  [ -e "$f" ] || continue
  b="$(basename "$f")"
  case " $UNIT_UNITS " in *" $b "*) ;; *) printf '   · 不归本脚本管（状态墙等，另有部署）：%s\n' "$b" ;; esac
done
# 日志收紧：web.log 里有 Web UI 的 token 明文（已手工 chmod，这里固化）
chmod 700 "$AG/logs" 2>/dev/null || true
find "$AG/logs" -maxdepth 1 -type f -name '*.log' -exec chmod 600 {} + 2>/dev/null || true
ok "日志：目录 700，*.log 600"
# /etc/logrotate.d 要 root（用户单元装不了）——退而求其次：本脚本自己轮转，超限保留 .1/.2/.3
rotate_log() {
  f="$1"
  [ -f "$f" ] || return 0
  sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
  [ "$sz" -gt "${QYN_LOG_MAX_BYTES:-5242880}" ] || return 0
  i=3
  while [ "$i" -ge 1 ]; do
    [ -f "${f}.${i}" ] && mv -f "${f}.${i}" "${f}.$((i + 1))"
    i=$((i - 1))
  done
  mv -f "$f" "${f}.1"
  : > "$f"
  chmod 600 "$f"
  printf '   · 轮转 %s（原 %s 字节 > %s）\n' "$f" "$sz" "${QYN_LOG_MAX_BYTES:-5242880}"
  return 0
}
for f in "$AG/logs"/*.log; do
  [ -e "$f" ] || continue
  rotate_log "$f"
done
printf '   ! 系统级轮转要 root（本次已内建：>%s 字节时轮转，保留 .1/.2/.3）\n' "${QYN_LOG_MAX_BYTES:-5242880}"

say "9/10 全套测试（插件 / 数据 / 状态墙 / 中继）"
# 以前只跑 rehearse + smoke，插件（模型真正面对的那一层）与数据合规都没跑过。
cd "$DEST"
# ★ 插件测试必须在**真的数据副本**上跑，不能直接打 release：
#   plugin.test.mjs 的写工具用 fs.cpSync 复制 AGENT 树，而发布版 AGENT/data 是软链
#   （→ ~/qyn/agent/data）；Node 的 cpSync 会把这条软链复制成**指向真机数据的绝对软链**
#   （已实测：cpSync 把相对链 ../../../data 解析后写成绝对 target），于是 :262 的夹具覆盖
#   与写工具的落盘**穿透软链写进真机数据**。2026-10-15 18:15 实测：对着 release 跑一次，
#   真机 data.json / rules.json / schedule.json 被测试夹具清空（plans.json 逃过）。
#   所以这里先用 cp -aL 解引用出沙箱（AGENT/data 变成真副本），cwd 与 QYN_REPO_ROOT 都指沙箱：
#   插件无论走 config.repoRoot、QYN_REPO_ROOT 还是 cwd，都落在副本上；沙箱内没有任何软链，
#   cpSync 也就复制不出指向真机的链。沙箱在退出时（含失败）删除。
PLUGIN_TEST="$DEST/AGENT/dsh/plugin/qyn-time/test/plugin.test.mjs"
[ -f "$PLUGIN_TEST" ] || die "release 里缺插件测试：$PLUGIN_TEST"
VERIFY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/qyn-verify-XXXXXX")"
CLEANUP_DIR="$VERIFY_DIR"
mkdir -p "$VERIFY_DIR/repo"
cp -aL "$DEST/AGENT" "$VERIFY_DIR/repo/AGENT" || die "复制沙箱 AGENT 树失败"
[ -d "$VERIFY_DIR/repo/AGENT/data" ] || die "沙箱里没有 AGENT/data"
[ ! -L "$VERIFY_DIR/repo/AGENT/data" ] || die "沙箱里 AGENT/data 仍是软链（cp -aL 没生效？）—— 拒绝跑插件测试"
( cd "$VERIFY_DIR/repo" && QYN_REPO_ROOT="$VERIFY_DIR/repo" "$NODE_BIN" AGENT/dsh/plugin/qyn-time/test/plugin.test.mjs ) \
  || die "插件测试未通过（模型真正面对的那一层）"
python3 AGENT/scripts/check_data.py || die "数据合规检查未通过"
[ -f "$DEST/CENTRAL/GUI/status-wall/test_wall.py" ] \
  || die "release 里缺 CENTRAL/GUI/status-wall/test_wall.py（deploy-agent.ps1 的 items 里有 CENTRAL\\GUI\\status-wall 吗？）"
python3 CENTRAL/GUI/status-wall/test_wall.py || die "状态墙端到端测试未通过"
[ -f "$DEST/CENTRAL/tools/test_lan_http_proxy.py" ] \
  || die "release 里缺 CENTRAL/tools/test_lan_http_proxy.py（deploy-agent.ps1 的 items 里有 CENTRAL\\tools 吗？）"
python3 CENTRAL/tools/test_lan_http_proxy.py || die "中继白名单策略测试未通过"
# Agent 系统探针（大屏 SERVICE 的 svc-agent 节点）：日志解析最容易悄悄烂掉，
# 自测用夹具盯住正常/异常两条路径，且**不碰真机数据**（基目录走临时目录）。
[ -f "$DEST/CENTRAL/scripts/agent-probe.py" ] \
  || die "release 里缺 CENTRAL/scripts/agent-probe.py（deploy-agent.ps1 的 items 里有 CENTRAL\scripts 吗？）"
python3 CENTRAL/scripts/agent-probe.py --selftest || die "Agent 系统探针自测未通过"

say "10/10 切换 current（最后一步，原子；到这里所有校验都过了）"
assert_data_intact   # ★ 硬断言：真机已有数据不许被本次部署改动/删除
[ -d "$DEST" ] || die "新 release 不在：$DEST"
ln -sfn "releases/$TS" "$AG/current"
CUR_SWITCHED=yes
[ "$(readlink -f "$AG/current")" = "$(readlink -f "$DEST")" ] || die "current 指向不对：$(readlink -f "$AG/current")"
ok "current -> releases/$TS（旧：${OLD_RAW:-（无）}）"

if [ -f "$AG/phone-state/credentials.json" ]; then
  ok "已有微信凭证（不用重新扫码）"
else
  printf '   ! 还没有微信凭证。两条路：\n'
  printf '     a) 从旧机器搬：停掉旧机器的接收方 → 拷 phone-state 过来 → 在这里启用单元\n'
  printf '     b) 在这里扫码：%s/login（要能看到二维码）\n' "$AG/bin/clawbot"
fi

cat <<EOF

$(printf '\033[1m装好了。\033[0m')

  代码     $AG/current  →  $(readlink -f "$AG/current")
  数据     $AG/data（软链在 AGENT/data，换代不丢；部署前快照 $BK）
  DSH_HOME $DH
  CLI      $CLI_BIN
  Node     $NODE_BIN
  手机     $AG/bin/clawbot（命令入口，走 current 软链）
           状态 $AG/phone-state（软链在 PHONE/state，换代不丢）
           日志 $AG/logs/phone.log · 单元 qyn-phone.service（只装不启，切换由人来做）
  单元     $HOME/.config/systemd/user/ 下 10 个（与 release 逐字节一致）
           qyn-agent.timer 07:00 · qyn-egress-probe.timer 每 5 分钟（svc-egress）· qyn-agent-probe.timer 每 1 分钟（svc-agent）· qyn-agent-plan-publish.path 数据一变就推（agent-plan）
           qyn-agent-web.service 8799（回环，token 在 $AG/logs/web.log）

  ★ 失败即回滚：本脚本任何一步 die 都会把 current 切回旧 release 并打印旧路径。

  ★ 数据差异：发布包里带的是开发机 AGENT/data 快照，真机数据是唯一真源 ——
     只补缺失、绝不覆盖；同名不同内容默认停下要求人工比对（QYN_DATA_DIVERGENCE=keep 放行）。
  ★ 数据完整性：部署前后逐文件 md5 清单比对，已有文件被改/被删就 die
     （清单与 diff：$AG/logs/data-integrity-*.{before,after,diff}）。

跑一轮（无人值守，一次性）：
  cd $AG/current && QYN_DSH_HOME=$DH \\
    PATH=$(dirname "$NODE_BIN"):\$PATH \\
    $CLI_BIN --profile qyn "现在我要做什么"

常驻：见 CENTRAL/scripts/systemd/qyn-agent.{service,timer}

手机通道切换（★ 同一个微信账号只允许一个接收方，务必先停旧机器上的那个）：
  旧机器  停掉 clawbot run
  这里    systemctl --user enable --now qyn-phone
  验收    $AG/bin/clawbot status --json     # logged_in / receiver.running / inbox.last_n
EOF
