#!/bin/bash
# SERVER（Proxmox VE 宿主机）采集器 —— 一键安装
#
# 在 **PVE 主机** 上以 root 执行（不是工作站 ✗）：
#     wget -qO- http://127.0.0.1:8099/install-proxmox-collect.sh | bash
#
# 它会：① 装采集器 ② 干跑一次给你看 JSON ③ 装 5 秒定时器 ④ 打印第一帧推送结果
set -euo pipefail

SRC="${SRC:-http://127.0.0.1:8099}"

echo "== 1/4 检查能不能连到中控 =="
if ! wget -q --spider --timeout=5 "$SRC/proxmox-collect.py"; then
  echo "✗ 连不上 $SRC —— 这台 PVE 到中控（127.0.0.1）不通？"
  echo "  先测： wget -qO- http://127.0.0.1:8080/api/state | head -c 80"
  exit 1
fi
echo "  ✓ 可以拉文件"

echo "== 2/4 安装采集器 =="
mkdir -p /root/qyn
wget -qO /root/qyn/proxmox-collect.py "$SRC/proxmox-collect.py"
chmod 0755 /root/qyn/proxmox-collect.py
echo "  ✓ /root/qyn/proxmox-collect.py ($(stat -c%s /root/qyn/proxmox-collect.py) B)"

echo "== 3/4 干跑（只打印，不推送）=="
python3 /root/qyn/proxmox-collect.py --once --print --no-push | head -24

echo "== 4/4 安装定时器 =="
wget -qO /etc/systemd/system/qyn-proxmox-collect.service "$SRC/qyn-proxmox-collect.service"
wget -qO /etc/systemd/system/qyn-proxmox-collect.timer "$SRC/qyn-proxmox-collect.timer"
systemctl daemon-reload
systemctl enable --now qyn-proxmox-collect.timer
echo "  ✓ 定时器已启用（每 5 秒一帧，和墙上其它机器一致 ✓）"

# 立刻手动跑一次，确认能推到墙上
systemctl start qyn-proxmox-collect.service || true
sleep 3
echo
echo "== 最近一次推送 =="
journalctl -u qyn-proxmox-collect.service -n 6 --no-pager || true
echo
echo "== 定时器状态 =="
systemctl list-timers qyn-proxmox-collect.timer --no-pager || true
echo
echo "完成 ✓ 墙上 SERVER 卡应该 15 秒内变成 OL，显示 CPU / TEMP / RAM 三个状态。"
