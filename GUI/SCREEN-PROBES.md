# 大屏 SERVICE 面板：探针接口契约

> 给**状态墙 / 大屏**那侧的交接文档。用户 2026-10-16：
> 「最后一轮时刻和其他关于 agent 系统的探针准备好接口。我要让另一个 agent 并入大屏。」
>
> 数据生产方是 **Agent（时间管理）** 这一侧，渲染方是大屏。本文件是**接口**，不含渲染实现。

## 1. 两条读取通路（同源，取一条即可）

| 通路 | 地址 | 适用 |
|---|---|---|
| **状态墙 SSE** | `GET http://127.0.0.1:8080/api/stream` → `data: {...}` 里的 `nodes[]` | 大屏已经在用的通路；节点名 `svc-agent` |
| 一次性快照 | `GET http://127.0.0.1:8080/api/state` → `nodes[]` 里找 `node == "svc-agent"` | 调试 / 非流式页面 |
| **JSON 文件** | `~/qyn/agent/logs/agent-probe.json`（中控机上） | 不想走墙、或想在服务端渲染 |

推送方：`qyn-agent-probe.timer`（**每 1 分钟**）→ `CENTRAL/scripts/agent-probe.py --post`
→ `POST /api/telemetry`，body 为 `{"node": "svc-agent", "frame": {...}}`（与 `svc-egress` 完全同一条通路）。

## 2. 帧的形状

与 `svc-egress` / `svc-opencode` 同一个约定：**大屏按 `items` 逐行渲染，不硬编码字段语义**。

```json
{
  "schema": "qyn.svc-agent/1",
  "level": "ok",                       // ok | warn | bad —— 取 items 里最差的那个
  "headline": "最后一轮 12:46",         // 一行摘要（折叠态就显示它）
  "generated_at_ms": 1792130808441,
  "fresh_for_ms": 300000,              // 超过 now - generated_at_ms 就显示「数据陈旧」
  "items": [
    {"key": "最后一轮",   "value": "12:46 message · 退出码 0 · 37s",            "level": "ok"},
    {"key": "唤醒链",     "value": "下一个 10-17 11:30（下一块 09:30-11:30…）",  "level": "ok"},
    {"key": "守卫",       "value": "13:58 空转（边界未到）",                    "level": "ok"},
    {"key": "微信通道",   "value": "接收中 · 最后 10/16 12:46 image",           "level": "ok"},
    {"key": "接收方轮询",   "value": "8 秒前",                                  "level": "ok"},
    {"key": "最后一条发出", "value": "10/16 16:06 已送达 <message_id>",      "level": "ok"},
    {"key": "入站媒体",   "value": "2 个 · 442KB · 最近 10/16 12:46",           "level": "ok"},
    {"key": "内核",       "value": "20261016-140408 · 工具 11",                "level": "ok"},
    {"key": "数据",       "value": "注册 54 · 课次 21 · 快照 10-16 12:46 新鲜",  "level": "ok"}
  ]
}
```

**兼容性约定**（大屏按这个写就不会被将来的字段变化打断）：

- 只依赖 `level` / `headline` / `generated_at_ms` / `fresh_for_ms` / `items[].{key,value,level}`；
- `items[].level` **可以缺**（`svc-egress` 就有缺的）⇒ 缺了按 `ok` 处理；
- 出现**新的 key** 是正常演进 ⇒ 直接多渲染一行，不要按白名单过滤；
- `schema` 变了才是破坏性变更（大屏遇到不认识的 `schema` 可以整块降级显示 headline）。

## 3. 每一项什么时候变 warn / bad

判据全在 `CENTRAL/scripts/agent-probe.py` 里（**只读**：解析日志 + systemd 只读查询 + 一次内核 `context`）。

| 项 | 正常 | warn | bad |
|---|---|---|---|
| 最后一轮 | 时刻 / 模式 / 退出码 / 耗时 | 有开始没结束（还在跑或没记退出码）；**> 30 小时**没跑过任何一轮 | 退出码 ≠ 0 |
| 唤醒链 | 下一个到点唤醒的时刻 + 理由 | 从没排出过 | 对应的 boundary 定时器不存在 |
| 守卫 | 最后一次守卫的时刻 + 空转 | 补跑过（说明链条曾断） | — |
| 微信通道 | `接收中` + 最后一条消息的时刻与类型 | — | 接收方 `qyn-phone.service` 不是 active |
| 接收方轮询 | `N 秒前`（最后一次长轮询回来的时刻） | 没有心跳；超过 5 分钟 | 超过 15 分钟 |
| 最后一条发出 | 时刻 + `已送达 <message_id>` | — | 发送失败（带失败阶段与原因） |
| | `还没发过`（全新系统，不算问题） | | |
| 入站媒体 | 落盘个数 / 总大小 / 最近时间 | — | — |
| 内核 | 当前 release + 插件注册的工具数 | — | 内核文件不在 |
| 数据 | 注册条目数 / 课次数 / 规划快照时刻 + 新鲜度 | 规划快照**已作废**（`stale`） | — |

★ 「数据」的新鲜度用的是**内核自己的判据**（`context.last_plan.stale`），不在这里重写一遍 ——
红线是「同一条规则只能有一份实现」。

## 4. 渲染参考（给大屏那侧）

大屏里已经有 `svc-egress` 的渲染块（`CENTRAL/GUI/screen/web/index.html` 的 `renderEgress()`）。
把节点名参数化就是一个通用的 `svc-*` 渲染器：

```js
function renderSvcNode(hostId, node) {
  const box = document.getElementById(hostId);
  if (!box) return;
  const f = node && node.frame;
  if (!f) { box.textContent = "无数据"; return; }
  const stale = f.generated_at_ms && f.fresh_for_ms &&
                (Date.now() - f.generated_at_ms > f.fresh_for_ms);
  const worst = f.level || "ok";
  box.className = "svc " + (stale ? "stale" : worst);
  box.innerHTML =
    `<div class="svc-head">${escapeHtml(f.headline || "")}${stale ? " · 数据陈旧" : ""}</div>` +
    (f.items || []).map(it =>
      `<div class="svc-row ${it.level || "ok"}">` +
      `<span class="k">${escapeHtml(it.key)}</span>` +
      `<span class="v">${escapeHtml(it.value)}</span></div>`).join("");
}
// 从 SSE 快照里取：
//   const n = snapshot.nodes.find(x => x.node === "svc-agent");
//   renderSvcNode("svc-agent", n);
```

（`escapeHtml` 是必需的：`value` 里会出现用户/课程名。）

## 5. 为什么有「最后一条发出」这一项

2026-10-16 用户报「我发出后没有回应」，排查时发现：**发送结果只存在于那一轮的会话存档里** ——
收件箱有 `inbox.jsonl`，发出的却没有对应物，于是"收得到"和"发得出"没法对照，翻六处才确认
其实三批消息都发出去了（`message_id` 都在）。

现在 `clawbot send` 每次都会往 `<state>/sent.jsonl` 追加一条流水
（`ts / ok / peer_id / stage / message_id / attempts / error / chars / head`），
探针据此给出「最后一条发出」——大屏上"收"和"发"并排，一眼能看出是哪一侧断了。

## 6. 为什么有「接收方轮询」这一项（心跳）

**游标 mtime 不能当心跳** —— `save_cursor` 的判据是"值变了才写"，没人发消息时它一动不动。

2026-10-17 凌晨就是这样吃了亏：用户 18:03 之后发的一条消息静默丢了，而"接收方到底还在不在轮询"
任何地方都看不出来（我按游标 mtime 判过一次，**判错了**）。现在接收方**每轮长轮询回来**都摸一下
`<state>/poll.heartbeat`（`os.utime`，不会被"没变就跳过"），探针据此给出这一项 ——
"还在不在收"从此是一眼可见的事实，而不是要翻日志去猜。

## 7. 生产方

```
CENTRAL/scripts/agent-probe.py                  探针本体（只读；--json / --post / --no-file）
CENTRAL/scripts/systemd/qyn-agent-probe.service oneshot，跑一次推一次
CENTRAL/scripts/systemd/qyn-agent-probe.timer   每 1 分钟
CENTRAL/deploy/install-agent.sh                 随发布安装并启用（12 个单元之一）

★ 部署会**自动重启正在跑的接收方**（`qyn-phone.service`）：手机侧的改动原先"只装不启"，
  于是 2026-10-16 部署的入站留痕与发送流水**一次都没生效**，直到人工重启才发现。
  重启安全（接口承诺"至少一次"，游标已落盘）；没在跑时依然**不擅自启用**。
```

调试：

```bash
ssh qyn-central 'python3 ~/qyn/agent/current/CENTRAL/scripts/agent-probe.py'          # 打印这一帧
ssh qyn-central 'systemctl --user list-timers | grep probe'                           # 定时器
ssh qyn-central 'journalctl --user -u qyn-agent-probe.service -n 5'                   # 最近一次推送
```

## 8. 大屏 SERVICE 面板：工作区约定（WS01 / WS02）

> 用户 2026-10-17：`WS WS01` 并进标题、备用工作区要能看到"月剩余 + 刷新"。
> 本节记的是**大屏读什么**；采集怎么填由 `apiusage` 那侧决定。

### 8.1 大屏怎么显示

| 位置 | 来源 | 展开大模块 | 折叠 |
|---|---|---|---|
| 卡片**标题** | 大屏写死 `OCGO` + 当前工作区 | **`OCGO WS01`** | `OCGO`（不带 WS01） |
| `WS2` 行 | 大屏固定写 `WS02` | 显示 | 不显示 |
| `MO` 行 | **第二个带 GO 的工作区**的 `month` 计量 | 显示 | 不显示 |

- **`WS01` / `WS02` 是大屏的叫法**（对应控制台里的两个工作区），不是接口字段；
  当前工作区仍取契约里"剩余最少且不为 0"的那个（`svcPick`）。
- `MO` 行口径 = **剩余**（`usable_percent`）+ **刷新时刻**（`resets_at_ms` / `reset_in`）。
- 探针里**没有**第二个带 GO 的工作区时：`WS2` 行写 `WS02`、`MO` 行写 **`STANDBY`**。
- ⇒ **采集侧只要把 WS02 放进 `workspaces[]` 且 `has_go: true`，大屏这行自动生效，不用改代码。**

### 8.2 2026-10-17 实测（中控机，只读探测）

```
GET https://opencode.ai/console/api/orgs                 → 200
   wrk_EXAMPLE000000000000000000   name = WS01
   org_EXAMPLE000000000000000000   name = WS02
GET .../api/go/status  (x-org-id = WS01)                 → 200  有订阅数据
GET .../api/go/status  (x-org-id = WS02)                 → 200  body = null（**暂无 GO 订阅**）
```

判读：

- **一个 Cookie 同时看见 WS01 与 WS02** ⇒ 同账号，**不需要第二套 Cookie**。
- WS02 的 `go/status` 现在返回 `null` ⇒ 它**还没有 GO 订阅**，所以大屏 `MO` 行显示 `STANDBY` 是**对的**。
- **`oc_sk_` Key 是账号级的**（见 `apiusage/README.md`）：只拿额度、**区分不了工作区**
  ⇒ 要让 WS02 单独出数，必须走 **Cookie + `x-org-id`**。

### 8.3 对话（`POST /api/chat`）**是可用的**；不回复请先查出口

> ★ 2026-10-17 21:36 **实测更正**：对话**不需要什么"plan"**，而且**当时就是通的** ——
> 发一句"只回复 OK" ⇒ `ok=true`、`reply="OK"`、**耗时 4.0 秒**。
>
> 它跑的和你每轮 agent 是**同一条命令、同一套环境、同一个 profile**（⇒ **同一把工作区 KEY**）
> ⇒ **KEY 能用，对话的模型访问就能用**。原先"大屏对话不通"是我**误判** ✗
> —— 根源是当天 14:28 / 14:44 的**出口故障**（`opencode.ai`），与 agent 那一轮 TRANSPORT 报错**同因**。
>
> ⇒ 排查顺序：**先看 `svc-egress`**（墙上 SERVICE 卡的 `EGR` 行：`OL` / 红闪 `OF`）✓，
> 不要再去怀疑 plan ✗。

### 8.4 对话可以**带图**（2026-10-20 接入）

屏幕把图按 base64 塞进 **`text/plain` 的 JSON 字符串**（仍是简单请求 ⇒ 不触发 CORS 预检）
⇒ 桥落盘到 `~/qyn/chat-uploads/`（≤3 张 · 单张 ≤8MB · 后缀白名单外一律强制 `.png`）
⇒ 再把 **`@绝对路径`** 拼在提示词最前面 ⇒ DSH 把 `@` 当"用户显式引用的路径"
⇒ Agent 用 `read_image` 打开。

用法：对话区里 **Ctrl+V 贴图**（或点 📎 选文件）⇒ 出现待发小片 ⇒ 回车发送。
于是**课表截图 / 作业照片**可以直接丢进墙上的对话框（`qyn-timetable` 技能本来就读课表截图）。

---

# 推送体系：规划与待办走状态墙 SSE（`agent-plan` 节点）

用户 2026-10-17：「改成推送体系」。

原先大屏是**轮询**：页面每 60 秒拉 `/api/agent`，桥对规划再缓存 60 秒 ⇒ 规划改一次最坏
~2 分钟才上屏。现在改成**数据一变就推**：

```
内核 data/*.json 变化
   ↓ （systemd path 单元监听，事件驱动）
发布器 agent-plan-publish.py  →  POST /api/telemetry   node="agent-plan"
   ↓ （状态墙存整份 payload 并 SSE 广播）
大屏页面（已经在读 SSE）      →  直接渲染，不再轮询
```

## 帧的形状（`node = "agent-plan"`）

```json
{
  "schema": "qyn.agent-plan/1",
  "generated_at_ms": 1792250000000,
  "fresh_for_ms": 300000,
  "level": "ok",                          // ok | warn | bad（规划有问题就是 bad）
  "headline": "10/18 周日 · 下一块 08:30-11:50 示例课程H 第二次实验报告",
  "plan": {
    "created_at": "2026-10-17 23:26:49",
    "stale": false,
    "focus": "…", "order": ["…"],
    "slots": [{"date": "2026-10-18", "time": "08:30-11:50",
               "task": "示例课程H 第二次实验报告",
               "why": "…", "passed": false}],
    "problems": []                        // 内核校验出来的问题（空 = 干净）
  },
  "time": {
    "now": {"date": "2026-10-17", "time": "23:58", "phase": "深夜"},
    "due": [{"task": "…", "date": "2026-10-19", "why": "还剩 2 天",
             "urgency": "soon", "lead_days": 3}],
    "sessions": [{"date": "2026-10-18", "time": "08:30-11:50",
                  "course": "示例课程H 第二次实验报告", "location": "…"}]
  }
}
```

**兼容性约定**（与 SERVICE 面板同一套）：

- 只依赖 `level` / `headline` / `generated_at_ms` / `fresh_for_ms` + 上面这些键；
- **新键是正常演进**，`schema` 变了才是破坏性变更；
- 帧内**不放**「要不要提前、提前多久」这类语义判断 —— DDL 那行只有时间（见第 3 节）。

## 页面怎么用

1. 页面**已经在**连状态墙 SSE（SERVICE 面板就是）⇒ 复用同一条连接；
2. 收到 `type == "snapshot"` 或 `sample`/`update` 事件时，取 `nodes[]` 里 `node == "agent-plan"`；
3. 有它就用它渲染 AGENT 面板与 DUE/日程；**没有**（或 `fresh_for_ms` 过期）就退回原来的
   `/api/agent` 轮询 —— 推送是主路，轮询只做兜底，绝不因为推送没到就白屏。

## 发布方

```
CENTRAL/scripts/agent-plan-publish.py             组帧 + POST（只读内核：wall + sessions）
CENTRAL/scripts/systemd/qyn-agent-plan-publish.path    PathModified data/*.json ⇒ 立刻推
CENTRAL/scripts/systemd/qyn-agent-plan-publish.service oneshot
CENTRAL/scripts/systemd/qyn-agent-plan-publish.timer   兜底每 5 分钟（防漏事件/首次）
```

调试：

```bash
ssh qyn-central 'python3 ~/qyn/agent/current/CENTRAL/scripts/agent-plan-publish.py --json | head -40'
ssh qyn-central 'systemctl --user list-timers | grep plan-publish'
ssh qyn-central 'python3 -c "import json,urllib.request; d=json.loads(urllib.request.urlopen(http://127.0.0.1:8080/api/state
```
