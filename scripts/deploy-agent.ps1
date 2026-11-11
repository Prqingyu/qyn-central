# 从 Windows 开发机把 Agent 发到中控机（构建 → 传输 → 远端装配）。
#
#     pwsh -File CENTRAL/scripts/deploy-agent.ps1              # 全流程
#     pwsh -File CENTRAL/scripts/deploy-agent.ps1 -SkipUpload  # 只构建，不发
#     pwsh -File CENTRAL/scripts/deploy-agent.ps1 -StrictData  # 数据同名不同内容就停（默认放行）
#
# 远端只动 ~/qyn/agent 与 ~/qyn/.dsh；状态墙那套（~/qyn/current、wall.db、qyn-*.service）不碰。
#
# ★ 数据差异姿态：发布包里带的是**开发机** AGENT/data 快照（只用于真机首次播种），
#   而真机数据是唯一真源 —— 实测两边 data.json / plans.json 内容本来就不同。
#   所以默认传 QYN_DATA_DIVERGENCE=keep：只补真机缺的文件，**绝不覆盖**已有文件；
#   要人工比对差异时加 -StrictData（传 die），远端装配会在 2/10 停下（真机数据仍一个字节不动）。
#
# ★ 失败可回滚：远端 install-agent.sh 自己会在任何失败时把 current 切回旧 release 并打印旧路径。
#   这里在远端退出码非 0 时再打印「部署前 current」与「现在的 current」，确认线上指向（两者一致 = 已回滚）。
[CmdletBinding()]
param(
    [string]$Target = 'qyn-central',
    [switch]$SkipUpload,
    [switch]$StrictData
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $repo '.runtime\deploy-stage'
$tgz = Join-Path $repo ".runtime\qyn-agent-$ts.tar.gz"

Write-Host "`n== 1/3 构建发布包 ==" -ForegroundColor Cyan
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

# 要发的：内核与数据种子、技能、AGENTS.md、文档、部署件与 systemd 单元、手机通道。
# ★ CENTRAL\tools 与 CENTRAL\GUI\status-wall 必须发：install-agent.sh 的 9/10 要跑
#   中继白名单策略测试与状态墙端到端测试；少了它们，装配会直接把部署判失败。
$items = @(
    @{ s = 'AGENT';                   d = 'AGENT' },
    @{ s = '.dsh';                    d = '.dsh' },
    @{ s = 'AGENTS.md';               d = 'AGENTS.md' },
    @{ s = 'docs';                    d = 'docs' },
    @{ s = 'CENTRAL\deploy';          d = 'CENTRAL/deploy' },
    @{ s = 'CENTRAL\scripts';         d = 'CENTRAL/scripts' },
    @{ s = 'CENTRAL\tools';           d = 'CENTRAL/tools' },
    @{ s = 'CENTRAL\GUI\status-wall'; d = 'CENTRAL/GUI/status-wall' },
    # 硬件控制接口（屏幕 / 机箱灯光）：qyn-hw.py + 文档
    @{ s = 'CENTRAL\HARDWARE';        d = 'CENTRAL/HARDWARE' },
    @{ s = 'PHONE';                   d = 'PHONE' }
)
foreach ($it in $items) {
    if (-not (Test-Path $it.s)) { Write-Host "   · 跳过（不存在）: $($it.s)" -ForegroundColor DarkGray; continue }
    $dst = Join-Path $stage $it.d
    New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
    Copy-Item $it.s $dst -Recurse -Force
}
# 清运行时垃圾（数据本身要带上，作为首次播种）
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' -EA SilentlyContinue | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File -Include '*.pyc','*.bak','*.log' -EA SilentlyContinue | Remove-Item -Force
Get-ChildItem $stage -Recurse -Directory -Filter '.runtime' -EA SilentlyContinue | Remove-Item -Recurse -Force

# ★ PHONE 的运行时目录绝不能进发布包：
#   state/ 里是真实微信凭证（bot_token）与收件箱，logs/ 是常驻接收方的日志。
#   状态在目标机上外置（~/qyn/agent/phone-state），跨机器迁移靠单副本搬迁，不靠发布包。
$phoneStage = Join-Path $stage 'PHONE'
if (Test-Path $phoneStage) {
    foreach ($rel in @('state', 'logs')) {
        $p = Join-Path $phoneStage $rel
        if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "   · 排除 PHONE/$rel（运行时状态，绝不外发）" -ForegroundColor DarkGray }
    }
    Get-ChildItem $phoneStage -Recurse -Directory -Filter '*.egg-info' -EA SilentlyContinue | Remove-Item -Recurse -Force
}
# 兜底断言：发布包里一旦出现凭证文件就直接失败（静默泄漏比构建失败贵得多）
$leak = Get-ChildItem $stage -Recurse -Force -File -Filter 'credentials.json' -EA SilentlyContinue
if ($leak) {
    $where = ($leak | ForEach-Object { $_.FullName }) -join " ; "
    throw "发布包里出现 credentials.json —— 真实凭证绝不能离开开发机： $where"
}
Write-Host "   ✓ 已确认发布包里没有 credentials.json" -ForegroundColor Green

tar -czf $tgz -C $stage .
$sizeMB = [math]::Round((Get-Item $tgz).Length / 1MB, 1)
Write-Host "   ✓ $tgz ($sizeMB MB)" -ForegroundColor Green

if ($SkipUpload) { Write-Host "`n-SkipUpload：到此为止。" -ForegroundColor Yellow; return }

Write-Host "`n== 2/3 传到中控机 ==" -ForegroundColor Cyan
ssh $Target 'mkdir -p ~/qyn/agent/incoming' | Out-Null
scp -q $tgz "${Target}:~/qyn/agent/incoming/qyn-agent-$ts.tar.gz"
Write-Host "   ✓ 已上传" -ForegroundColor Green

# 读远端 current（**原样**软链目标，回滚基准）。
# ★ 用参数形式而不是把脚本管道喂给 `ssh bash -s`：PowerShell 5.1 把多行字符串写进
#   原生命令 stdin 时不可靠 —— 实测 `if … then … else … fi` 会被撕成"未预期的 EOF"，
#   而且**静默返回空串**（于是失败报告里会显示"（无）"，误导人）。
function Get-RemoteCurrent([string]$t) {
    $raw = ssh -o BatchMode=yes $t 'readlink $HOME/qyn/agent/current 2>/dev/null || echo none'
    $v = ($raw | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($v) -or $v -eq 'none') { return '（无）' }
    return $v
}

Write-Host "`n== 3/3 远端解包并装配（可能要几分钟：装 node + DSH CLI）==" -ForegroundColor Cyan
$before = Get-RemoteCurrent $Target
Write-Host "   部署前 current = $before" -ForegroundColor DarkGray
$div = if ($StrictData) { 'die' } else { 'keep' }
$remote = @"
set -e
cd ~/qyn/agent/incoming
rm -rf $ts && mkdir -p $ts
tar --warning=no-timestamp -xzf qyn-agent-$ts.tar.gz -C $ts
QYN_DATA_DIVERGENCE=$div bash $ts/CENTRAL/deploy/install-agent.sh ~/qyn/agent/incoming/$ts
"@
# ★ 远端写 stderr 不等于失败（tar 的时钟偏差警告就会写）—— 临时放开 Stop，只按退出码判
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
ssh $Target $remote 2>&1 | ForEach-Object { "   $_" }
$remoteCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($remoteCode -ne 0) {
    $after = Get-RemoteCurrent $Target
    Write-Host "`n部署失败：远端退出码 $remoteCode" -ForegroundColor Red
    Write-Host "   部署前 current = $before" -ForegroundColor Yellow
    Write-Host "   现在  current = $after" -ForegroundColor Yellow
    Write-Host "   （install-agent.sh 的 die 路径会自行回滚；上面两个值一致即已回到旧 release）" -ForegroundColor Yellow
    throw "远端装配失败（退出码 $remoteCode）—— 上面的输出就是原因"
}
$after = Get-RemoteCurrent $Target
Write-Host "`n部署完成。" -ForegroundColor Green
Write-Host "   current: $before  →  $after" -ForegroundColor Green
