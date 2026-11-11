# SERVER（Proxmox VE 宿主机）状态采集

> **★ 2026-11-10 起改成「墙主动拉 PVE API」** ✓ —— 服务器上**不再装探针** ✓（零日志 ✓）。
>
> | | 方案 |
> |---|---|
> | **现在（主用）** | `CENTRAL/scripts/pve-api-pull.py` 跑在**中控**上，每 5 秒拉 `https://<PVE>:8006/api2/json/nodes/<node>/status` ✓ 推给墙 |
> | 旧方案（停用 ✗） | `proxmox-collect.py` + timer 跑在 PVE 上 —— 每 5 秒一行 journal ⇒ 一天 1.7 万行 ✗ 把日志塞满 ✗ |
>
> 新方案要点：只读 API Token（`PVEAuditor` ✓ 最小权限 ✓）、`~/.config/qyn/pve-token`（600 ✓）、
> 中控 user service `qyn-pve-pull.service`（`--interval 5` ✓ 只在第一帧/出错时记日志 ✓）。
> **温度永远是 `null`** ✓（PVE REST API 不提供 CPU 温度 ✗ 墙上显示 `—` ✓ 不是故障 ✓）。
>
> 首次配置（已做过 ✓ 留档）：
> ```bash
> # PVE 上：建只读 token（Datacenter → Permissions → API Tokens → Add，Privilege Separation 关或授角色）
> # 然后给用户授只读角色（必须 --propagate 1 ✓ 否则 /nodes/<node> 仍 403 ✗）
> pveum acl modify / --users QYN@pam --roles PVEAuditor --propagate 1
> # 中控上：写 token + 测通 + 起服务
> python3 pve-api-pull.py --set-token 'QYN@pam!wall=<secret>'
> python3 pve-api-pull.py --test
> systemctl --user enable --now qyn-pve-pull.service
> ```
>
> 下面的内容保留作**旧探针**的档案 ✓（万一 API 方案不可用时回退）。

---

# SERVER（Proxmox VE 宿主机）采集器

> 2026-11-08：SERVER（`192.0.2.10`，墙上节点 id = `mc`）装了 **Proxmox VE**，
> 而且是**完全虚拟化**的 —— 宿主机本身不跑业务，所有东西都在 VM / 容器里。
> **2026-11-10 补记**：**Minecraft 服务器已完成虚拟化，就是这台 PVE 上的 VM**（不再占裸机）；
> 该宿主地址那天实测已从 `.7` **漂到 `192.0.2.10`**（DHCP 无保留）⇒ 建议给它做静态保留。
>
> ⇒ 宿主机只需要回答一个问题：**"这台机器还活着吗、累不累"** ✓
> ⇒ 所以**只采三个状态**：**CPU% / CPU 温度 / 内存占用** ✓
> ⇒ 不采 VM 列表、不采存储池（那些对"宿主机健康"没有增量信息 ✗）

## 一、采集器

`CENTRAL/scripts/proxmox-collect.py` —— 单文件，**只读 `/proc` 和 `/sys`**：

| 状态 | 来源 | 说明 |
|---|---|---|
| **CPU%** | `/proc/stat` 两次差值（0.6s 窗口） | 宿主机整体 CPU |
| **TEMP** | `/sys/class/thermal/thermal_zone*/temp` 取最高 | 读不到就 `null`（不拿 0 冒充 ✓） |
| **RAM** | `/proc/meminfo`（`MemTotal - MemAvailable`） | 不用 `MemFree` ✗（那会把 cache 算成占用） |

顺带（契约要求 / 墙上本来就显示）：
- `disk` = 根分区占用（`statvfs` ✓）
- `net.latency_ms` = ping `192.0.2.10`（和别的机器同一参照物 ✓）
- `extra.uptime_s` = 运行时长
- `platform` = 自动识别 `Proxmox VE 8.x`（读 `/etc/pve/.version` ✓）

**零依赖** ✓：不需要 `psutil` ✗、不需要 `pvesh` ✗、不需要 API token ✗ —— 纯标准库 + `/proc`。

## 二、安装（在 **PVE 主机** 上，root）⚠️ 不是工作站 ✗

> 2026-11-08 踩过：把"工作站上的 scp 命令"直接贴进 PVE 控制台 ✗ ⇒ 报
> `No such file or directory`（`CENTRAL/scripts/...` 是工作站的相对路径 ✓）。
> **正确做法**：在 PVE 主机上，从**中控**拉文件（PVE 能到墙 ⇒ 一般也能到中控 ✓）。

### 一键装（推荐）

```bash
# 0) 先确认能连到中控（能出 JSON 就是通 ✓）
wget -qO- http://127.0.0.1:8080/api/state | head -c 80; echo

# 1) 一键安装（装采集器 + 干跑 + 装 **5 秒**定时器 + 打印第一帧）
wget -qO- http://127.0.0.1:8099/install-proxmox-collect.sh | bash
```

### 手工装（想逐步来）

```bash
mkdir -p /root/qyn
wget -qO /root/qyn/proxmox-collect.py              http://127.0.0.1:8099/proxmox-collect.py
wget -qO /etc/systemd/system/qyn-proxmox-collect.service http://127.0.0.1:8099/qyn-proxmox-collect.service
wget -qO /etc/systemd/system/qyn-proxmox-collect.timer   http://127.0.0.1:8099/qyn-proxmox-collect.timer
chmod 0755 /root/qyn/proxmox-collect.py

python3 /root/qyn/proxmox-collect.py --once --print --no-push | head -20   # 干跑
python3 /root/qyn/proxmox-collect.py --once                                # 推一帧
systemctl daemon-reload && systemctl enable --now qyn-proxmox-collect.timer
journalctl -u qyn-proxmox-collect.service -n 20 --no-pager
```

> 如果 PVE 到中控 **不通** ✗（`wget` 卡住/超时）：
> ① 在工作站上 `scp CENTRAL/scripts/proxmox-collect.py root@<PVE-IP>:/root/qyn/`（需要 PVE 的 22 端口可达）；
> ② 或把文件 base64 后粘进 PVE 控制台：
> `echo '<base64>' | base64 -d > /root/qyn/proxmox-collect.py`。

⚠️ 旧的裸机采集器要在 SERVER 上**停掉** ✓（`systemctl disable --now qyn-collect` 或对应服务 ✓），
否则两套会互相覆盖 ✓。

## 三、墙上会变成什么样

SERVER 卡和别的机器**完全一样**（三个状态 ✓ 不多不少 ✓）：

```
SERVER                    OL · 3s      PVE · 192.0.2.10
CPU   19     多线程
TEMP  47
RAM   34     11.0 / 32.0 GB
```

## 三·五、日志策略（★ 2026-11-10 用户：探针把日志塞满了 ✗）

5 秒一帧 × 每帧一行 = **一天约 1.7 万行** ✗ ⇒ 现在改成：

| 位置 | 策略 |
|---|---|
| 采集器 | **默认只报错** ✓（第一帧报一次 ✓；想每帧都报加 `--verbose` ✓） |
| systemd unit | `StandardOutput=null` + `StandardError=journal` ✓ 双保险 |
| journald | **务必设上限**（见下 ✓ 否则任何服务都能塞满 ✗） |

### 立刻止血（不用换文件 ✓ 30 秒）

```bash
mkdir -p /etc/systemd/system/qyn-proxmox-collect.service.d
printf '[Service]\nStandardOutput=null\n' > /etc/systemd/system/qyn-proxmox-collect.service.d/quiet.conf
systemctl daemon-reload
journalctl --vacuum-size=100M          # 清一次历史日志
journalctl -u qyn-proxmox-collect.service --since "10 min ago" | wc -l   # 之后应该只有几行 ✓
```

### 给 journal 设上限（根治 ✓）

```bash
# /etc/systemd/journald.conf 里改：
#   SystemMaxUse=200M
#   SystemMaxFileSize=20M
systemctl restart systemd-journald
```

### 换成静默版采集器

```bash
wget -qO /root/qyn/proxmox-collect.py              http://127.0.0.1:8099/proxmox-collect.py
wget -qO /etc/systemd/system/qyn-proxmox-collect.service http://127.0.0.1:8099/qyn-proxmox-collect.service
systemctl daemon-reload && systemctl restart qyn-proxmox-collect.timer
```

## 三·六、另一条路：墙直接调 PVE API（零探针）

实测**中控能连到** PVE API（`192.0.2.10:8006` ✓）⇒ 也可以**不在服务器上装任何东西** ✓，
让状态墙每 5 秒自己拉：

```
GET https://192.0.2.10:8006/api2/json/nodes/pve-node-1/status
     Authorization: PVEAPIToken=qyn@pve!wall=<secret>
```

- 在 PVE 上建 **API Token**：Datacenter → Permissions → API Tokens → 用户 `qyn@pve` →
  角色给只读（`PVEAuditor` ✓）→ 复制 token
- 优点：**PVE 上零探针、零日志** ✓
- 缺点：token 要保管 ✓；墙必须能一直连到 8006 ✓

需要我把这条 poller 写进状态墙，说一声 ✓（建好 token 给我即可）。

## 四、排错

| 现象 | 原因 / 处理 |
|---|---|
| 温度一直是 `—` | 宿主机没有 thermal_zone（很多服务器板子没暴露 ✓）—— 正常，不是故障 |
| 墙上一直 `OFFLINE` | 采集器没跑：`systemctl status qyn-proxmox-collect.timer` |
| 墙上**反复** LOST/OFFLINE 闪烁 | 推频太慢：墙是 **10s 算 LOST / 15s 算 OFFLINE** ⇒ 必须 `OnUnitActiveSec=5s` ✓（原来是 15s ✗ 2026-11-08 已改） |
| `platform` 显示成 `Debian GNU/Linux` | `/etc/pve/.version` 读不到（不是 root 或没挂 pmxcfs）—— 不影响三个状态 |
| CPU% 一直 0 | `/proc/stat` 读不到（极少见）—— 检查权限 |

---

*2026-11-08 · 采集器 `CENTRAL/scripts/proxmox-collect.py` · 单元 `CENTRAL/scripts/systemd/qyn-proxmox-collect.{service,timer}`*
