# 把中控机 Agent 的对话地址（带 token）塞进 Edge 收藏夹；token 变了自动更新。
#
#     & CENTRAL\scripts\qyn-agent-bookmark.ps1
#
# 设计成可以被**计划任务**反复调用：登录时跑一次、之后每 5 分钟跑一次。
# token 与上次相同 ⇒ 什么都不做（不折腾浏览器）。
#
# 为什么用隧道：DSH 拒绝 --host 0.0.0.0（would expose remote code execution to the network）。
param(
    [string]$Target = 'qyn-central',
    [int]$Port = 8799,
    [string]$Name = 'QYN Agent（中控机）',
    [switch]$Quiet,
    [int]$IntervalSec = 60,
    [switch]$Loop,
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$stateFile = Join-Path $env:LOCALAPPDATA 'QynAgent\bookmark.state'
$edgeBookmarks = Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data\Default\Bookmarks'
$favDir = Join-Path $env:USERPROFILE 'Favorites'
$desktop = [Environment]::GetFolderPath('Desktop')

function Say($m, $c = 'Gray') { if (-not $Quiet) { Write-Host "   $m" -ForegroundColor $c } }

function Sync-One {

# ── 1. 确保中控机上的 Web UI 在跑 ──
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
ssh $Target 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user is-active qyn-agent-web.service >/dev/null || systemctl --user start qyn-agent-web.service' 2>&1 | Out-Null
$ErrorActionPreference = $prev

# ── 2. 取当前 token ──
$token = (ssh $Target 'grep -o "token=[A-Za-z0-9_-]*" ~/qyn/agent/logs/web.log | tail -1') -replace '^token=', ''
if (-not $token) { throw '拿不到 token：看中控机 ~/qyn/agent/logs/web.log' }
$url = "http://127.0.0.1:$Port/?token=$token"

# ── 3. token 没变就不折腾 ──
$prevToken = ''
if (Test-Path $stateFile) { $prevToken = (Get-Content $stateFile -Raw).Trim() }
if ($prevToken -eq $token -and (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
    Say "token 未变，隧道在跑 —— 不用动" 'DarkGray'; return
}
if ($prevToken -eq $token) {
    # token 没变但隧道**没在跑**：不能直接 return（原来就是这样让隧道永远起不来），补起隧道再报
    Say "token 未变，但隧道没在跑 —— 补起" 'Yellow'
}

# ── 4. 隧道 ──
# ★ 隧道**归 `CENTRAL/scripts/qyn-agent-tunnel.ps1` 管**（常驻 + 断线自动重连 + 登录自启）。
#   这里只在"没有监听"时补起一份兜底，**绝不 Stop-Process 别人的监听** ——
#   否则两个管理者会互相杀（实测会形成抢 8799 的拉锯）。
if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -WindowStyle Hidden ssh -ArgumentList @('-N', '-o', 'ServerAliveInterval=30', '-o', 'ExitOnForwardFailure=yes', '-L', "${Port}:127.0.0.1:${Port}", $Target)
    Start-Sleep -Seconds 4
}
try { Invoke-WebRequest -Uri $url -TimeoutSec 12 -UseBasicParsing | Out-Null }
catch { throw "隧道没通：$($_.Exception.Message)" }
Say "隧道在跑" 'Green'

# ── 5. 写三处入口 ──
New-Item -ItemType Directory -Force (Split-Path $stateFile) | Out-Null
Set-Content -LiteralPath $stateFile -Value $token -NoNewline

# 5a. Favorites 下的 .url（Edge「导入收藏夹」认它；其它浏览器也认）
New-Item -ItemType Directory -Force $favDir | Out-Null
$urlFile = Join-Path $favDir "$Name.url"
Set-Content -LiteralPath $urlFile -Encoding ascii -Value @("[InternetShortcut]", "URL=$url", "IconIndex=0")

# 5b. 桌面快捷方式（立刻可点）
$lnk = Join-Path $desktop "$Name.url"
Copy-Item $urlFile $lnk -Force

# 5c. Edge 自己的收藏夹（Bookmarks JSON）—— 先备份，再按名字合并
if (Test-Path $edgeBookmarks) {
    Copy-Item $edgeBookmarks "$edgeBookmarks.bak-qyn" -Force
    try {
        $bk = Get-Content $edgeBookmarks -Raw -Encoding UTF8 | ConvertFrom-Json
        $bar = $bk.roots.bookmark_bar
        if (-not $bar.children) { $bar | Add-Member -NotePropertyName children -NotePropertyValue @() -Force }
        $hit = $bar.children | Where-Object { $_.name -eq $Name }
        if ($hit) {
            $hit.url = $url
            Say "Edge 收藏夹：已更新 $Name" 'Green'
        } else {
            $maxId = 0
            foreach ($c in $bar.children) { if ([int64]$c.id -gt $maxId) { $maxId = [int64]$c.id } }
            $bar.children += [pscustomobject]@{
                date_added = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * 1000 + 11644473600000000).ToString()
                guid = [guid]::NewGuid().ToString()
                id = ($maxId + 1).ToString()
                name = $Name
                type = 'url'
                url = $url
            }
            Say "Edge 收藏夹：已新增 $Name" 'Green'
        }
        $bk | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $edgeBookmarks -Encoding UTF8 -NoNewline
    } catch {
        Say "Edge 收藏夹写入失败（不影响其它入口）：$($_.Exception.Message)" 'Yellow'
    }
}

Say "地址：$url" 'White'

}   # ← Sync-One 结束

# ── -Install：写启动文件夹（登录自启）+ 立刻起一份常驻 ──────────────────────
# 与 CENTRAL/scripts/qyn-agent-tunnel.ps1 同一套做法（用户 2026-10-18「做成这样」）。
# 为什么需要：中控机的 Web UI 一重启 token 就变，收藏夹里的地址不刷新就"打得开但 token 无效"。
if ($Install) {
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    # ★★ 同隧道：用 **.vbs + wscript**（窗口模式 0 ⇒ 零窗口 ✓）。
    #   以前写的是 .cmd ⇒ Explorer 启动时**必然先开一个控制台** ⇒ 每次开机闪一个空黑窗 ✗
    #   （用户 2026-10-18 报的就是它）。
    $vbsPath = Join-Path $startup 'QYN-Bookmark-Sync.vbs'
    $ps1 = (Resolve-Path $PSCommandPath).Path
    $psExe = (Get-Command powershell.exe).Source
    $body = @"
' QYN bookmark sync - logon autostart. wscript runs PowerShell hidden: ZERO windows.
CreateObject("WScript.Shell").Run "$psExe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$ps1"" -Loop", 0, False
"@
    # 清掉旧版 .cmd（闪窗的就是它）
    $oldCmd = Join-Path $startup 'QYN-Bookmark-Sync.cmd'
    if (Test-Path $oldCmd) {
        try { Remove-Item $oldCmd -Force -ErrorAction Stop; Say "已删除旧 .cmd 启动项（它每次开机闪一个空窗口）" 'Yellow' }
        catch { Say "旧 .cmd 删不掉（可能仍被占用）：$($_.Exception.Message)" 'Red' }
    }
    # 清掉 Run 键里那份**重复**的常驻：启动文件夹已经有一份 -Loop 了，
    #   两份会各开一个 ssh 轮询/各写一次收藏夹 ✗（用户 2026-10-18：顺便去重）
    try {
        $rk = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        if (Get-ItemProperty -Path $rk -Name 'QYN-Agent-Bookmark' -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $rk -Name 'QYN-Agent-Bookmark' -ErrorAction Stop
            Say "已删除重复的 Run 键 QYN-Agent-Bookmark（启动文件夹那份已经在跑）" 'Yellow'
        }
    } catch { Say "Run 键没删掉：$($_.Exception.Message)" 'Red' }
    try {
        Set-Content -Path $vbsPath -Value $body -Encoding ASCII -ErrorAction Stop
        Say "已写入启动项：$vbsPath" 'Green'
    } catch {
        Say "写启动项失败：$($_.Exception.Message)" 'Red'
        exit 1
    }
    # 已经有常驻实例就别再起第二个（两个实例会抢着写 Edge 收藏夹）
    $busy = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'qyn-agent-bookmark\.ps1' -and $_.CommandLine -match '-Loop' }
    if ($busy) {
        Say "已有常驻实例（PID $($busy.ProcessId | Select-Object -First 1)），不重复启动" 'Yellow'
    } else {
        # ★ Start-Process（非阻塞）：用 & 会等常驻子进程、永不返回（隧道那边踩过）
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ps1, '-Loop') | Out-Null
        Say "已启动常驻（并已登记登录自启）" 'Green'
    }
    exit 0
}

# ── 主控：跑一次；带 -Loop 就每 IntervalSec 秒再跑一次 ──
# ★ 注意主体里"token 未变"走的是 return（只结束 Sync-One），不是 exit ——
#   上一版写成 exit 0，进程当场退出，循环根本跑不到（实测 12 秒就没了）。
Sync-One
if ($Loop) {
    while ($true) {
        Start-Sleep -Seconds $IntervalSec
        Sync-One
    }
}
