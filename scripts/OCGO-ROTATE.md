# OpenCode GO 账号轮切（固定 15 天 · 纯 Key 模式）

> 用户 2026-10-21："我有两个账号的两个 key…**轮切触发就是固定 15 天切换**"，
> 随后问："**所以基于 cookie 的就必须手动不断更新？**" —— **不必** ✓ 本文就是结论。

## 为什么不用 Cookie（关键结论 ✓）

用户给的约束是：**一个账号只能有一个 GO 订阅**。

⇒ 那么"工作区"这个维度**没有信息量** ✓（每个账号就一个 GO，拿账号级数字就是全部 ✓）
⇒ 而 **Cookie 只用来"按工作区拆明细"** ✓；它还会**过期**（实测 10-15 的 Cookie 6 天就 401 ✗），
   只能靠人反复从浏览器复制 ✗
⇒ **改用账号级 Key** ✓：`oc_sk_…` **不会过期** ✓ 一次配置永久有效 ✓

代价（明确接受 ✓）：**拿不到"明细接口"**（逐条调用记录 / 保留期）✗。
`report --days N` 那类"逐条+合计"的报表会退化成只有账号级额度 ✓。
额度、已用、重置时间、等级判定**全部不受影响** ✓（都来自 `go/status` ✓）。

## 轮切公式（两台机器同一套，纯日期决定）

```
账号序号 = (距 anchor 的天数 // 15) % 2
anchor = 2026-10-21      第 0 天 = 账号 0 = 新账号（WS02）
```

**不依赖任何"切换动作"是否成功** ✓ —— 机器重启、脚本漏跑都不影响，下次运行时按日期算就对 ✓。
两台机器的 **anchor 与账号顺序必须一致** ✗ 否则出现"用量按新账号、调用按旧账号"。

| 机器 | 管什么 | 配置位置（600，不进仓库 ✓） | 谁去切 |
|---|---|---|---|
| **中控机** | Agent 模型调用的 KEY | `~/qyn/agent/ocgo-accounts.env` | `qyn-ocgo-rotate.sh` + timer（每天 04:10，平时空操作） |
| **Windows** | 用量探针的 KEY | `~/.apiusage/accounts.json` | 推送脚本每 60 秒推前先算，变了才写 |

**要手动做的事：没有** ✓（除非又换新账号 ⇒ 加一条 key 进这两个文件 ✓）。

## 中控机侧

```bash
bash ~/qyn/agent/current/CENTRAL/scripts/qyn-ocgo-rotate.sh --dry-run   # 现在该用哪个账号
bash ~/qyn/agent/current/CENTRAL/scripts/qyn-ocgo-rotate.sh            # 真切（幂等）
```

* 切完**不需要重启任何服务** ✓：`tick.sh` 与桥的 `agent_env()` 都是**每轮现读** `agent.env` ✓
* 自动备份 `agent.env.bak-<时间戳>`；日志 `~/qyn/agent/logs/ocgo-rotate.log`

## Windows 侧

`CENTRAL/scripts/qyn-opencode-post.py`（每 60 秒一帧）：

* 按同一公式选账号 → 用 `OPENCODE_GO_API_KEY=<该账号 Key>` 喂给 `apiusage panel` ✓
* `APIUSAGE_GO_COOKIE` 指向一个**不存在的路径** ⇒ 连 401 都不会去试 ✓（旧 Cookie 文件留着备份）
* Key 模式下 apiusage 会给占位名 `（按 Key 直查）` ⇒ 推送时**用账号标签盖掉** ✓
  （所以屏上标题是 `OCGO 新账号（WS02）` ✓，不是 `OCGO KEY` ✓）
* 想立刻推：`python CENTRAL\scripts\qyn-opencode-post.py --once --verbose`

### 两个账号**同时上屏**（用户 2026-10-21）

每帧**跑两次 apiusage**：主账号（轮转生效的、Agent 正在用的）+ 另一个账号（只用于展示）。

* 主账号那个工作区带 `in_use: true` ⇒ 屏上 `svcPick()` **优先选它** ✓
  （以前按"剩余最少"挑 ✗ —— 两账号轮转时剩得少的不等于正在用的那个）
* 另一个账号的工作区追加进同一帧 ⇒ OCGO 卡**展开时**多出两行：
  `WS2 <另一个账号名>` + `MO  $剩余 百分比   (Nd)` ✓ = **月额度 + 月刷新倒计时** ✓
* 另一个账号取不到（key 失效 / 网络）⇒ 只推主账号，屏上那两行自然不出现 ✓ **不报错** ✓

### ⚠️ 改了推送脚本必须**重启常驻进程**

它由 Run 键在**登录时启动**、常驻内存 ⇒ 改完 `.py` 不重启就还是**旧代码**在跑 ✗：

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'qyn-opencode-post' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process "$env:LOCALAPPDATA\Microsoft\WindowsApps\pythonw.exe" `
  -ArgumentList "D:\path\to\repo","--interval","60" -WindowStyle Hidden
```

**只留一个实例** ✓ —— 多个实例会各自每 60 秒取两次数（实测曾出现 3 个 ✗）。

## 降级源（中控机，Windows 关机时顶上）★ 2026-10-22 修过

`CENTRAL/scripts/qyn-ocgo-fallback.py`（+ `qyn-ocgo-fallback.timer` 每 2 分钟）：

* **只在主源陈旧 > 300 秒时才推** ✓（主源每 60 秒一帧 ⇒ 平时是空操作，绝不抢 ✓）
* 帧上带 `fallback: true` ⇒ 屏上 RES 行显示 **`TOK`**（本机用 key 拉），主源则是 **`WIN`** ✓
* ★ 以前它用**自己那份 10-15 的 apiusage 快照 + 自己的旧 key** ✗ ⇒ 不跟轮切、屏上出现
  `（按 Key 直查）`、数字还是别的账号的 ✗（用户 2026-10-22 发现）。现在：
  * 用**真 apiusage**：`~/qyn/opencode-fallback/pkg`（从仓库 `OPENCODEGO/apiusage` 只读同步）
  * 账号/Key 跟 `~/qyn/agent/ocgo-accounts.env` 走（**同一个 anchor ✓**）
  * 和主源一样跑两次（当前账号 + 另一个账号）✓，工作区名写账号标签 ✓
* 它已**纳入仓库**与 `deploy/install-agent.sh` 的单元清单 ✓（以前是隐形单元 ✗）
* 日志：`~/qyn/agent/logs/ocgo-fallback.log`

## 屏上**绝不显示**探针的占位名

旧版 apiusage 在账号级 Key 模式下会把工作区名写成 `（按 Key 直查）` ✗。
两层都挡住了：降级源/主推送都会**用账号标签盖掉** ✓，屏幕端 `wsTagOf()` 还会把
这类占位名当空处理 ✓（测试 `屏蔽占位名` 钉住 ✗）。

## 换新账号时改哪里（唯一需要动手的场景）

| 加在哪 | 内容 |
|---|---|
| 中控机 `~/qyn/agent/ocgo-accounts.env` | `OCGO_KEY_3=oc_sk_…` + 把 `% 2` 改 `% 3`（脚本里）+ 顺序对齐 |
| Windows `~/.apiusage/accounts.json` | `accounts` 数组里追加 `{"id":"C","label":"…","key":"oc_sk_…"}` |
| 顺序 | 两边**必须同一个顺序**（第 0 个 = 当前生效的那个） |

## 屏上怎么看当前是哪个账号

OCGO 卡展开时标题 = **账号标签**（如 `OCGO 新账号（WS02）`）✓。
万一哪天出现 **`OCGO KEY`**，说明推送没读到账号标签（`accounts.json` 缺失/格式坏）⇒ 查该文件 ✓。
