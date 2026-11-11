# 状态墙 — 综合信息显示屏幕 架构

> 版本: v1.0（架构定稿）
> 日期: 2026-09-15
> 状态: 架构已定，代码未写
> 关联: [CONNECTION.md](CONNECTION.md)（本机连接）、[../WEBCONNECT/NETWORK_ARCHITECTURE.md](../WEBCONNECT/NETWORK_ARCHITECTURE.md)（端口规划 §6）、[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)（事件总线/运维 Agent）

---

## 1. 目标

中控机挂着一块 **HDMI 1600×900** 显示器，把它变成信息中心的常驻状态看板：各节点在线状态、CPU/内存、网络流量等。

**这不是新项目**——它就是 WEBCONNECT §6 里规划的那个 **「Web 仪表盘 :8080」**，同一个产物。中控机上全屏 kiosk 显示，别的机器用浏览器也能看。

---

## 2. 已定决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据通道 | **MQTT 消息中枢**（mosquitto :1883） | 支持 retained 消息与 **LWT（遗嘱）**，多节点扩展，与 WEBCONNECT 规划一致 |
| 展示方式 | **浏览器 kiosk**（本地 HTML + Chromium 全屏） | 迭代最快；同一页面远程也能看；就是规划里的那个仪表盘 |
| kiosk 环境 | **保留 GNOME**，加自动登录 + 自启 | 改动最小，临时想用桌面也方便 |

---

## 3. 四层架构

```
┌─ 采集层 ──────────────────────────────────────────────────┐
│  中控机      本地采集 (psutil 7.1.0 已装)   7×24           │
│  Windows机   轻量 exporter  ──推──┐         按需开机        │
│  存储工作站  exporter       ──推──┤         按需(BMC唤醒)   │
│  MC 服务器   轻量心跳        ──推──┤         7×24，非自有   │
│  BMC(.4)     中控机 IPMI 轮询 ◄───┘         常在线（唯一"拉")│
└──────────────────────┬───────────────────────────────────┘
                       │  统一指标模型（§5.2）
┌─ 传输层 ─────────────▼───────────────────────────────────┐
│  MQTT mosquitto :1883   ← 主通道，retained + LWT          │
│  HTTP POST :8080/api/telemetry ← 轻量节点兜底，桥接进 MQTT │
└──────────────────────┬───────────────────────────────────┘
                       │
┌─ 状态层 ─────────────▼───────────────────────────────────┐
│  SQLite: 最后已知状态 + 陈旧度                             │
│  ★ 离线保留最后值，不显示空白                              │
└──────────────────────┬───────────────────────────────────┘
                       │
┌─ 展示层 ─────────────▼───────────────────────────────────┐
│  :8080 页面 + SSE 推送 → Chromium kiosk 全屏 1600×900     │
│  ★ 局域网无外网 ⇒ 所有 CSS/JS/字体本地化，禁用 CDN         │
└──────────────────────────────────────────────────────────┘
```

---

## 4. 节点矩阵（设计的出发点）

| 节点 ID | 主机 | 在线特性 | 能否装 Agent | 采集方式 |
|---|---|---|---|---|
| `central` | 中控机 `.9` | 7×24 | 就是它自己 | 本地 `psutil` |
| `win-dev` | Windows 开发机 `.6` | **按需** | 能 | 轻量 exporter（推） |
| `storage` | 存储工作站（通用双路服务器，OS 计划 `.40`） | **按需**（BMC 唤醒） | 能 | exporter（仅开机时） |
| `mc` | **Proxmox VE 宿主机**（MC 服务器是它上面的一台 VM）`.121` —— 2026-11-10 实测；旧记的 某台 Windows 机 `.7` 已失效 | 7×24 | ❌ **非自有** | **PVE 宿主机采集器**（`proxmox-collect.py` 推 CPU/温度/内存）+ VM 内轻量心跳 |
| `bmc` | 通用双路服务器 `.4` | 常在线 | 不适用 | 中控机主动 IPMI 轮询 |

**★ 关键点**：存储工作站平时关机，但它的 **BMC 常在线**。中控机通过 IPMI 轮询就能一直知道它的电源状态——于是状态墙上"存储工作站：**已关机**"是一个**确定的已知状态**，而不是"失联"。

---

## 5. 组件规格

### 5.1 MQTT 主题与遗嘱

```
qyn/node/<node_id>/telemetry     retained   指标快照（新订阅者立刻拿到最后值）
qyn/node/<node_id>/status        retained   在线状态
qyn/node/<node_id>/event         非retained 瞬时事件
```

**LWT（遗嘱）是这个设计的关键**：节点连接时向 broker 注册遗嘱，一旦异常断线，broker **自动**代它发布 `status = offline`。
→ **离线检测不用我们写轮询、不用超时判断**，broker 白送。这正好治"节点按需开机"这个特性。

### 5.2 指标模型

沿用项目开发风格：**schema 最小够用 + 开放字典**（见 AGENT `memory/DEV_STYLE.md`）。

```json
{
  "node": "central",
  "ts": "2026-09-15T21:30:00+08:00",
  "online": true,
  "platform": "linux",
  "cpu":  {"pct": 12.3, "load1": 0.42, "cores": 8},
  "mem":  {"used_pct": 41.0, "used_mb": 6700, "total_mb": 16384},
  "disk": {"used_pct": 5, "free_gb": 220, "total_gb": 233},
  "net":  {"rx_kbps": 120, "tx_kbps": 30},
  "extra": {}
}
```

**`extra` 是开放通道**，节点特有信息往里放，schema 不预先完备：

- `mc`：玩家数、世界大小、进程状态
- `bmc`：电源态（on/off）
- `central`：WAN 认证状态（minieap 上线后）、Tailscale 状态

### 5.2.1 不采集温度

**主板自带温控保护，温度不是可行动的信息。** 采它只会引入
"每台机器传感器各不相同"的复杂度——实测 Windows 上 psutil **根本没有传感器接口**，
要拿 CPU 温度得给每台机器装 HWiNFO / LibreHardwareMonitor 这类第三方软件。

对"采集器复用到多台电脑"这个目标来说不值得。曾实现的可插拔探针层已删除。

> 若将来需要 GPU 利用率之类的数据，那是**利用率**不是温度，应作为独立议题评估，
> 而不是把探针框架整个搬回来。

### 5.3 HTTP ingest

`POST :8080/api/telemetry` —— 给**装不了 MQTT 库的节点**（MC 服务器、未来 STM32）用。
收到后由服务端**桥接发布到 MQTT**，于是下游（页面、运维 Agent）只需订阅 MQTT 一个口，不关心节点用什么方式上报。

### 5.4 状态层

SQLite，形如 `node_state(node, ts, payload, received_at)`，保存**最后已知值**。
**陈旧度 = now − received_at**，页面上显示"3 小时前的数据"。

**离线是常态，不是故障** —— 只有"该在线却离线"（如 MC 服务器）才标红。

### 5.5 展示层

- 后端：一个小服务（FastAPI 或纯 stdlib），订阅 MQTT → 经 **SSE** 推给页面
- 前端：静态 HTML/CSS/JS，**全部本地资源，零 CDN**
- kiosk：`chromium --kiosk --app=http://localhost:8080/`

---

## 6. 与 AGENT 架构的接缝

指标流接进现有 **EventBus**，作为新事件源：

| 事件 | 发布者 | 消费者 |
|---|---|---|
| `node:telemetry` | MQTT→EventBus 桥 | 状态墙、**运维 Agent**（规划中） |
| `node:offline` | MQTT LWT→桥 | 同上（触发告警） |

**一份数据，两个消费者**——状态墙只负责渲染，运维 Agent 负责告警，**不重复采集**。这正是 AGENT 文档"事件总线解耦"的用法。

> 落地时机：AGENT 代码目前还没部署到中控机。**现在只需把主题和 schema 定死**，将来写这个桥就是几十行的事。

---

## 7. kiosk 配置要点（保留 GNOME）

| 项 | 做法 |
|---|---|
| 自动登录 | GDM `AutomaticLoginEnable` / `AutomaticLogin=user` |
| 禁息屏/休眠 | `gsettings` 关 blank/idle + `systemd` mask 睡眠 target |
| 自启 kiosk | `~/.config/autostart/*.desktop` 拉起 Chromium kiosk |
| 隐藏鼠标 | `unclutter` 或 Chromium 参数 |
| 断电自恢复 | 中控机 BIOS 设 **AC Power Loss = Power On**（这块屏是无人值守的） |

---

## 8. 分阶段路线

| 阶段 | 内容 | 验收 |
|---|---|---|
| **S1** | 中控机自采集 + 最小页面 + kiosk（mosquitto 待中控机上线时装） | 屏幕上能看到中控机自己的实时 CPU/内存，断电重启自动恢复 |
| **S2** | BMC IPMI 轮询 → 存储工作站电源态 | 屏幕上正确显示存储站"开机/关机" |
| **S3** | MC 服务器 HTTP 心跳 | 显示玩家数、世界大小、在线状态 |
| **S4** | Windows / 存储工作站 exporter | 各节点指标上墙 |
| **S5** | 接 EventBus + 运维 Agent 消费 | 告警链路打通 |

**先从 S1 开始**——它同时验证了「采集→传输→状态→展示→kiosk」整条链路，后面几步都只是往里加节点。

### ★ S1 现状（2026-10-09）

| S1 子项 | 状态 |
|---|---|
| 采集器（psutil → 统一指标模型，`collect.py` + `metrics.py`） | ✅ 跑通 |
| 上报去向 `http`（给装不了 MQTT 库的节点兜底） | ✅ 已加并实测 |
| 状态层（**最后已知值 + 陈旧度**，`state.py`） | ✅ 已实现并测试 |
| 展示层（HTTP + SSE + ingest，**纯 stdlib**，`server.py` / `serve.py`） | ✅ 已实现并测试 |
| 页面（**零 CDN**，全内联，`web/index.html`） | ✅ 已实现；"零外部资源"由测试断言 |
| 采集器 → 服务端 → 页面 全链路联调 | ✅ 已实测（真实机器指标上墙） |
| mosquitto（MQTT 主通道） | ⏳ 待中控机上线（**2026-10-13 出口已通，可直接 `apt install mosquitto`**，见 CONNECTION.md §3） |
| Chromium kiosk + 断电自恢复 | ⏳ 待真机（`chromium --kiosk` + BIOS AC Power Loss = Power On） |

测试：`python CENTRAL/GUI/status-wall/test_wall.py` → **9/9**（含 SSE 推送、队列有界与丢弃计数、
上报失败容忍、陈旧度、**零 CDN 硬规则**、输入校验）。

> 展示层刻意用 **stdlib** 而不是 FastAPI：中控机依赖越少，离线环境越可靠
> （本文件 §5.5 原本写的就是"FastAPI **或**纯 stdlib"）。这条与"有没有外网"无关 ——
> **即使出口已通（2026-10-13），也不给它加依赖**。

---

## 9. 约束与风险

| 约束/风险 | 说明 | 对策 |
|---|---|---|
| ~~**局域网无外网**~~ ✅ 已解除（2026-10-13） | 内网已有出口（路由器链），装包可直接 `apt`；**但页面仍然不能用任何 CDN**（局域网客户端不保证能出网） | 装包走 `apt`（中继 `tools/lan_http_proxy.py` 留作备用）；**资源全本地** |
| 单网卡 | 中控机只有 `enp2s0`，将来做网关需重新设计（见 CONNECTION.md §6） | 与本项目无冲突，但会影响"WAN 状态"显示项 |
| Windows 机 IP 动态 | 按需开机 + DHCP，地址会漂 | 建议做 DHCP 保留（CONNECTION.md §9） |
| GNOME 桌面占资源 | 24×7 kiosk 跑完整桌面略浪费 | 已选保留；将来可换轻量会话 |
| 中控机 IP 已固定 | `.9`，DHCP 保留，重启验证过 | 无需额外处理 |
