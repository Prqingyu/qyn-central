# QYN Agent Web UI 的本地隧道（自动重连、开机自启）
#
# 为什么需要它：中控机上的 DSH Web UI 只监听 127.0.0.1:8799（它拒绝 --host 0.0.0.0，
# 原话是会把远程代码执行暴露到网络上）。所以要在这台 Windows 上开一条 SSH 隧道：
#
#     ssh -N -L 8799:127.0.0.1:8799 qyn-central
#
# 然后浏览器访问 http://127.0.0.1:8799/?token=<中控机 ~/qyn/agent/logs/web.log 里的 token>
# （收藏夹里那个「QYN Agent（中控机）」由 CENTRAL/scripts/qyn-agent-bookmark.ps1 -Loop 自动更新 token。）
#
# ★ 用户 2026-10-18 报「收藏夹那个链接失效」—— 根因就是**隧道没开**（8799 没有监听），
#   而不是 token 过期。手动开一次关掉终端就断，所以这里做成常驻 + 自动重连 + 开机自启。
#
# 用法：
#   pwsh -File CENTRAL\scripts\qyn-agent-tunnel.ps1            # 前台常驻（Ctrl-C 退出）
#   pwsh -File CENTRAL\scripts\qyn-agent-tunnel.ps1 -Once     # 只连一次（调试）
#   pwsh -File CENTRAL\scripts\qyn-agent-tunnel.ps1 -Install  # 注册为登录时自启的计划任务

[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Once,
    [string]$Host_ = 'qyn-central',
    [int]$Port = 8799
)

$ErrorActionPreference = 'Continue'
$log = Join-Path $env:LOCALAPPDATA 'qyn-tunnel.log'
function Log([string]$m) {
    $line = "{0}  {1}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $m
    $line | Tee-Object -FilePath $log -Append | Out-Null
    Write-Host $line
}

if ($Install) {
    # ★ 用**启动文件夹**而不是计划任务：实测 Register-ScheduledTask 在这台机器上
    #   Access is denied（0x80070005），而它默认不抛错 ⇒ 会谎报"已注册"。
    #   启动文件夹不需要管理员、登录即启，够用。
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    # ★★ 用 **.vbs + wscript**，不要 .cmd（用户 2026-10-18：每次开机都弹一个空黑窗 ✗）：
    #   .cmd 由 Explorer 启动时**必然先开一个控制台**（`-WindowStyle Hidden` 只藏得住它
    #   转手拉起的 PowerShell，藏不住 conhost 自己）⇒ 登录时闪一个空白窗口。
    #   wscript 以窗口模式 0 运行 ⇒ **窗口数 = 0** ✓（只有 wscript 进程，无窗口）。
    $vbsPath = Join-Path $startup 'QYN-Agent-Tunnel.vbs'
    $ps1 = (Resolve-Path $PSCommandPath).Path
    $psExe = (Get-Command powershell.exe).Source
    $body = @"
' QYN tunnel - logon autostart. wscript runs PowerShell hidden: ZERO windows.
CreateObject("WScript.Shell").Run "$psExe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$ps1""", 0, False
"@
    # 清掉旧版 .cmd 启动项（闪窗的就是它）
    $oldCmd = Join-Path $startup 'QYN-Agent-Tunnel.cmd'
    if (Test-Path $oldCmd) {
        try { Remove-Item $oldCmd -Force -ErrorAction Stop; Log "已删除旧 .cmd 启动项（它每次开机闪一个空窗口）" }
        catch { Log "旧 .cmd 删不掉（可能仍被占用）：$($_.Exception.Message)" }
    }
    try {
        Set-Content -Path $vbsPath -Value $body -Encoding ASCII -ErrorAction Stop
        Log "已写入启动项：$vbsPath"
    } catch {
        Log "写启动项失败：$($_.Exception.Message)"
        exit 1
    }
    # 单实例：已经有常驻隧道就别再起第二个（两个 ssh 抢 8799，第二个永远重试）
    # ★ 判据要排除两类误判：自己（$PID）与**调用者**（它的命令行也含脚本名 + -Install）
    $others = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and
                       $_.CommandLine -match 'qyn-agent-tunnel\.ps1' -and
                       $_.CommandLine -notmatch '-Install' }
    if ($others) {
        Log "已有常驻隧道实例（PID $($others.ProcessId -join ','))，不重复启动"
        exit 0
    }
    # 立刻起一份（不管登录与否，现在就要能用）
    # ★ 必须 **Start-Process**（非阻塞）：用 `&` 会等子进程，而隧道是常驻的
    #   ⇒ -Install 永远不返回（实测把我的命令挂了 8 分钟）。又一个「看着成功、其实卡住」。
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ps1) | Out-Null
    Log "已启动隧道进程（并已登记登录自启）"
    exit 0
}

# 单实例守卫：本实例之外若已有隧道脚本在跑，直接退出。
$peers = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and
                   $_.CommandLine -match 'qyn-agent-tunnel\.ps1' -and
                   $_.CommandLine -notmatch '-Install' }
if ($peers -and -not $Once) {
    Log "已有实例在跑（PID $($peers.ProcessId -join ','))，本实例退出"
    exit 0
}

$common = @(
    '-o', 'BatchMode=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-N', "-L", "${Port}:127.0.0.1:${Port}", $Host_
)

while ($true) {
    Log "连接隧道 ${Port} -> ${Host_}:${Port}"
    & ssh @common 2>&1 | ForEach-Object { Log "  ssh: $_" }
    $code = $LASTEXITCODE
    Log "隧道断开（ssh 退出码 $code）"
    if ($Once) { exit $code }
    # ★ 竞态兜底：如果已经有**别的**实例在跑（两次 -Install 几乎同时起时会发生），
    #   本实例就该让位 —— 否则输的那个 ssh 永远抢不到 8799、每 5 秒重连一次刷日志。
    $peers = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and
                       $_.CommandLine -match 'qyn-agent-tunnel\.ps1' -and
                       $_.CommandLine -notmatch '-Install' }
    if ($peers) {
        Log "已有别的实例在跑（PID $($peers.ProcessId -join ','))，本实例让位退出"
        exit 0
    }
    Start-Sleep -Seconds 5
}
