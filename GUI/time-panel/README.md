# CENTRAL/GUI/time-panel —— TIME 面板的只读桥

> 建立：2026-10-14 ｜ 关联：[../screen/README.md](../screen/README.md)（屏幕壳）、
> [../../../AGENT/qyn.py](../../../AGENT/qyn.py)（时间管理内核）、[../../scripts/systemd/qyn-time-panel.service](../../scripts/systemd/qyn-time-panel.service)

## 它是什么

屏幕第三栏 **TIME** 面板的数据源：把**时间管理 Agent 的内核**（`AGENT/qyn.py`，一个 CLI）
的**只读**输出转成 HTTP，供浏览器里的屏幕壳直接 `fetch`。

```
AGENT/qyn.py sessions   ┐
AGENT/qyn.py anomalies  ┤
AGENT/qyn.py context    ┼─▶ time-panel/serve.py (:8091) ──▶ 屏幕 TIME / AGENT 面板
AGENT/qyn.py brief      ┤
AGENT/qyn.py review     ┤
AGENT/qyn.py wall       ┘   ← 大屏专用只读视图（规划 + 每块 why/gist/urgency）
```

为什么需要这一层：屏幕壳是**浏览器里的页面**，跑不了 Python；而内核是 CLI，没有 HTTP 接口。
桥只做"转发 + 缓存"，不做任何加工。

## ★★ 两条红线

1. **只读、零写副作用**：只调 `sessions` / `anomalies` / `context` / `brief` / `review` / `wall`
   （都是内核文档里承诺零副作用的读命令）。
   **绝不调** `apply` / `mark` / `sync` / `plan-snapshot` —— 写操作只属于时间管理 Agent，
   桥不许碰，更不许直接读改 `AGENT/data/*.json`（项目铁律：写操作只经内核；
   而且 `plan` 正文**没有 schema 校验**、随时可能改键，拿它当接口很脆）。
2. **不自己算日期**：页面上"今天几号、周几、还剩多久"**全部用内核给的现成字串**
   （`sessions.now` / `starts_in` / `why`）。日期由内核算，别处心算必错 ——
   学校会调休停课，"第 N 周周三"推不出"10 月 14 日"。

## 接口

| 路径 | 说明 |
|---|---|
| `GET /api/time` | TIME 面板数据：`{ok, source, updated_at, age_s, sessions:{now,next,sessions,due}, anomalies}`（缓存 15 秒） |
| `GET /api/agent` | **AGENT 面板（规划）**：`{ok, now, plan:{created_at, triggered_by, focus, horizon, order, stale, age_days, slots[]}, rules[], warnings[], unscheduled[]}`（缓存 60 秒） |
| `GET /api/time?force=1` / `GET /api/agent?force=1` | 绕过各自缓存 |
| `GET /health` | 探活，回内核路径 |
| `POST /api/chat` | 把大屏对话的这句话交给 Agent 跑一轮，返回它的话（忙时 429，不排队） |
| **`POST /api/replan`** | **一键重排**（用户 2026-11-08）：启动 `qyn-agent-replan.service` 跑一轮 `tick.sh replan` ⇒ 重排 + 存快照 + 推大屏，**不发微信**。返回 202 `{ok,state:"running"\|"started"}`；已在跑 ⇒ 202 `already`（连点只跑一轮）；别的轮占着锁 ⇒ **429 `busy`**（不排队） |
| `GET /api/replan` | 重排状态：`{ok, active, state:"running"\|"idle", started_at, finished_at, exit_status}` —— 大屏按钮靠它知道什么时候排完 |

> **桥不自己排规划**：`/api/replan` 只"启动那一轮"，重排这件事仍然只有
> `CENTRAL/scripts/qyn-agent-tick.sh replan` 一份实现（内核写、快照、推送全在它手里）。
> 换成桥自己 spawn agent 的话，环境/日志/超时都要再抄一遍，桥一重启还会打断正在跑的那轮 ✗。
> 页面（`:8090`）与桥（`:8091`）跨源：POST 一律 **text/plain + 空体**，避免 CORS 预检。

`/api/agent` 只挑大屏要显示的字段（`context` 整个很大），并把注册表里的 `gist`/`why`/`urgency`
按**任务名**接到每个时段上（内核的 `slots` 只带名字）。

> **`slots[].why`（每块"为什么排在这个时段"）已经能读到了**（2026-10-15）：
> AGENT 侧新增了只读子命令 **`wall`**（"大屏只读视图：规划快照完整结构（含每块的 why）"），
> 契约写在它的 docstring 里 —— 只读零副作用、`why`/`gist` 缺省时**省略**（不写空串）、
> `task` 不截断（`context.last_plan` 截 40 字是给模型省 token 的，`wall` 不省）。
> 实测线上 11 块 **11 块都带 `why`**，形如
> `"白天满课，周四上课前唯一整块；本属轻活但题量大（6 题一页A4）"`。
>
> 之前为什么读不到：`context.last_plan` 是**给模型看的**（最多 12 块，且读回时在
> `AGENT/qyn.py` 的 `_plan_view` 里把 `why` 丢掉了）。**别再回头用 `context` 拼规划** ——
> 大屏用 `wall`，模型用 `context`，两条路各司其职。

内核失败时**不返回 5xx**，而是 `{ok:false, error:"…"}` —— 屏幕要能显示"读不到"，
而不是白屏。

## 怎么跑

```bash
# 手工（调试）
python3 CENTRAL/GUI/time-panel/serve.py --port 8091 \
        --kernel ~/qyn/agent/current/AGENT/qyn.py --days 3

# 中控机上由 systemd 用户服务托管
systemctl --user status  qyn-time-panel
systemctl --user restart qyn-time-panel
```

内核路径用 `~/qyn/agent/current/AGENT/qyn.py`：那里的 `data` 是**指向 `~/qyn/agent/data`
的符号链接**，所以读到的是**活数据**，不是发布快照。

## 屏幕上显示什么

- **NEXT**：下一节课（课程名 + 时间 + 地点 + 内核给的 `starts_in`）
- **DUE**：待办清单（内核已按紧迫度排序；`urgent` 红闪、`soon` 黄；附 `gist` 一行摘要）
- **ALERT**：内核的确定性异常清单（没有就不占位置）
- **AGENT 面板**（第四栏）：Agent 的**规划** —— 主旨（`focus`）+ 覆盖范围（`horizon`）+
  触发原因（`triggered_by`）→ **建议执行列表**（`slots[]` 按天分组，每块带详细任务 `gist`
  与"为什么排这儿"）→ **规划依据**（`planning_rules[]`，含"用户口述 2026-10-11"这样的来源）
  → **碰了软偏好 / 排不上**（`brief.warnings[]` / `review.unscheduled[]`）

## 跨通道并发：一把 flock + 幂等 + 忙时 429（用户 2026-10-20）

三个入口会跑**同一个 Agent**：① 墙上的对话（本桥）② 微信来消息 ③ 07:00 定时
（②③ 都走 `CENTRAL/scripts/qyn-agent-tick.sh`）。内核本身**没有锁**，所以必须由入口串行化。

| 机制 | 实现 |
|---|---|
| **全局锁** | 同一个 flock 文件 `~/qyn/agent/logs/agent-run.lock`。桥非阻塞拿（`LOCK_EX\|LOCK_NB`），tick.sh 用 `exec 9>` + `flock -n 9` |
| **拿不到怎么办** | 桥 ⇒ **HTTP 429** + `Retry-After: 60`（`{"ok":false,"busy":true}`，**不排队、不自动重试**）；tick ⇒ 记一行"本次跳过"并 `exit 0`（丢掉的那轮由 30 分钟守卫补跑） |
| **幂等** | 请求带 `client_msg_id`（屏幕每次发送生成）。同一 id 重复到达 ⇒ 直接回上次结果（`duplicate:true`，**0 秒**、不跑 Agent），窗口 24 小时、最多 200 条，存 `~/qyn/agent/logs/chat-idem.json` |

实测（2026-10-20）：首次 4.5s ⇒ 同 id 0.00s 命中 ⇒ 占锁 429 + `Retry-After: 60` ⇒ 释放后 3.7s 正常；
桥占锁期间跑 `qyn-agent-tick.sh message` ⇒ 立刻跳过（`另一轮…本次跳过`），释放后正常开跑 ✓。

渲染逻辑有测试：`node CENTRAL/GUI/screen/render-test.js`（含 TIME / AGENT 面板断言，共 263 项）。
