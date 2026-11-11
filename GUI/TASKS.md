# 状态墙 · 任务拆分（WBS）

> 建立：2026-10-13 ｜ 状态：**待拍板**（先拆分，未开工）
> 关联：[README.md](README.md)（GUI 开发区）、[../STATUS_WALL.md](../STATUS_WALL.md)（架构与 S1–S5 路线）、[status-wall/README.md](status-wall/README.md)（代码）

---

## 0. 起点：S1 已完成的部分

| 层 | 文件 | 现状 |
|---|---|---|
| 采集 | `qynwall/metrics.py` + `sysinfo.py` | 本机指标（psutil）→ 统一模型；`extra` 开放通道**还空着** |
| 传输 | `qynwall/sinks.py` | stdout / mqtt / http 三种去向 —— **只发不收** |
| 状态 | `qynwall/state.py` | SQLite 最后已知值 + **服务端**算的陈旧度（upsert ⇒ 天然有界） |
| 展示 | `qynwall/server.py` | 纯 stdlib：静态页 + `/api/state` + SSE + ingest |
| 页面 | `web/index.html` | **单文件全内联**、零 CDN；`EXPECTED_ONLINE` 与阈值**硬编码在前端** |
| 入口 | `collect.py` / `serve.py` | 只做组装，无业务逻辑 |
| 测试 | `test_wall.py` | 9 项；纯 stdlib、内存库、随机端口、不碰真实数据 |

### 三个已看清的缺口（整个拆分围着它们转）

1. ★ **MQTT 只有"发"没有"收"。** `MqttSink` 往 broker 发 `telemetry` / `status`，
   但 `server.py` 里**没有任何订阅者** —— 所以"MQTT 是主通道（retained + LWT，
   离线检测白送）"目前**不成立**：状态墙根本读不到 broker。补读半边 = **BE-1**。
2. ★ **前端是一个文件。** 样式与逻辑全在 `web/index.html` 里，两条 UI 线并行必然互相踩。
   要并行 ⇒ 先拆 `index.html + app.js + style.css` 并给 server 加静态路由 = **UI-0**。
3. **"应有的在线性"与阈值写死在前端。** `EXPECTED_ONLINE = {central, mc, bmc}`、
   45s/300s 阈值都在 `index.html`；加一个节点就要改前端。外置 = **BE-4 + UI-3**。

---

## 1. CT · 契约（前置，冻结后才好并行）

| ID | 目标 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| **CT-1** | 指标模型 + 各节点 `extra` 字段表（主机 / bmc 电源态 / mc 玩家数与世界大小） | `status-wall/CONTRACT.md` | — | 三个消费者（采集器、状态层、页面）能各自照它写测试；`extra` 不再"随便放" |
| **CT-2** | 实时链路形状：SSE 事件（`snapshot` / `sample` / 心跳）、`/api/state` 响应、断线语义 | 同上 | — | 前端不用读后端源码就能实现；改字段必须先改契约 |
| **CT-3** | ingest 契约：状态码语义（400/411/413/422/202）、校验规则、鉴权开关 | 同上 | — | 每个状态码都有测试；鉴权默认关、开启方式写清 |

> 契约是**唯一**允许前后端同时引用的东西。它一变，两边一起改 —— 所以它必须最先冻结。

---

## 2. BE · 后端

写入范围：`status-wall/qynwall/*.py`、`collect.py`、`serve.py`、`config.example.toml`、`requirements.txt`

| ID | 目标 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| **BE-1** ★ | **MQTT 订阅桥**：订阅 `qyn/node/+/telemetry` 与 `.../status` → 写进 StateStore（补齐主通道的读半边） | `qynwall/bridge.py` | CT-2 | 假 broker / 假客户端下能测；`paho` 缺失时**不崩**、只警告降级 |
| **BE-2** ★ | **在线/离线落到状态层**：`online` 不再由采集器硬编码为 True；LWT 的 `{"online": false}` 进 store，并记 `status_at` | `state.py` + `metrics.py` + `bridge.py` | BE-1 | "该在线却离线"由**数据**判定，不再靠前端猜 |
| **BE-3** | ingest 加固：可选 token 鉴权、节点白名单、每节点速率上限（脏节点不能刷爆 SQLite） | `server.py` | CT-3 | 未授权被拒、超速被限流，且有测试 |
| **BE-4** | 节点配置外置：`EXPECTED_ONLINE`、陈旧/离线阈值、节点显示名 → `wall.toml`，经 `/api/config` 下发 | `serve.py` + `server.py` + `config.example.toml` | CT-2 | 加节点**不用改前端** |
| **BE-5** | 采集源 S2：BMC / IPMI 轮询 → `extra.power`（存储工作站"已关机"是**确定状态**，不是失联） | `qynwall/pollers/bmc.py` | CT-1、BE-2 | 用假 IPMI 输出测；真机只读轮询、失败不致命 |
| **BE-6** | 采集源 S3：MC 服务器心跳 → `extra.players` / `extra.world_mb` | ingest 扩展 + 文档 | CT-1 | 无 MQTT 库的节点走 HTTP 也能上墙 |
| **BE-7** | 远程 exporter S4：Windows / 存储工作站一键采集（复用 `collect.py` + 配置模板 + 上手文档） | 配置模板 + README | BE-2 | 新机器 3 步内上墙；节点 ID 显式、不靠主机名 |
| **BE-8** | 状态库有界与耐久：`wall.db` 增长、VACUUM 时机、断电后 SQLite 完整性 | `state.py` | — | 长跑不涨；kill -9 后能打开 |
| **BE-9** | 可观测：`/api/health`（版本、订阅数、丢弃计数、broker 连接态）+ 日志规范 | `server.py` | BE-1 | 页面/命令行都能一眼看出"链路哪一段断了" |

---

## 3. UI · 前端

写入范围：`status-wall/web/*`

| ID | 目标 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| **UI-0** ★ | 拆单文件：`index.html + app.js + style.css`；server 加**只读**静态路由（扩展名白名单，防目录穿越） | `web/*` + `server.py` | CT-2 | 页面功能不变（现有 9 项测试仍全绿）；两条 UI 线可并行 |
| **UI-1** | **1600×900 kiosk 定版布局**：信息层级、卡片密度、排序规则（现在只是 auto-fit 网格，没按这块屏设计） | `style.css` + `index.html` | UI-0 | 一屏读完；远处 2 米能看清；不出现滚动条 |
| **UI-2** | 分节点类型卡片：主机（CPU/内存/磁盘/网络）· bmc（电源态大字）· mc（玩家数/世界大小）；`extra` 不再裸渲染 | `app.js` | CT-1、UI-0 | 三种节点各有一张图；未知字段有兜底 |
| **UI-3** | 四态可视化 + 图例：在线 / 陈旧 / 离线（按需开机，正常）/ 离线（异常）；阈值与"应有在线性"来自 `/api/config` | `app.js` + `style.css` | BE-4、UI-0 | 图例常驻；四态肉眼可分（不只靠颜色 —— 色盲与远距离） |
| **UI-4** | 实时链路健壮性：重连退避（现在固定 15s）、断线**多久了**可见、快照与增量不打架 | `app.js` | UI-0 | 拔网线/杀 server 后页面自己恢复，且过程可见 |
| **UI-5** | 无人值守健壮性：防烧屏（像素漂移 / 夜间降亮）、页面 watchdog 自刷新、长时间内存有界 | `app.js` | UI-1 | 连续跑 24h 不糊屏、不涨内存 |
| **UI-6** | 字体与 kiosk 环境：`fonts-noto-cjk` 缺失检测、字体回退链、隐藏鼠标 | `style.css` + 部署清单 | UI-1 | 中文**可读**（不是方框）—— 缺字体必须能看出来，不能静默 |
| **UI-7** | 远程浏览器视图：别的机器用浏览器看（响应式），与 kiosk 共用一套页面 | `style.css` | UI-1 | 手机/笔记本都能读；不引入第二套页面 |
| **UI-8** | 空态与异常态：一个节点都没有 / 只有 bmc / broker 掉线 | `app.js` | UI-3 | 每种状态都有明确文案，不留白屏 |

---

## 4. DEP · 部署与真机（kiosk）

| ID | 目标 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| **DEP-1** | mosquitto 装好并配置：retained + LWT、只绑内网、鉴权策略（出口已通，可直接 `apt`） | 配置 + 文档 | — | 断线后 broker 代发 `offline`（`mosquitto_sub` 实测） |
| **DEP-2** | systemd **用户**服务：`wall.service` + `collector.service` + `loginctl enable-linger` | unit 文件 | BE-9 | 重启后自启；`journalctl --user` 能看到日志 |
| **DEP-3** | Chromium kiosk 自启 + GDM 自动登录 + 禁息屏 | autostart + 脚本 | DEP-2 | 断电重启 → 屏幕自动回到状态墙 |
| **DEP-4** | 断电自恢复：BIOS `AC Power Loss = Power On` + 重启自检清单 | 清单 | DEP-3 | 拔电再插，无人干预恢复 |
| **DEP-5** | 部署脚本：tar over SSH → `releases/<时间戳>` → 切 `current` 软链（DEV_WORKFLOW §8 目前只有"拟"） | `scripts/deploy.sh` | DEP-2 | 一条命令部署；回滚 = 指回旧 release |
| **DEP-6** | 开机自检：页面 200 / SSE 真有推送 / broker 在线 / 采集器在跑 | `scripts/check.sh` 扩展 | DEP-2 | 一条命令回答"墙是活的吗" |

---

## 5. QA · 质量

| ID | 目标 | 依赖 | 验收 |
|---|---|---|---|
| **QA-1** | **契约测试**：后端测试钉住前端消费的 JSON 形状（改字段就红） | CT-2 | 故意改一个字段名，测试必须失败 |
| **QA-2** | 新采集源各自测试（BMC / MC 用假数据，不打真机） | BE-5、BE-6 | 每源都有断言；无硬件也能跑 |
| **QA-3** | 故障注入：拔网线、杀进程、断电、broker 挂掉 | DEP-2 | 每种故障后**页面自己恢复**，且"哪段断了"可见 |
| **QA-4** | 文档同步：README / STATUS_WALL §8 状态表 | 全部 | 文档与代码一致（含路径、命令） |

---

## 6. 建议的并行波次

```
波 1  CT-1/2/3            冻结契约（1 条线，别人都在等它）
波 2  BE-1·BE-2  ｜  UI-0→UI-1·UI-2·UI-3  ｜  DEP-1
      主通道读半边   前端拆文件 + kiosk 定版       broker 装好
波 3  BE-3·BE-4·BE-9  ｜  UI-4·UI-5·UI-8  ｜  DEP-2·DEP-3·DEP-4
波 4  BE-5·BE-6（S2/S3）  ｜  UI-2 扩展  ｜  DEP-5·DEP-6  ｜  QA-1
波 5  BE-7（S4 exporter）  ｜  UI-6·UI-7  ｜  QA-2·QA-3
波 6  S5（EventBus / 运维 Agent 消费）—— 等 AGENT 侧的接口
```

**关键路径**：CT → BE-1/BE-2 → DEP-1 → DEP-2/DEP-3 → 真机验收。
**可以立刻并行的**：CT 冻结后，BE（后端）与 UI（前端）各一条线 —— 因为写入范围不重叠
（`qynwall/*.py` vs `web/*`），前提是 **UI-0 先拆完文件**。

---

## 7. 需要你拍板的（拆分里的分叉点）

| # | 问题 | 我的建议 |
|---|---|---|
| D1 | **一屏放什么**：1600×900 要几个节点、哪些指标、要不要"大字模式" | 先给 4–6 张卡片 + 大字关键值；具体等你说 |
| D2 | **MQTT 上不上主通道** | 上：LWT 白送离线检测，且现在"只发不收"是**真缺口**；HTTP ingest 保留作兜底 |
| D3 | **要不要历史趋势**（现在是"最后已知值"，没时序） | 先不要；等有具体问题再上时序表（现在 schema 是 upsert，加时序是另一张表） |
| D4 | **中控机现在能碰吗**（SSH / 显示器 / Chromium） | 需要你确认；DEP 那一整块都卡在这个前提上 |
| D5 | **展示层是否继续纯 stdlib** | 继续：零依赖在离线环境最可靠，S1 的 9 项测试也是这么钉的 |
| D6 | **要不要拆成多人并行**（我把这些任务挂到共享任务板 / 派 teammate） | 建议波 1 先由我一条线做完契约，之后再说 |
