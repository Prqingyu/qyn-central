# 一条命令把中控机上的 Agent 对话界面拉到你面前：
#     & CENTRAL\scripts\open-agent-ui.ps1
#
# 原理：DSH 拒绝 --host 0.0.0.0（would expose remote code execution to the network），
# 所以远程访问走标准做法 —— SSH 端口转发（什么也不暴露）。
[CmdletBinding()]
param([string]$Target = 'qyn-central', [int]$Port = 8799, [switch]$Open)
$ErrorActionPreference = 'Stop'

$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
ssh $Target 'export XDG_RUNTIME_DIR=/run/user/$(id -u); systemctl --user is-active qyn-agent-web.service >/dev/null || systemctl --user start qyn-agent-web.service; sleep 1; systemctl --user is-active qyn-agent-web.service' 2>&1 | ForEach-Object { "   $_" }
$ErrorActionPreference = $prev

$token = (ssh $Target 'grep -o "token=[A-Za-z0-9_-]*" ~/qyn/agent/logs/web.log | tail -1') -replace '^token=', ''
if (-not $token) { throw 'no token: see ~/qyn/agent/logs/web.log' }

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { Stop-Process -Id $busy.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
Start-Process -WindowStyle Hidden ssh -ArgumentList @('-N', '-o', 'ServerAliveInterval=30', '-o', 'ExitOnForwardFailure=yes', '-L', "${Port}:127.0.0.1:${Port}", $Target)
Start-Sleep -Seconds 4

$url = "http://127.0.0.1:$Port/?token=$token"
try { $r = Invoke-WebRequest -Uri $url -TimeoutSec 12 -UseBasicParsing; Write-Host "   tunnel ok (HTTP $($r.StatusCode))" -ForegroundColor Green }
catch { throw "tunnel failed: $($_.Exception.Message)" }

Write-Host "`n  打开这个地址，跟中控机上的 Agent 对话：" -ForegroundColor Green
Write-Host "  $url`n" -ForegroundColor White
if ($Open) { Start-Process $url }
