#!/usr/bin/env bash
# 信息中心 · 中控机连接自检
# 在 Windows 开发机的 Git Bash 里运行：bash scripts/check.sh [别名]
set -u

HOST="${1:-qyn-central}"
IP=127.0.0.1
fail=0

echo "=== 信息中心 · 中控机连接自检 ==="
echo "目标: $HOST ($IP)"
echo

# 1) 链路层
if ping -n 1 -w 2000 "$IP" >/dev/null 2>&1; then
    echo "[ ok ] 链路: ICMP 可达"
else
    echo "[FAIL] 链路: ICMP 不可达（中控机没开机？网线松了？IP 变了？）"
    fail=1
fi

# 2) SSH 端口
if timeout 5 bash -c "echo > /dev/tcp/$IP/22" 2>/dev/null; then
    echo "[ ok ] 端口: 22 开放"
else
    echo "[FAIL] 端口: 22 不通（sshd 停了？防火墙改了？）"
    fail=1
fi

# 3) 免密登录 + 远端状态
if out=$(ssh -o ConnectTimeout=8 -o BatchMode=yes "$HOST" \
        'echo "$(hostname)|$(whoami)|$(uname -r)|$(uptime -p)|$(systemctl is-active ssh)"' 2>&1); then
    IFS='|' read -r h u k up s <<<"$out"
    echo "[ ok ] 认证: 免密登录成功"
    echo "       主机 = $h    用户 = $u"
    echo "       内核 = $k"
    echo "       运行 = $up"
    echo "       sshd = $s"
    if [ "$s" != "active" ]; then
        echo "[warn] sshd 不是 active 状态"
        fail=1
    fi
else
    echo "[FAIL] 认证: $out"
    fail=1
fi

echo
if [ "$fail" = 0 ]; then
    echo "结论: 连接正常"
else
    echo "结论: 存在问题，见上方 [FAIL]"
fi
exit "$fail"
