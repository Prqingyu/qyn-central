# CENTRAL/dsh — 中控机 DSH 部署与运维说明

> 建立日期：2026-10-09 ｜ 最近更新：2026-10-15（新增 §8「中控机运维」）
> 关联：[../../docs/MIGRATION_TO_DSH.md](../../docs/MIGRATION_TO_DSH.md)（迁移总方案）、[../CONNECTION.md](../CONNECTION.md)（中控机连接手册）

---

## 1. 这是什么

要部署到中控机的 **DSH 资产**：无人值守用的 headless profile `qyn`，外加一个只绑回环的
Web profile（人工对话用，见 §8.3）。

它让中控机能以「信息中心值守员」的身份跑无人值守轮次 —— 语义判断由 DSH + 模型负责，
确定性计算与结构校验交给 `AGENT/qyn.py`。

> **目录现状（2026-10-13 搬迁后）**：装配件已从 `CENTRAL/dsh/` 搬到 `AGENT/dsh/`。
> **`CENTRAL/dsh/` 现在只剩这份 README**；插件、profile、彩排脚本都在 `AGENT/` 下。
> 全仓只有这一份目录树，其它文档只给指针。

```
AGENT/dsh/                      ← DSH 资产（源，唯一一份）
├── plugin/qyn-time/          工具适配器（11 个工具，唯一写入口 qyn_apply）
├── profiles/qyn/             运行档
│   ├── package.json            # bundles = dsh-base + dsh-headless
│   ├── cordis.yml              # 恒为 []（一切靠 patch 层组合）
│   └── cordis.patch.yml        # 我们唯一的补丁层：权限 + 人设 + 模型接入
└── rehearsal/                彩排 / 演示 / 转录 / 审计工具（本机验证用，真机部署不需要）

CENTRAL/dsh/
└── README.md                 ← 本文件（部署 + 运维说明）

.dsh/skills/qyn-*/            语义层（技能）；装配到 $DSH_HOME/skills，见 §3
```

> 🗄 **本机彩排脚本不在这里**：`rehearse.py` / `demo.py` / `webui.py` / `ask.py` /
> `context_report.py` / `transcript.mjs` / `audit_round.mjs` / `eval_write.py` / `measure_tools.mjs`
> 在 [`AGENT/dsh/rehearsal/`](../../AGENT/dsh/rehearsal/)（本机验证用，真机部署不需要）。
> 它们**仍然可用**（路径已按新位置改好），本文下面给出的命令用的是新路径。

> **本机彩排已通过**：这份 profile 在装好 CLI 的 Windows 上是能组合、能启动的
> （`rehearse.py` 的断言逐条见 §4；条数与实测命令见
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §六）。
> 真机的 Linux 沙箱 / systemd / 模型出口三件事，现状见 §8。

---

## 2. 部署映射

| 仓库内 | 中控机上 | 说明 |
|---|---|---|
| `AGENT/dsh/profiles/qyn/` | `~/qyn/.dsh/profiles/qyn/` | profile 落到 `$DSH_HOME/profiles/<name>`（另有生成的 `qyn-web`，见 §8.3） |
| `.dsh/skills/` | `~/qyn/.dsh/skills/` | **技能放这儿，不要放别处** —— 见 §3 |
| 整个 release（`AGENT/`、`docs/`、`PHONE/` …） | `~/qyn/agent/current/` | 代码；`current` 是指向 `~/qyn/agent/releases/<时间戳>` 的软链 |
| `AGENT/data/` | `~/qyn/agent/data/` | **数据外置**：release 里的 `AGENT/data` 是指向它的软链，换代不丢 |
| （环境变量） | `DSH_HOME=~/qyn/.dsh` | **隔离**，绝不用宿主的 `~/.dsh` |

> ⚠️ **两个 `current` 别混**：Agent 这套是 **`~/qyn/agent/current`**（单元里的真值：
> `QYN_REPO_ROOT=%h/qyn/agent/current`）；**`~/qyn/current` 是状态墙那套**
> （→ `~/qyn/releases/<时间戳>`，wall.db / qyn-wall.service 用）。
> 把后者的路径写进 `QYN_REPO_ROOT`，插件就找不到内核。

---

## 3. 技能放哪（关键设计，别改）

**放到 `$DSH_HOME/skills`，即中控机上的 `~/qyn/.dsh/skills`。**

为什么不是仓库里那个 `.dsh/skills`：`dsh-skill-filesystem` 判定「项目根」的规则是
**含 `.git` 的最近祖先，没有则退回 cwd**。中控机是 **tar 部署**（不带 `.git`）、
cwd 又由 systemd 决定 —— 两条都不可靠，一旦不对就是**静默"没有技能"**，排查起来很费时间。

而该 provider 的根目录表里，rank 400 是 `<dshHome>/skills`：

| Rank | 来源 | 路径 |
|---|---|---|
| 100 | project-dsh | `<项目根>/.dsh/skills` |
| 200 | project-agents | `<项目根>/.agents/skills` |
| 300 | custom | `Config.customSkillDirs` |
| **400** | **user-dsh** | **`<dshHome>/skills`** |
| 500 | user-agents | `<agentsHome>/skills` |

所以只要 `DSH_HOME` 配对，技能**无需任何配置**就能被发现，且 Windows / Linux 行为一致。
`cordis.patch.yml` 里因此**刻意不配** `customSkillDirs`。

---

## 4. 本机彩排（等真机之前的唯一验证手段）

CLI 有 Windows 版，所以可以在这台机器上把"能验的都验掉"。

```powershell
# 一次性：装 CLI（隔离在 .runtime/，不影响全局，也不碰宿主 ~/.dsh）
npm install --prefix .runtime/dsh-cli @deepseek-ai/dsh@0.2.0-rc.2

# 彩排（会自建 .runtime/dsh 作为隔离 DSH_HOME）
python AGENT/dsh/rehearsal/rehearse.py
```

`rehearse.py` 断言 6 件事：

| # | 断言 | 针对的坑 |
|---|---|---|
| 1 | profile 可组合（bundles → patch 层） | bundles 名字写错 |
| 2 | **我们的补丁真的生效**（权限预设 + 插件行出现在合成配置里） | **patch 的 `id` 写错会静默丢弃整段补丁** |
| 3 | **身份与准则装到 DSH 认的地方**（`$DSH_HOME/AGENTS.md` 与仓库根逐字一致，且没有单数 `AGENT.md` 干扰） | 文件名写成 `AGENT.md`（DSH 只认 `AGENTS.md`）；准则被内联进 persona（web 端被 agent preset 盖掉） |
| 4 | 无 `warn` / `unknown` / `unmatched` / `ignored` 告警 | 同上，负向校验 |
| 5 | `$DSH_HOME/skills` 布局正确且 frontmatter 齐全 | §3 那个静默失效 |
| 6 | profile **能真正启动**（会话创建 + turn 走完） | 只在组合层"看着对"、实际起不来 |

第 6 项在**没有模型凭据**时允许、且只允许停在 `MISSING_CREDENTIAL` ——
那恰好证明除模型出口外整条链路都是通的（真机的模型出口已于 2026-10-13 打通，见 §8.4）。

---

## 5. 已验证的 row id（来自 `dsh-base` 的 bundle patch）

patch 层用 `- id:` 定向覆盖，**必须用这些短名**（写包名会静默丢弃）：

```
agent  agent-default-model  agent-instructions  agent-loop  approval
attachment-local  authorization  bash-sandbox  command-compact  command-feedback
command-goal  commands  compaction-basic  config-editor  credentials
deepseek-account  deepseek-llm-api-extensions  fs-observation-policy  fs-sandbox
goal  goal-round-driver  hmr  image-offload  jobs  llm  llm-deepseek
llm-deepseek-account  llm-pi-ai  llm-retry  mcp-resources  otel  permission
plan-mode  plugin-manager  plugin-package-inventory-deepseek  ptc-runtime
pwsh-sandbox  repeat-tool-reminder  sandbox  sandbox-policy  session
session-checkpoint-policy  session-log-deepseek  session-persistence-jsonl
session-projection  session-projection-cache  session-query-sqlite
session-telemetry-otel  session-title  session-title-llm  settings  shell-env
skill  skill-badge  skill-filesystem  spill-local  spill-policy  storage
storage-domain  storage-json  subagent  subagent-fork-in-process
subagent-spawn-in-process  subprocess  system-prompt  timeout-policy  timer
token-meter  tool-bash  tool-fs  tool-fs-search  tool-goal  tool-jobs
tool-plugin-manager  tool-pwsh  tool-ralph  tool-result-pruner  tools
tool-skill  tool-subagent  tool-subagent-control  tool-subagent-fork
tool-subagent-list-agents  tool-todo  tool-web  tool-workflow  typert
typert-gateway  typert-loader  user-questions  web  web-fetch-http
web-search-deepseek  workflow-ptc
```

（`dsh-headless` bundle 额外改了 `system-prompt`、`tools`，并关闭 `hmr`。）

**两条硬规则**（实测确认）：

1. `- id:` 定向 patch **替换整行 config**，不是合并 ⇒ 每个键都必须重述。
   我们的 `system-prompt` 补丁因此把 headless 设的 `personaPrefix`/`personaSuffix` 一并重写。
2. 行顺序不决定加载顺序（激活由服务可用性驱动），只决定 patch 应用顺序。

---

## 6. 跑一次 demo（自然语言 → 工具 → 内核 → JSON）

一条命令，端到端把"用户说一句话 → 模型调工具 → Python 内核落盘"跑出来：

```bash
python AGENT/dsh/rehearsal/demo.py --check     # 体检：CLI / 凭据 / profile / 插件 / 技能
python AGENT/dsh/rehearsal/demo.py             # 预置演示（**不修改任何数据**）
python AGENT/dsh/rehearsal/demo.py --task "帮我看下现在有什么待办"
```

预置演示刻意选了**不改数据**的两条：

| 演示 | 验证什么 |
|---|---|
| "看一下现在有什么异常和待办…" | 模型会去调 `qyn_anomalies` + `qyn_state`（读链路） |
| "把「论文」标记为已完成。" | **保守原则**：系统里没有"论文"（只有"结课论文"），工具必须拒绝并返回 `known_tasks`，模型据此**回询用户**而不是猜（写链路的安全边界） |

凭据按此顺序取，**不回显、不入库**：

1. `.secrets/demo.key`（一行；`.secrets/` 已在 `.gitignore` 里）
2. 环境变量 `DEEPSEEK_API_KEY`
3. DSH 自己的凭据库

```powershell
# 一次性创建（你自己执行，key 不要贴进聊天）
New-Item -ItemType Directory -Force D:\path\to\repo | Out-Null
Set-Content -LiteralPath D:\path\to\repo -Value '<测试key>' -NoNewline -Encoding ascii
```

> demo 用的是 headless profile：`DSH_HOME` 隔离在 `.runtime/dsh`，
> **不碰宿主的 `~/.dsh`**，也不影响你正在用的桌面版。

---

## 7. 真机验证：留档（本机无法彩排）

| 项 | 状态 | 说明 |
|---|---|---|
| Linux 沙箱内核 | ⏳ 仍未验证 | `defaultPreset: workspace-write` 在 Ubuntu 上能否真正限制写入（`pwsh-sandbox` 是 Windows 专属，Linux 侧走 bash/fs sandbox） |
| systemd 用户服务 | ✅ 已落地 | 5 个单元已装并 enabled、`Linger=yes`，`qyn-agent.service` 手动跑通一轮（`agent.log` 有真实输出）；`qyn-agent.timer` 下次 `2026-10-16 07:00` —— 怎么装/启/停/看日志见 §8.2 |
| **模型出口** | ✅ 2026-10-13 打通 | ~~中控机没有外网 ⇒ 云端模型连不上~~ 见 [../CONNECTION.md](../CONNECTION.md) §3 与本文 §8.4（每 5 分钟探测）。可选路线：A 中继常驻 / B 本机小模型 / C 只调度不推理 / D 推进 minieap |

模型接入的配置模板已写在 `AGENT/dsh/profiles/qyn/cordis.patch.yml` 末尾（注释态），
定方案后 uncomment 即可 —— 记得 `id` 只能用 `llm-pi-ai` / `agent-default-model`。

---

## 8. 中控机运维（部署 / 单元 / 入口 / 观测）

> 目标：**一个没参与过的人照着这一节能从零把中控机跑起来。**
> `qyn-agent.service` / `qyn-agent-web.service` 的 `Documentation=` 就指向本文件 —— 本节是它们的说明书。
> 真机事实（2026-10-15 只读核对）：`~/qyn/agent/releases/` 已有 16 个 release、
> `~/qyn/agent/current -> releases/20261015-174421`；`qyn-agent.timer`、`qyn-egress-probe.timer`、
> `qyn-agent-web.service`、`qyn-phone.service` 均 enabled，`loginctl show-user` 的 `Linger=yes`。
> `qyn-agent.timer` 的 `Next=` 是 2026-10-16 07:00（`LastTriggerUSec` 还空着 —— 它在 10-14 才装上，
> 中间那轮是**手动** `systemctl --user start qyn-agent.service` 跑的）。

### 8.1 部署：一条命令

在**开发机**（Windows，仓库根）执行：

```powershell
& CENTRAL\scripts\deploy-agent.ps1                        # 全流程：构建 → 传输 → 远端装配
& CENTRAL\scripts\deploy-agent.ps1 -SkipUpload            # 只在本机构建发布包，不发
& CENTRAL\scripts\deploy-agent.ps1 -Target qyn-central    # 默认目标就是它，一般不用给
```

三段各自做什么：

| 段 | 干什么 |
|---|---|
| 1/3 构建 | 把 `AGENT/`、`.dsh/`、`AGENTS.md`、`docs/`、`CENTRAL/deploy/`、`CENTRAL/scripts/`、`PHONE/` 收进 `.runtime/deploy-stage` → 清 `__pycache__` / `*.pyc` / `.bak` / `.runtime` → **排除 `PHONE/state`、`PHONE/logs`** → **断言包里没有 `credentials.json`**（有就直接构建失败）→ 打成 `.runtime/qyn-agent-<ts>.tar.gz` |
| 2/3 传输 | `ssh <目标> mkdir -p ~/qyn/agent/incoming` + `scp` 发布包 |
| 3/3 装配 | 远端解包到 `~/qyn/agent/incoming/<ts>/`，再 `bash CENTRAL/deploy/install-agent.sh <release>` |

`install-agent.sh` 的 7 步（**幂等**）：目录 → 落 release（`~/qyn/agent/releases/<ts>`）→
**数据外置**（`~/qyn/agent/data` 是唯一权威，release 里的 `AGENT/data` 只是指过去的软链）→
Node（用户本地，不需要 sudo）→ DSH CLI（装在 `~/qyn/agent/cli`，与 release 解耦）→
装配 `$DSH_HOME` 并跑 `rehearse.py` 验收 → 内核自检（`qyn.py validate` + `smoke_qyn.py`）→
手机通道（生成 `~/qyn/agent/bin/clawbot` 入口、状态外置到 `~/qyn/agent/phone-state`）。

- **幂等**：再跑一次 = 发一个新 release 并把 `~/qyn/agent/current` 指过去；数据、凭据、日志都不动。
- **不碰状态墙**：脚本只写 `~/qyn/agent/` 与 `~/qyn/.dsh/`；`~/qyn/current`、`wall.db`、
  `qyn-wall/collect/screen/kiosk/time-panel.service` 一概不碰。
- **前置**：开发机能免密 `ssh qyn-central`（见 [../CONNECTION.md](../CONNECTION.md)）、有 `tar`/`ssh`/`scp`；
  中控机有 `python3` 且能出网（否则第 3、4 步装 Node/CLI 会失败）。
- **首次**：中控机要有 `~/qyn/agent/agent.env`（`DEEPSEEK_API_KEY=...`，chmod 600）。
  单元里写的是 `EnvironmentFile=-%h/qyn/agent/agent.env`，那个 `-` 表示"缺了不报错" ——
  所以**没有这个文件不会报配置错，只会跑不起来**。

### 8.2 六个单元：装 / 启 / 停 / 看日志

单元文件在仓库的 `CENTRAL/scripts/systemd/`，运行时装到中控机的 `~/.config/systemd/user/`。

**装是自动的**：`install-agent.sh` 会把下面六个单元都装好（幂等 cp、内容变化才留 `.bak-qyn`、
`daemon-reload`、**只在内容变化时** enable/restart，并断言"已安装逐字节 == release"）。
所以正常情况下**不用手工 cp** —— 下面那段手工步骤只在"脚本没跑成"或"只改了单元想立刻生效"时用。

| 单元 | 类型 | 干什么 | 日志 |
|---|---|---|---|
| `qyn-agent.timer` | timer | **每天 07:00** 拉起一轮（`OnCalendar=*-*-* 07:00:00`，`Persistent=true` ⇒ 开机错过会补跑） | — |
| `qyn-agent.service` | oneshot（static） | 被上面那个 timer 拉起：`echo "现在我要做什么" \| dsh --profile qyn`，任务走 stdin | `~/qyn/agent/logs/agent.log` |
| `qyn-agent-web.service` | simple（常驻） | 常驻 DSH Web UI，绑 `127.0.0.1:8799`（人工对话；怎么开见 §8.3）。`Restart=on-failure` | `~/qyn/agent/logs/web.log` |
| `qyn-egress-probe.timer` | timer | **每 5 分钟**探一次模型出口（`OnBootSec=2min` + `OnUnitActiveSec=5min`） | — |
| `qyn-egress-probe.service` | oneshot（static） | 被上面那个 timer 拉起：跑 `CENTRAL/scripts/egress-probe.sh`，写 §8.4 的三个文件 | `~/qyn/agent/logs/egress.log` |

还有一个独立的 `qyn-phone.service`（手机通道接收方，常驻）：`install-agent.sh` 只把它**装入但不启用** ——
同一个微信账号只允许一个接收方，切换由人来做（见 `PHONE/INTERFACE.md`）。

**唤醒时刻跟着规划走，不是固定定时器**（用户 2026-10-15）：
内核 `python3 AGENT/qyn.py next-tick` 读最新规划快照，算出「第一块**结束时刻**晚于现在」的那一刻
（正在跑的那块 ⇒ 它的结束时刻；两块之间 ⇒ 下一块的结束时刻；没有可跟的边界 ⇒ 回落锚点 07:00）。
`CENTRAL/scripts/qyn-agent-tick.sh` 跑完一轮就问一次内核，并用
`systemd-run --user --on-calendar=<那一刻>` 排出下一次唤醒（链式自续，单元名 `qyn-agent-boundary-<ts>`）。
三种模式：`morning`（每日锚点，由 `qyn-agent.timer` 07:00 拉起）、`boundary`（被唤醒时）、
`guard`（每 30 分钟的 `qyn-agent-rearm.timer`：**边界没到就直接退出、不调模型**；到了才补跑，
兜住关机/上一轮失败/快照被清导致的断链）。日志：`~/qyn/agent/logs/tick.log`。

★ **到点唤醒是 `systemd-run --user` 起的 transient 单元 —— systemd 不会给它 `EnvironmentFile`**，
所以驱动器脚本必须自己加载凭据并**导出**：`set -a; . ~/qyn/agent/agent.env; set +a`。
点号加载只设 shell 变量、不导出，子进程 `dsh` 会报
`MISSING_CREDENTIAL: … OPENCODE_GO_API_KEY … is not set`。
2026-10-15 第一次真实到点唤醒就栽在这：消息轮有 `EnvironmentFile=` 所以全绿，只有这条会挂。

到点那一轮的**语义**（用户定：默认用户没做完）写在提示词里、不在内核：不要标记完成，
把那一块往后挪、重排剩余、存快照；默认不发消息，只有「排不下 / 今天到期的活没排上 /
需要用户决定」时才发一句。

```bash
# ① 手工装单元（正常由 install-agent.sh 完成；这段是兜底/临时用）
mkdir -p ~/.config/systemd/user
cp ~/qyn/agent/current/CENTRAL/scripts/systemd/qyn-agent.service \
   ~/qyn/agent/current/CENTRAL/scripts/systemd/qyn-agent.timer \
   ~/qyn/agent/current/CENTRAL/scripts/systemd/qyn-agent-web.service \
   ~/qyn/agent/current/CENTRAL/scripts/systemd/qyn-egress-probe.service \
   ~/qyn/agent/current/CENTRAL/scripts/systemd/qyn-egress-probe.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload

# ② 启：两个 timer 常驻 + Web UI 常驻
systemctl --user enable --now qyn-agent.timer qyn-egress-probe.timer qyn-agent-web.service

# ③ 无人登录也要跑 —— 少了这条，退出 SSH 后用户级单元会被停
loginctl enable-linger $USER

# ④ 停 / 手动跑一轮 / 改完重启
systemctl --user stop qyn-agent-web.service
systemctl --user start qyn-agent.service            # oneshot：立刻跑一轮，跑完就退
systemctl --user restart qyn-agent-web.service

# ⑤ 看
systemctl --user list-timers --all | grep qyn       # 下次什么时候跑
systemctl --user status qyn-agent-web.service       # 状态 + 最近日志
systemctl --user show qyn-agent.service -p WorkingDirectory -p Environment
journalctl --user -u qyn-agent-web.service -n 50    # 单元自身的事件
tail -f ~/qyn/agent/logs/agent.log                  # Agent 的正文输出
```

> `WorkingDirectory` 是 `%h/qyn/agent`（**稳定的父目录，不是 `current`**）——
> `current` 是软链，DSH 会把它解析成 `releases/<ts>`，每发一版就多一个工作区（实测攒了 10 个）。
> 插件找内核靠环境变量 `QYN_REPO_ROOT=%h/qyn/agent/current`，与工作目录无关。

### 8.3 Web UI：只能走 SSH 端口转发

对话界面由 `qyn-agent-web.service` 常驻，**只绑 `127.0.0.1:8799`**。
DSH 拒绝 `--host 0.0.0.0`（原话：*would expose remote code execution to the network*），
所以远程访问走隧道 —— 除了 SSH 什么都不暴露：

```powershell
# 开发机（或任何能 ssh 到中控机的机器）：开一条隧道，保持这个窗口别关
ssh -N -L 8799:127.0.0.1:8799 qyn-central

# 然后本地浏览器打开（token 每次重启会变）：
#   http://127.0.0.1:8799/?token=<token>
```

**token 在 `~/qyn/agent/logs/web.log`**（该文件已 `chmod 600`，只有中控机用户读得到）：

```bash
grep -o 'token=[A-Za-z0-9_-]*' ~/qyn/agent/logs/web.log | tail -1
```

收藏夹脚本 [`CENTRAL/scripts/qyn-agent-bookmark.ps1`](../scripts/qyn-agent-bookmark.ps1)
把上面这套自动化了（确认 web 单元在跑 → 取 token → 起隧道 → 写入口）：

```powershell
& CENTRAL\scripts\qyn-agent-bookmark.ps1           # 跑一次
& CENTRAL\scripts\qyn-agent-bookmark.ps1 -Loop     # 常驻：每 60 秒一轮（token 没变就什么都不做）
```

它写三处入口：`%USERPROFILE%\Favorites\<名字>.url`（浏览器"导入收藏夹"认它）、桌面同名 `.url`、
以及 Edge 自己的收藏夹 JSON（写前先备份成 `Bookmarks.bak-qyn`）。**token 变了自动更新** ⇒
让它常驻（`-Loop`，默认 60 秒一轮），或做成"登录时跑一次 + 之后每 5 分钟一次"的计划任务。
（`-IntervalSec` / `-Port` / `-Name` / `-Quiet` 可调；默认名字 `QYN Agent（中控机）`。）

### 8.4 出口探测：`出口=no` 是什么意思

```bash
tail -20 ~/qyn/agent/logs/egress.log   # 状态变了、或每 12 次（约 1 小时）记一行
cat ~/qyn/agent/logs/egress.last       # 上次结果：yes / no
cat ~/qyn/agent/logs/egress.state      # 连续失败次数（成功即归零）
```

- `出口=yes`：中控机 → 模型端点（默认 `opencode.ai` 的 `/zen/go/v1/models`）拿到 HTTP 200，模型那条路是通的。
- **`出口=no`：模型那条路断了。** 后果不止"探测失败"：那一轮 Agent 会直接 `TRANSPORT` 报错死掉，
  而**它自己发不出告警**（模型就是断掉的那条路）。所以探测刻意独立于模型：
  **首次失败**（`fails=1`）就通过 `clawbot send` 发一条手机消息 —— 该通道不依赖模型。
  同一场故障 30 分钟内不重复发（`QYN_EGRESS_ALERT_COOLDOWN`）；`fails>=3` 时日志里再升级记一行。
  实测故障形态是"每次只失败一次"（单次故障短于 5 分钟探测间隔），所以判据必须是 `>=1` 而不是 `>=3`。
  clawbot 不在 / 没登录 / 发送失败 ⇒ **降级只写日志**，探测本身照常按故障返回非零。
- 端点可用环境变量 `QYN_EGRESS_HOST` / `QYN_EGRESS_PATH` 覆盖（写在单元的 `Environment=` 里）。

### 8.5 大屏的只读契约（`wall`）

内核的 `wall` 子命令是给大屏（状态墙的 TIME / AGENT 面板）用的**只读**视图：规划 + 每块
`why` / `gist` / `urgency`。面板侧怎么取数、字段什么形状，见
[`CENTRAL/GUI/time-panel/README.md`](../GUI/time-panel/README.md)。
