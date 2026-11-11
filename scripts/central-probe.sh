#!/bin/sh
# central-probe.sh — 中控机**只读**体检：装了什么、缺什么、屏幕/会话什么状态。
# 不装、不改、不写任何文件。所有输出为 ASCII 标记 + 原文，便于跨编码传输。
#
# 用法（Windows 侧，脚本走 stdin 避开 PowerShell 引号处理）：
#   Get-Content -Raw CENTRAL/scripts/central-probe.sh | ssh qyn-central bash -s
#
# 为什么走 stdin：ssh 的参数要经 PowerShell → ssh → 远端 shell 三层解析，
# 双引号会被中间层吃掉（实测：python3 -c "..." 到远端就散了）。走 stdin 只剩远端一层。

echo "== os =="
. /etc/os-release 2>/dev/null
echo "$PRETTY_NAME"
uname -r

echo "== python / psutil =="
python3 -V 2>&1
python3 -c 'import psutil; print("psutil", psutil.__version__)' 2>&1 | tail -1

echo "== tools =="
for c in git pip3 pip chromium chromium-browser google-chrome mosquitto mosquitto_sub mosquitto_pub ipmitool curl wget unclutter fc-list xrandr; do
    printf '%-20s %s\n' "$c" "$(command -v "$c" || echo -)"
done

echo "== services =="
printf '%-20s %s\n' mosquitto "$(systemctl is-active mosquitto 2>&1)"
printf '%-20s %s\n' ssh "$(systemctl is-active ssh 2>&1)"

echo "== cjk fonts =="
fc-list :lang=zh 2>/dev/null | wc -l

echo "== display / session =="
echo "DISPLAY=${DISPLAY:-<empty>}"
echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-<empty>}"
ls /usr/share/xsessions 2>/dev/null | tr '\n' ' '
echo
loginctl list-sessions --no-legend 2>&1 | head -5

echo "== user services =="
printf '%-20s %s\n' 'systemctl --user' "$(systemctl --user is-system-running 2>&1)"
loginctl show-user user -p Linger 2>&1

echo "== network =="
if ping -c1 -W3 223.5.5.5 >/dev/null 2>&1; then echo "net-ok"; else echo "net-fail"; fi
getent hosts archive.ubuntu.com >/dev/null 2>&1 && echo "dns-ok" || echo "dns-fail"

echo "== load / mem / disk =="
uptime
free -m 2>/dev/null | head -2
df -h / | tail -1

echo "== screen modes =="
if command -v xrandr >/dev/null 2>&1 && [ -n "$DISPLAY" ]; then
    xrandr 2>/dev/null | grep -E ' connected|\*' || echo "(xrandr 无输出)"
else
    echo "(非 X 会话或无 xrandr)"
fi

echo "== existing qyn stuff =="
ls -d ~/qyn ~/qyn/* 2>/dev/null || echo "(无 ~/qyn)"
echo "-- venv --"
ls ~/qyn/status-wall/.venv/bin/python 2>/dev/null || echo "(无 status-wall venv)"
echo "-- systemd user units --"
ls ~/.config/systemd/user/ 2>/dev/null || echo "(无用户单元)"
echo "-- autostart --"
ls ~/.config/autostart/ 2>/dev/null || echo "(无 autostart)"
echo "-- listening ports --"
ss -tlnp 2>/dev/null | head -12

echo "== done =="
