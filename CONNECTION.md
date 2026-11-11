# CENTRAL — 中控机连接手册

> 建立日期: 2026-09-14
> 状态: ✅ 已打通（免密 SSH）；✅ **2026-10-13 内网已有外网出口**（认证由路由器链承担，见 §3）
> 关联: [../WEBCONNECT/NETWORK_ARCHITECTURE.md](../WEBCONNECT/NETWORK_ARCHITECTURE.md)（网络架构）、[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)（信息中心总览）

---

## 1. 这台机器是什么

信息中心的**核心硬件**：24×7 在线的调度中枢 + 内网唯一出口。

| 项 | 值 |
|---|---|
| 主机名 | `CENTRAL` |
| 角色 | 网关 + 调度中枢 + 运维 Agent 宿主 |
| 主板 | **通用 ITX 主板
| 系统 | Ubuntu 桌面版（GNOME） |
| 内核 | 7.0.0-30-generic |
| 网卡 | `enp2s0`（单网卡，**未见到第二块网卡**） |
| MAC | `00:11:22:33:44:55` |

---

## 2. 连接方式

| 项 | 值 |
|---|---|
| 地址 | `127.0.0.1`（通用 AX 路由 局域网内，**DHCP 静态租约**绑定，见 §7） |
| 用户 | `user`（有 sudo） |
| 认证 | SSH 密钥，免密 |
| 私钥 | `~/.ssh/id_ed25519_qyn_central`（Windows 开发机） |
| 公钥注释 | `qyn-central-win-dev` |
| 别名 | `qyn-central` / `central` |

```bash
ssh qyn-central              # 直接登录
ssh central '命令'            # 远程执行
bash scripts/check.sh        # 一键自检（链路 + 端口 + 认证 + 远端状态）
```

`~/.ssh/config` 里配了 `ServerAliveInterval 30`，防止校园网 NAT 会话老化导致的静默断连。

---

## 3. ~~关键环境约束：局域网没有外网~~ ✅ **已解除（2026-10-13）**

> ✅ **2026-10-13 更新：内网已经有外网出口了**，本节整段约束**作废**。
>
> 现状（实测）：用户**刷了一台路由器**（路线 B）完成校园网接入，链路是
> `内网 192.0.2.10/24 → 192.0.2.10（OpenWrt 主路由）→ 192.0.2.10「router.local」→ 校园网`。
> - 从 Windows 以太网绑源测：`ping -S 192.0.2.10 223.5.5.5` = **44ms 通**
> - 中控机：`ping 223.5.5.5` 通、`getent hosts archive.ubuntu.com` 可解析
> - ⇒ **中控机可以直接 `apt install` / `pip install` 了，§9 里"装包必须走中继"那条作废**
> - 中控机仍是**单网卡纯客户端**（`enp2s0` = `127.0.0.1`，默认路由 `.1`）：**不跑 minieap、不做 NAT/DHCP**
> - 上游两台设备：**① 上游认证路由器，原厂 通用路由器，全千兆）
> - 🔴 **实测两层天花板**（闲时）：**校内 90.7 / 95.8 Mbps**（贴百兆线速），**出公网 ~50 Mbps**（校园网侧限速，
>   换设备也解决不了）。① 的 WAN 口是百兆 ⇒ 本地天花板 ≈94Mbps。
>   **规划按「出公网 ≈50Mbps」算**，别按官方 200Mbps 算
> - 详见 [../WEBCONNECT/FEASIBILITY.md](../WEBCONNECT/FEASIBILITY.md) §0.1（实测拓扑 + 证据）与 §0.2（变更单）
>
> ⚠️ 下面这段**保留作历史记录**（中继工具本身没坏，将来若内网再断外网仍可用）：

**通用 AX 路由 这个 192.0.2.10/24 网段没有互联网出口**（已实测：从以太网 ping 223.5.5.5 100% 丢包；校园 WiFi 才通）。
中控机的有线口也没做校园网认证客户端，尚未实施）。

**后果**：中控机**无法直接 `apt install`**。

### 解决办法：`tools/lan_http_proxy.py`

让有外网的 Windows 开发机当**中转**，一个端口干两件事：

```
中控机 (127.0.0.1) ──▶ Windows 中继 (192.0.2.10:8080) ──▶ archive.ubuntu.com
   无外网                      有外网（校园 WiFi）
```

| 用途 | 请求形态 | 行为 |
|---|---|---|
| apt 代理 | 绝对 URL（`GET http://archive.ubuntu.com/...`） | 转发到软件源 |
| 文件投递 | 普通路径（`GET /s`） | 发出 `tools/relay/` 下的文件 |

**安全约束**（刻意收紧，不是开放代理）：只绑局域网网卡、只接受中控机一个客户端、只转发软件源域名、文件投递有路径穿越防护。

**放宽白名单要显式开启**（默认姿态不变；策略有测试守着：`python tools/test_lan_http_proxy.py`）：

```bash
python tools/lan_http_proxy.py                              # 默认：仅 ubuntu.com / debian.org
python tools/lan_http_proxy.py --install-mode               # + nodejs.org / npm / pypi（装 Node、DSH CLI、pip 包）
python tools/lan_http_proxy.py --allow-host api.deepseek.com # 按需放行单个域名，可重复
```

> `--install-mode` 是**临时**手段：装完立刻关掉，不要长期挂着。

**用法**：

```bash
# Windows 侧启动（保持前台运行，用完 Ctrl-C）
python tools/lan_http_proxy.py

# 中控机侧装包（sudo -i 后设代理；sudo -i 是必须的，见 §5）
sudo -i
export http_proxy=http://192.0.2.10:8080
export https_proxy=$http_proxy
apt update && apt install -y <包名>
exit
```

> ⚠️ **中继地址会漂，别照抄本文里的 IP**。Windows 开发机的以太网是 DHCP 动态地址
> （实测从 `.3` 漂到 `.6`，见 §9）。上面所有 `192.0.2.10` 都只是**当次**的值：
> - `tools/lan_http_proxy.py` 启动时会打印它实际绑定的地址，照那个填；
> - `tools/relay/m`、`tools/relay/d` 支持 `QYN_RELAY` 环境变量指定中继地址，
>   并在动手前先探测可达性（不可达就直接报错，不让 apt 挂到超时）。

> ⚠️ **`http_proxy` 设了之后，取本地文件也会走代理**。这一点已在中继里处理（指向自身的绝对 URL 会当作本机文件），所以两种用法可以共存，不用来回 unset。

### 防火墙

Windows 侧需要一条入站放行规则（**需管理员**，用完建议删除）：

```
netsh advfirewall firewall add rule name="QYN-CENTRAL-APT-RELAY" ^
  dir=in action=allow protocol=TCP localport=8080 remoteip=127.0.0.1 profile=any

netsh advfirewall firewall delete rule name="QYN-CENTRAL-APT-RELAY"
```

---

## 4. 排错顺序

按这个顺序查，能快速定位是哪一层断了（`scripts/check.sh` 就是按这个顺序做的）：

| 层 | 检查 | 常见原因 |
|---|---|---|
| 1. 链路 | `ping 127.0.0.1` | 中控机没开机 / 网线松 / IP 变了 |
| 2. 端口 | `/dev/tcp/127.0.0.1/22` | sshd 停了：`ssh qyn-central 'systemctl status ssh'`（要先能连上…） |
| 3. 认证 | `ssh -v qyn-central` | 公钥没装 / 权限不对（`~/.ssh` 700、`authorized_keys` 600） |
| 4. 远端 | `uptime` / `systemctl is-active ssh` | 服务异常 |

**踩过的坑（真实教训）**：

- **别用 `/dev/tcp` 扫端口做判断**。实战中它在 127.0.0.1 上报过 25/110/143 三个"开放"端口，而主机侧 `ss -tlnp` 证明**那些端口根本没监听**——全是误报。**以主机侧 `ss -tlnp` 为准。**
- **多行命令块不要直接粘**。`. /etc/os-release` 这种以 `. ` 开头的行，粘到终端会变成 `./etc/os-release` 去执行，报"没有那个文件或目录"。给中控机的命令要**单行、可手打**。
- **默认不装 `curl`**。这台 Ubuntu 桌面版没有 curl，也没有 wget。`python3` 一定有。

---

## 5. 已做的变更清单（可回溯）

| 位置 | 变更 |
|---|---|
| 中控机 `/home/user/.ssh/authorized_keys` | 追加 Windows 开发机公钥 |
| 中控机 | 安装 `openssh-server 1:10.2p1-2ubuntu3.6`，`systemctl enable --now ssh` |
| Windows `~/.ssh/id_ed25519_qyn_central{,.pub}` | 新建专用密钥对 |
| Windows `~/.ssh/config` | 新增 `qyn-central` / `central` 两个别名 |
| Windows 防火墙 | 新增 `QYN-CENTRAL-APT-RELAY` 入站规则（**临时，用完删**） |
| Windows `~/.ssh/known_hosts` | 收录 `127.0.0.1` 的 ED25519 主机密钥 |

> 关于 `sudo -i`：中控机上 `sudo` 默认 `env_reset`，`sudo -E` 不一定能透传 `http_proxy`。**先进 root 再 export** 是唯一稳妥、且最好敲的写法。

---

## 6. ⚠️ 待修正的文档错误

实测推翻了架构文档里的两处硬件描述，需回头改 `AGENT/` 和 `WEBCONNECT/` 的文档：

| 文档原写法 | 实测 |
|---|---|
| 中控机 = 通用 ITX 主板 / 通用 ITX 主板 | ❌ **中控机是通用 ITX 主板**；通用主板那台是 **Minecraft 服务器**（2026-11-10 起该机跑 **Proxmox VE**，MC 是它上面的 VM，宿主地址实测 `.121`） |
| 中控机「双板载网卡」 | ❓ `ip -br addr` 只看到 `enp2s0` **一块**网卡，需确认 |

另外 `NETWORK_ARCHITECTURE.md` 规划中控机装 **Debian**，实际装的是 **Ubuntu 桌面版**——作为 24×7 无头网关，桌面环境（GNOME）白吃资源，后续可评估换 Ubuntu Server 或精简。

---

## 7. 地址固定：DHCP 静态租约

中控机的地址**不是**主机侧静态配置，而是路由器侧的 **DHCP 地址保留**：

| 项 | 值 |
|---|---|
| 路由器 | 通用无线路由器（管理页 `192.0.2.10`） |
| 配置位置 | 高级设置 → **静态地址分配** |
| MAC | `00:11:22:33:44:55` |
| 保留 IP | `127.0.0.1` |
| DHCP 池 | `192.0.2.10 – 192.0.2.10`（**
| 中控机侧 | `nmcli` 显示 `ipv4.method=auto`（DHCP），**无需改动** |

> **为什么不用主机静态 IP**：DHCP 池覆盖了 `.2–.254` 全部可用地址，主机侧设静态迟早会和路由器分出去的地址撞车。由路由器保留是无冲突的唯一做法。

**验证（2026-09-15 通过）**：`sudo reboot` 后 —— 地址仍为 `127.0.0.1`、MAC 不变、sshd 自动起来、密钥登录照常。四层全绿，可靠性闭环。

---

## 8. 文件传输通道（SMB）

Windows 侧零安装 —— SMB 是 Windows 原生协议，体验等同于本地磁盘。

| 项 | 值 |
|---|---|
| 访问地址 | `\\127.0.0.1\share` |
| 映射盘符 | **`S:`**（已映射，`/persistent:yes` 持久） |
| 中控机侧路径 | `/home/user/share` |
| 认证 | SMB **独立密码**（`sudo smbpasswd -a user`），与 Linux 登录密码无关 |
| 服务 | `smbd`，已 `enable` 开机自启 |
| 配置位置 | `/etc/samba/smb.conf` 末尾的 `[share]` 段，带 `# --- QYN-CENTRAL share (managed) ---` 标记（脚本幂等，重复执行不会重复追加） |
| 投放脚本 | `tools/relay/m`（经中继执行，一键装+配） |

**已实测**：Windows 写入 → 中控机落地（属主自动为 `user`，权限 `0644`）→ 中文内容回读无乱码 → 删除正常。

**三个已知现象，都不是故障**：

- `net view \\127.0.0.1` 报 **1702**（RPC 绑定失败）—— Samba 默认禁止匿名枚举共享列表，不影响实际访问。
- **首次连接**如果没弹凭据框、直接报"找不到网络路径"—— 是 Windows 缓存了失败的 SMB 会话。
- **过夜/重启后 `S:` 掉线**—— 凭据没持久化，见下。

### ⚠️ 凭据必须用「记住我的凭据」存，不要用 cmdkey /add

2026-09-15 踩过的三个坑，按错误程度排序：

| 做法 | 结果 |
|---|---|
| `net use ... /user:user *` | 只认证**当次会话**，不写凭据库 → 过一夜 `S:` 就掉 |
| 管理员终端里 `cmdkey /add:127.0.0.1 ...` | 存成 **`类型: 本地计算机`**（机器级）→ 用户会话的 SMB 重定向器**读不到它**，每次连接照样弹凭据框；非交互环境下弹不出来就报 `系统错误 1223 操作已被用户取消` |
| ✅ **资源管理器连接时勾选「记住我的凭据」** | 存成 **`Domain:target=127.0.0.1`（`类型: 域`）** → 重定向器正常读取，**实测可用** |

**正确流程**：

1. 资源管理器地址栏输 `\\127.0.0.1\share`
2. 弹凭据框 → 用户名 `user` + SMB 密码 → **务必勾选「记住我的凭据」**
3. 映射盘符（**普通终端，不要用管理员**）：

```
net use S: \\127.0.0.1\share /persistent:yes
```

**怎么判断凭据存对没有**：

```
cmdkey /list
```

必须是 `Domain:target=127.0.0.1` + `类型: 域`。若是 `类型: 本地计算机`，删掉重来。

> 另注：SSHFS-Win 这条路**已验证走不通**——其二进制停留在 2020-12（winget 包名「SSHFS-Win 2021」），与 WinFsp 2025 不兼容，认证成功后卡在 `dup() in/out/err failed`。别在这上面再花时间。

---

## 9. 下一步

- [x] ~~局域网无外网 → 后续所有装包都要走 §3 的中继~~ ⇒ **2026-10-13 出口已打通，中控机可直接 `apt`/`pip`**
- [x] ~~按 NETWORK_ARCHITECTURE.md 推进 P2（minieap 认证）~~ ⇒ **不需要了**：认证由路由器链承担（路线 B），中控机保持纯客户端
- [ ] **删除临时防火墙规则 `QYN-CENTRAL-APT-RELAY`**（需管理员）——⚠️ 但**中继脚本别删**：出口是单点
      （上游路由器，一旦断网它就是唯一的装包手段，届时临时把规则加回来即可（命令见 §3）
- [x] ~~搞清认证跑在哪台路由器上~~ ⇒ **已确认：上游上游认证路由器，其 WAN 接校园网、LAN 接 通用有线路由 的 WAN
- [ ] **给出口加「掉线探测告警」**——出口串了两台路由器（认证点 + 主路由），**目前没有任何监测**，这是当前最大的运维缺口
- [ ] **导出两台路由器的配置备份**、记下 WAN 口 MAC 与固件版本（刷机丢配置 = 全家断网）
- [ ] 检查两台路由器管理面：WAN 侧远程管理 / UPnP / DMZ 一律关
- [ ] **给 Windows 开发机也做 DHCP 保留** —— 它的以太网 IP 是动态的（2026-09-15 从 `.3` 漂到 `.6`）
- [ ] 确认 `192.0.2.10` 是谁、`.20` / `.40` 为何不响应（状态墙节点矩阵要对得上）
- [ ] 修正 §6 记录的文档错误

> 上述 4 项（认证定位 / 探测告警 / 配置备份 / 管理面检查）与 [../WEBCONNECT/FEASIBILITY.md](../WEBCONNECT/FEASIBILITY.md) §0.2 变更单是同一份清单。
