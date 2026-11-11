# 大屏交互方式（座位遥控：9 键无线小键盘 / 语音）可行性研究

> 建立：2026-10-20 ｜ 角色：屏幕/交互侧（`CENTRAL/GUI/`） ｜ 状态：**可行性研究（未开工）**
> 关联：[README.md](README.md)（GUI 开发区）、[screen/README.md](screen/README.md)（屏幕壳）、
> [../STATUS_WALL.md](../STATUS_WALL.md)（屏幕架构）、[../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)（三层架构与红线）、
> [../HARDWARE/README.md](../HARDWARE/README.md)（真机基线）、[time-panel/README.md](time-panel/README.md)（对话桥）、
> [SCREEN-PROBES.md](SCREEN-PROBES.md)（SERVICE 探针与对话现状）
> 实测环境：2026-10-20 凌晨对 `qyn-central`（Ubuntu 26.04 / GNOME 50 Wayland / 通用 ITX 主板 / 7 GB / Firefox 158 snap kiosk）只读探测 + 开发机侧实验；命令与原始输出见附录 A。
> ⚠️ **并行改动说明（2026-10-20 00:58）**：研究期间 `index.html` / `render-test.js` 已被另一路改动更新（**§2.3 的三个输入 bug 已修 + 合并为单一 keydown**，但**还没有** `e.code` 归一化/9 键映射/语音）。本报告里"现存 bug"与行号按**接手前版本**写，保留作为验收点；实施时以当前代码为准（当前实测 263 项断言通过）。

> ★★ **使用场景（2026-10-20 用户澄清，本报告以此为准）**：中控机在**两米外的机柜**上，
> 人**坐在座位**上；现在的痛点是**每次交互都要起身去拿键盘**。常用操作是
> **方向键**（此前说"小键盘"是口误）+ **输入文字**。用户设想：**一个 9 键无线小键盘 + 一个
> 语音输入麦克风**，就能在座位上完成交互。
>
> **本报告的结论**：这个方向**可行**，而且是"两个无线外设"而不是"换屏/改硬件"。
> 但真正决定好不好用的是三件事（详见 §2.11 / §3.2 / §3.9）：
> ① **9 键怎么分**（方向 + 开对话/发送/关闭 + 一个"按住说话"键）；
> ② **麦克风要跟着人走**（无线耳麦/领夹，而不是把桌面麦留在机柜上——2 m 外拾音会明显变差）；
> ③ **2 m 外的屏幕可读性**（当前对话字号 16 px 偏小，回复要放大到 ≥22–24 px）。
> 备选：**一个 2.4G 无线 mini 键盘 + 触摸板**（方向/点击/少量打字一个设备全包，但中文输入不如语音）。

---

## 0. 结论摘要

| 交互方式 | 可行性 | 结论 | 主要障碍 / 代价 |
|---|---|---|---|
| **★ 座位遥控套装：9 键 2.4G 无线小键盘 + 无线麦克风** | ✅ **可行（推荐）** | 9 键负责方向/开对话/发送/关闭/按住说话；语音负责"输入文字"。两个外设都是标准 HID/UAC，Linux 免驱；2.4G 在 2 m 距离余量很大 | 9 键键位要设计（§2.11）；麦克风必须"跟人走"（无线耳麦/领夹，§3.2）；屏幕要开"远距模式"（字号放大，§3.9）；先修 3 个现存输入 bug（§2.3） |
| 现有键盘的小键盘区（方向键/快捷键） | ✅ 可行（零硬件） | 页面已有方向键导航骨架；`e.key` → `e.code` 归一化后 NumLock 开/关一致 | 纯前端（约 150 行 + 24 条测试）；**但解决不了"人起身"**，只作为兜底/调试 |
| 独立 9/12/18 键无线小键盘（USB / 2.4G） | ✅ 可行 | 标准 HID，Linux/Wayland 免驱；2 m 用 2.4G 足够 | 有线 ¥25–80 / 2.4G ¥30–150；**蓝牙不推荐**（`bluetooth.service` inactive、断连无人自愈） |
| **语音输入（按住说话 → 本地 ASR → 可编辑草稿 → 确认发送）** | ⚠️ **可行，但缺一块麦克风** | **ASR 引擎不用自研、不用装包（只下模型）**：DSH 已自带 `sherpa-onnx-node` + SenseVoiceSmall INT8，实测中文识别 RTF **0.024**（5.6 s 音频 0.14 s 出字）；模型 239 MB 从 **hf-mirror** 可下（77 s，SHA-256 与 DSH 资产清单一致） | **当前没有任何麦克风**（`arecord` 2 s 采样全程 0）；**2 m 外别用机柜上的桌面麦**，要无线耳麦/领夹（约 ¥100–300）；新增一个小服务 + PTT + 全局并发锁 |
| **语音回话（TTS）** | ⏸ 暂缓 | 默认 sink 是 PCH 数字输出（IEC958），本机未确认有能出声的喇叭/音箱；要出声得另加音箱。与"语音输入"独立 | 可选 P2；2 m 外"听"比"看大字"更省事，可后补 |
| 触摸屏 / 手势 / 存在感知 | ❌ / ⏸ 不建议 | 现有 1600×900 面板**不是触摸屏**；手势不可靠且涉及隐私；存在感知只是"唤醒屏幕"不是输入 | 换屏成本高、页面要重做触摸目标，暂不做 |
| 手机通道的语音 | ✅ 已存在（不是墙边交互） | 微信语音消息由服务端给转写文本，`qyn-phone` 已能收（`PHONE/INTERFACE.md:227-229`） | 只能当"人不在座位"的兜底，不算墙的交互方式 |

**一句话**：**"9 键无线小键盘 + 无线麦克风"能在座位上完成"方向 + 说话"两件事**——小键盘是改页面（0 元），语音是"买一个跟人走的无线麦 + 起一个本机 ASR 小服务 + 一套按住说话的流程"（ASR 引擎与运行时**已经在机器上**）。两者都**不碰内核、不碰数据**，语义仍全部交给时间管理 Agent。

---

## 1. 现状：交互面盘点

### 1.1 现在有哪些输入通道

| 通道 | 入口 | 能做什么 | 谁在用 / 定位 |
|---|---|---|---|
| **鼠标** | kiosk 页面 | 悬停放大面板、选子框、滚轮滚动、齿轮设置、点 dock 开对话、对话层内选图/贴图 | 日常主输入（墙上备有无线鼠标 `<VID:PID>`） |
| **键盘（全尺寸，带小键盘）** | kiosk 页面 | `←/→` 切面板、`↑/↓` 移子框、`+/-` 滚选中框、`Enter` 开对话、`Esc` 关层 | 已有，但**没有任何提示**，且 NumLock 一开就失灵（见 §2） |
| **对话层** | `#dock` / `Enter` | 文字 + 图片 → `POST :8091/api/chat` → 时间管理 Agent 跑一轮 → 回复显示在墙上 | 2026-10-20 已接图片（Ctrl+V / 📎） |
| **手机微信** | `qyn-phone.service` | 文本 / 图片 / 语音（取转写）→ Agent；回复发回手机 | 不在墙边时的主通道 |
| **DSH Web UI** | `:8799`（只绑回环，要 SSH 隧道 + token） | 完整 DSH 界面（含可能的语音插件） | 人工调试入口，不是给墙用的 |

### 1.2 大屏页面（要改的就是这一个文件）

- `CENTRAL/GUI/screen/web/index.html`（单文件 ~3090 行）：五块面板（SERVICE / DATACENTER / NETWORK / TIME / AGENT）、自动轮转 + 鼠标悬停 + 键盘选择三套逻辑、底部 43 px `#dock`、左下 `#chat` 对话层。
- 键盘处理（**2026-10-20 00:58 并行改动后**）：已合并为**一个** `keydown` 监听器（`index.html:2905-2965`，`bindKeys()`），处理 Esc/Enter/方向键；`chatClose()` 已补 `blur()`（`:2625-2632`）。**仍未做**：`e.code` 归一化（NumLock 打开时小键盘仍漏）、9 键映射、PTT、远距模式。
- 渲染逻辑有 Node 测试：`CENTRAL/GUI/screen/render-test.js`（**当前 263 项断言**，2026-10-20 00:58 实测 `全部 263 项通过`；不用浏览器，`document.addEventListener` 在测试替身里是空实现，见 `render-test.js:50`）。
- 配置铁律：所有可调参数走 `setting("key")`（`index.html:854-910`）。

### 1.3 真机基线（2026-10-20 实测）

| 项 | 实测值 |
|---|---|
| 主机 / 系统 | `CENTRAL` · Ubuntu 26.04.1 · GNOME **50.1 / Wayland** · 通用 ITX 主板（4C/8T） |
| 内存 / 磁盘 | 7192 MiB 总量，约 4 GB 可用 · 根盘 207 GB 空闲 |
| 屏幕 / kiosk | 1600×900 · Firefox **158.0**（snap）`--kiosk http://127.0.0.1:8090/`（`qyn-kiosk.service`） |
| 音频栈 | PipeWire **1.6.2** + WirePlumber active；声卡 **主板集成声卡**；默认 source = 内置模拟立体声（无输入信号）；默认 sink = `alsa_output…iec958-stereo`（PCH **数字输出**；本机未确认有能出声的喇叭/音箱） |
| 麦克风 | **没有**：`arecord -D default -f cd -d 2` 采 2 s，**peak=0 / mean_abs=0**（全静音）；`lsusb` 无 USB 音频设备 |
| 示例课程输入 | `<VID:PID>` 示例 USB 键盘（全尺寸）+ `<VID:PID>` 2.4G 接收器（鼠标）；`/dev/uinput` 仅 root |
| 键盘状态 | `gsettings … numlock-state = false`（**NumLock 当前是关的**）、`remember-numlock-state = true`；IME = `xkb cn` + **libpinyin** |
| 软件家底 | Python 3.14.4（无 pip/venv）；**Node v24.18.0**（`~/qyn/agent/node/bin/node`）；无 git/curl，有 wget/arecord；apt 源可用但**安装需 sudo 密码** |
| 外网 | github.com 主页通，但 **Release 资产域 `objects.githubusercontent.com` 超时**；**huggingface.co TLS 握手失败**；**hf-mirror.com 通**（239 MB / 77 s）；pypi.org 单次 HEAD 200 但队友复测 **IPv4 超时**（可达性不稳；且本机无 pip/venv，pip 路线不作为主选）；`registry.npmjs.org` 200（未实际装过）；dashscope.aliyuncs.com 可达；出口是单点（上游路由器

### 1.4 三条不能越的线（来自 `docs/ARCHITECTURE.md`）

1. **语义只在智能体层**：按键、录音、转写都是"机制"；"用户说的是不是同一件事/该不该提前"只能由 Agent 判断（`docs/memory/DEV_STYLE.md` 同款原则）。
2. **写操作只经内核**：输入链路不许碰 `AGENT/data/*.json`；要改数据就让 Agent 经 `qyn_apply/qyn_mark/...` 去改。
3. **展示层零 CDN、依赖越少越可靠**：新增服务要能 24×7 无人值守；前端仍然全本地资源。

---

## 2. 座位遥控：9 键无线小键盘 / 方向键

> 说明：用户 2026-10-20 澄清——不是"数字小键盘"，而是**一个 9 键无线小键盘**（方向键为主），
> 放在座位上手边，人不用走到 2 m 外的机柜。下面 2.2–2.10 的按键机制（`e.code`、输入框闸门、
> 现存 bug）对"数字键盘"和"9 键小键盘"**同样适用**；9 键的具体分配见 §2.11。

### 2.1 事件路径（已实测）

```
键盘（USB HID）→ Linux evdev → libinput → GNOME 50/Wayland（xkb cn/us + libpinyin）
  → Firefox 158 kiosk（127.0.0.1:8090）→ page 的 document keydown
```

- 页面是**我们自己的 HTML/JS**，按键不经过任何快捷键守护进程；kiosk 全屏持有焦点。
- Firefox/GNOME 会先吃掉的键（`F11`、`Ctrl+T/W`、`Super`、`Alt+Tab` 等）拿不到 —— 所以键位设计**只用裸方向键/小键盘键**，不用 F 键、不带修饰键（详见 §2.5）。

### 2.2 核心发现：现在能用是"运气"，NumLock 一开就全漏

代码（2026-10-20 00:58 合并后的 `bindKeys()`，`index.html:2946-2954`）仍然只看 `e.key`。`e.key` 会随 NumLock 变，`e.code` 不会：

| 示例课程键 | `e.code`（恒定） | NumLock **关** `e.key` | NumLock **开** `e.key` | 现有代码 |
|---|---|---|---|---|
| 小键盘 8/2/4/6 | `Numpad8/2/4/6` | `ArrowUp/Down/Left/Right` | `"8"/"2"/"4"/"6"` | 关时**碰巧能用**；开时**全漏** |
| 小键盘 7/9/1/3 | `Numpad7/9/1/3` | `Home/PageUp/End/PageDown` | `"7"/"9"/"1"/"3"` | 未处理 |
| 小键盘 0 / . | `Numpad0` / `NumpadDecimal` | `Insert` / `Delete` | `"0"` / `"."` | 未处理 |
| 小键盘 +/− | `NumpadAdd/Subtract` | `"+"` / `"-"` | 同左 | **两态都能用**（现有 `+/-`） |
| 小键盘 Enter | `NumpadEnter` | `Enter` | `Enter` | **两态都能用**（现有开对话） |
| 主键盘方向键 | `Arrow*` | `Arrow*` | `Arrow*` | 能用 |

实测中控机 **NumLock 是关的**（`numlock-state=false`、`/sys/class/leds/input6::numlock=0`）⇒ 用户现在按小键盘"能用"；一旦误按 NumLock（或换机后状态变化）就整片失灵，而且**屏幕上没有任何提示** —— 这是"按了没反应"最可能的成因。

**对策**：以 `e.code` 为主键归一化（`Numpad8` 与 `ArrowUp` 映射到同一动作），页面不需要知道 NumLock 状态（也拿不到）。**9 键小键盘若发的是 Numpad1–9，也一并被这条规则吸收。**

### 2.3 三个输入 bug（**2026-10-20 00:58 已有并行修复；保留为验收点**）

| # | 原 bug | 原证据 | 后果 | 现状（00:58 并行改动） |
|---|---|---|---|---|
| 1 | `chatClose()` 只改 class，不 `blur()` | 原 `index.html:2625-2628` + CSS 隐藏 | 关掉对话后继续打字，字打进看不见的输入框 | ✅ 已修：`chatClose()` 补 `blur()`（现 `:2625-2632`） |
| 2 | 全局 `←/→` 无输入焦点判断，且 `preventDefault()` | 原 `:2897-2923` | 输入框里按左右键：光标不动、面板在背后切换 | ✅ 已修：`isEditing(e)` 放行（现 `:2910-2915`、`:2936-2937`） |
| 3 | `Esc` 有两个监听器 | 原 `:967` 与 `:2752` | 对话和设置都开着时，一下 Esc 两个都关，顺序不可预期 | ✅ 已修：合并为单一 `bindKeys()`（现 `:2905-2965`） |

> 这三条现在已经在树里修好了（**以代码为准**）；保留在报告里是因为它们正是"座位遥控/语音"上线的**验收点**：
> 关对话后无隐藏输入、输入框里方向键归输入框、Esc 只关最上层。

### 2.4 落点：把现有单一 keydown 抽成"纯函数 + 派发器"

> 现状：并行改动已经把三处合并成**一个** `keydown`（`bindKeys()`，`:2924-2965`），并且 `settingsToggle` 已可外部调用（`:933/970`）。
> **下一步不是再合并，而是把内联 if/else 抽成可测的纯函数**：

```
document.addEventListener("keydown", (e) => {
  const ctx = keyCtx();        // 只读 DOM 状态：chatOpen / settingsOpen / inInput / composing / busy
  const act = mapKey(e, ctx);  // 纯函数、无 DOM、无副作用 ⇒ render-test.js 可直接断言
  if (act === "none") return;
  if (act !== "enter") e.preventDefault();   // enter 交给原生表单/IME
  dispatchKey(act);            // 唯一副作用点：调已有 nextTab/moveFocus/scrollFocus/settingsToggle/chatOpen…
  lastKeyAt = Date.now(); focusSource = "key";   // 复刻现有"键盘锁鼠标"逻辑
});
```

- 为什么还要抽：现在 `bindKeys()` 里是 `if (e.key === …) nextTab(...)` 的内联链，**Node 测试只能测"按了之后状态变没变"，测不了"某个键该映射成哪个动作"**；抽成纯函数后 24 条键位断言才能钉住（尤其是 NumLock 两态、9 键布局、IME/输入框闸门）。
- `mapKey` 必须**纯函数**（不读 DOM、不调渲染）。
- 现有 `bindKeys` 是**第一个** `safeInit`（`index.html:3070`）：一个按键动作抛错不许让全部键失效，派发器里每个动作要 `try/catch`。

### 2.5 键位表（建议）

**保留（现有动作不变，增加小键盘别名）**

| 键 | 动作 |
|---|---|
| `←` `→` / 小键盘 `4` `6` | 上一个 / 下一个面板（绕回） |
| `↑` `↓` / 小键盘 `8` `2` | 子框光标上 / 下（TIME 三框、AGENT 四框、机器卡等） |
| `+` `−` / 小键盘 `+` `−` | 滚动当前选中的框（每次约 44 px，允许长按重复） |
| `Enter` / 小键盘 `Enter` | 对话关闭时 = 开对话；对话打开时 = 原生提交 |
| `Esc` | 关对话 → 关设置 → 清子框焦点（单一优先级） |

**新增（全部按 `e.code`，NumLock 两态一致）**

| 键 | 动作 | 理由 |
|---|---|---|
| 小键盘 `*` | 开/关设置面板 | 主键盘不会发 `NumpadMultiply`；2 米外点不到右下角齿轮 |
| 小键盘 `/` | 显示/关闭键位图例 | 可发现性入口（取代危险的 F1） |
| 小键盘 `7` / `1` | 选中框滚到顶 / 到底 | 与 NumLock 关时的 `Home`/`End` 语义一致 |
| 小键盘 `9` / `3` | 上翻页 / 下翻页 | 与 `PageUp`/`PageDown` 一致 |
| 小键盘 `.` | 取消（= Esc 的低风险别名） | NumLock 关时是 `Delete`，语义相近 |
| **按住小键盘 `0`（焦点不在输入框时）** | **按住说话 PTT**（阶段 2） | 0 是大键、好按；输入框聚焦时**放行**（打 0/Insert 不受影响） |

**明确不做**

- **数字键不做 1–5 直跳面板**：NumLock 开时才是数字（功能绑在 LED 上），且 libpinyin 中文输入时数字键用于**选候选词**，全局拦截必误伤（`gsettings` 实测 IME 含 libpinyin）。
- 不做 `F` 键主键位（kiosk 会吃 F1/F11 等）。
- 不做唤醒词/常开麦克风（隐私 + 误触发，见 §3）。

### 2.6 上下文闸门与 IME（最容易出错的地方）

规则（写进代码注释 + 测试钉住）：

1. **正在拼写**（`e.isComposing || e.keyCode === 229`）⇒ 一律 `none`（含 Enter），不抢 IME。
2. **焦点在 `input/textarea/[contenteditable]`** ⇒ 只放行 `Esc`（关层），其余原样交给输入框（数字、方向、Enter 都别拦）。
3. **设置面板打开** ⇒ 只放行 `Esc` 与 `*`，不切面板（设置里是 `<input type="number">`）。
4. **对话打开** ⇒ 不拦 `↑↓←→`/`Enter`/`+/-`（光标与表单优先）。
5. 带 `Ctrl/Alt/Meta` ⇒ `none`。
6. `e.repeat` 只对滚动/翻页放行；`←/→`、`↑/↓` 忽略长按重复（否则按住一秒钟飞过 5 块）。
7. 未识别键 ⇒ `none`，**不改浏览器默认行为**。

> IME 的真实行为（libpinyin 中文模式下数字选词、Enter 上屏）**只能人到屏幕前按**：Wayland 下无法远程注入按键（`/dev/uinput` 是 `crw------- root`，`ydotool/wtype/xdotool` 均未装、不能 sudo）。所以逻辑用纯函数测试兜住，IME 用手工验收（§2.8）。

### 2.7 可发现性与反馈（不然用户只会以为屏幕坏了）

- **上下文提示条**：改 `#dock` 文案即可（43 px 高、半宽 800 px，24 px 字号约 30 个全角字）：
  - 导航态：`←→ 换面板 · ↑↓ 选块 · ＋− 滚动 · * 设置 · ⏎ 对话`
  - 对话态：`⏎ 发送 · Esc 关闭 · 🎤 按住说话（阶段 2）`
- **键位图例层**：小键盘 `/` 唤出，半透明黑底 + 键帽表格（字号 ≥24 px），任意键/5 秒后消失。
- **动作反馈**：每个有效动作在被作用的面板/子框上闪一次 2 px 高亮 200 ms；dock 右侧短暂显示"面板 → NETWORK"。
- **NumLock 专属提示**：收到 `Numpad*` 但映射为 `none`、且不在输入框 ⇒ dock 提示"小键盘 NumLock 开时数字键不导航，方向键仍可用"。
- 设置页加"交互"分组与只读键位表；`?debug=1` 顶栏显示"最后收到的键"（现场区分"键没到"还是"动作没执行"；正常不显示）。

### 2.8 硬件选型

| 方案 | 免驱 / 可靠性 | 价格档（电商常见，未逐一核实） | 结论 |
|---|---|---|---|
| **先用现有键盘的小键盘区** | ✅ 已在用 | **¥0** | **第一步：先做软件改造，0 元验证** |
| **★ 9 键 2.4G 无线小键盘（座位用）** | ✅ 标准 HID；2 m 距离余量大 | **¥30–80** | **本次场景的主选**：放在座位手边，只做方向/Enter/Esc/PTT；**买"2.4G + 标准 HID + 可自定义键或发方向键"的**（§2.11） |
| **USB 有线数字小键盘** | ✅ 标准 HID；无电池、无断连 | ¥25–80 | 要独立设备且不介意线（2 m 线到座位）就买这个 |
| 2.4G 无线数字/12 键小键盘 | ✅ HID；有电池/干扰 | ¥30–150 | 第二选择（键多更好分配）；建议可换电池款、dongle 插后置 USB2 |
| 蓝牙小键盘 | ⚠️ 需配对 | ¥30–150 | **不推荐**：`bluetooth.service` 实测 inactive；无人值守断连无法自愈 |
| **2.4G 无线 mini 键盘 + 触摸板**（遥控器形态） | ✅ 标准 HID（键盘+鼠标一体） | ¥80–200 | **备选**：一个设备包方向 + 点击 + 少量打字；中文输入不如语音顺手 |
| 宏键盘 / 可编程小键盘 | ⚠️ 看固件是否标准 HID | ¥100–300 | 可选（可把某键映射成 `F13` 做专用 PTT，避免任何冲突）；别买需要 Windows 驱动/root 刷写的 |
| HID 媒体遥控器 | ✅ | ¥30–100 | 只适合"上一条/下一条"，方向键常发 `ChannelUp/Down`，不适合精确导航 |
| **USB 脚踏开关（单键 HID）** | ✅ 插上就是一个键 | ¥40–120 | **PTT 的最佳形态**（脚踩说话，双手自由；2 m 用 USB 延长线拉到座位下）；用 `keydown`+`keyup`，不要用计数/自动重复 |

判据：**必须出标准 HID 键盘事件**（页面只看得到 DOM 键盘事件）；中控机不能装包/没有 root，任何要驱动/udev 规则的方案直接排除。24×7 的关键是"没有状态"：有线 > 2.4G（可换电池）> 蓝牙（配对状态会丢）。

### 2.9 测试与验收

- **纯函数测试**（开发机跑，`node CENTRAL/GUI/screen/render-test.js`；中控机没有系统级 node，DSH 自带的那份在 `~/qyn/agent/node/bin`、不在默认 PATH）：
  - 在 `__t` 导出串（`render-test.js:61-89`）加 `mapKey / keyCtx / dispatchKey / setChatOpen / settingsOpenOf`；
  - 建议 24 条断言：NumLock 两态同动作、主键盘不回归、`+/-` 与 `Shift+=`、Enter/NumpadEnter、组合态忽略、输入框放行、设置层拦截、Esc 优先级、repeat 规则、未知键不抛异常、有效键才刷新"键盘锁"、`chatClose` 后失焦等（报告里给了编号 K1–K24）。
- **真机人工验收**（必须人到屏幕前，11 步）：NumLock 关/开各走一遍方向键；输入框里打中文、数字选词、Enter 上屏不重复提交；关对话后按 8 不应有隐藏输入；闲置 35 s 自动轮转恢复；拔插键盘 3 s 内可用；断电重启后 NumLock 状态变化仍正常；2 米外读提示/图例。
- 失败可观察性：键盘派发器注册失败要落到现有"⚠ 初始化 N 步失败"（`index.html:3039-3045`）；`?debug=1` 显示最后收到的键。

### 2.10 代价与分期

| 方案 | 改动量 | 结果 | 建议 |
|---|---|---|---|
| 最小补丁 | ~15 行（在 `bindKeys()` 里加 `e.code` 别名） | NumLock 开着也能用；9 键映射/提示/远距模式都没有（3 个 bug 已在 00:58 并行改动里修掉） | 只适合"今天先救急" |
| **完整派发器** | ~150 行 + 24 条测试 + 12 处小改 | 两态一致、9 键/PTT 位有处放、有提示/图例/远距模式，且键位可测 | **推荐**（在现有单一 keydown 上重构，不推倒重来） |

### 2.11 ★ 本次场景：2 m 外座位上的 9 键无线小键盘怎么配

**要覆盖的动作**（按常用程度）：① 方向（切面板 / 移子框 / 滚动）；② 开对话；③ 发送/确认；
④ 关闭/取消；⑤ **按住说话（PTT）**。共 5 类，9 键够。

**键位分配建议**（设备能发标准 HID 键或按键可自定义时）：

| # | 键 | 动作 | 备注 |
|---|---|---|---|
| 1 | `↑` | 子框上 / 上滚 | 已在页面里 |
| 2 | `↓` | 子框下 / 下滚 | 已在页面里 |
| 3 | `←` | 上一个面板 | 已在页面里（绕回） |
| 4 | `→` | 下一个面板 | 已在页面里（绕回） |
| 5 | `Enter` | 开对话；对话打开时 = 发送 | 已在页面里；小键盘 `NumpadEnter` 等价 |
| 6 | `Esc` | 关对话 → 关设置 → 清焦点 | 已在页面里（P0 统一优先级） |
| 7 | **`Space` 长按**（或设备上的大键） | **按住说话 PTT** | P0 只做导航；P1 加 PTT；输入框聚焦时放行（打空格正常） |
| 8 | `*` 或 `NumpadMultiply` | 开/关设置（可选） | 2 m 外点不到右下角齿轮 |
| 9 | `/` 或 `NumpadDivide` | 键位图例/帮助（可选） | 可发现性 |

- **如果 9 键发的是数字键盘编码**（很多"9 键小键盘"就是 Numpad1–9）：`e.code` 归一化后
  可以映射成**完全相同的动作**（Numpad8/2/4/6 → 方向，Numpad7/9/1/3 → 翻页/顶底等），
  页面不需要知道它是"数字键"还是"方向键"。
- **如果 9 键不够**：买 **12 键**（多 3 个做 `+/-`、设置、图例）；或 **9 键 + 脚踏**（脚踏专做 PTT，
  9 键全给导航）。**不建议**为了省键把"按住说话"做成组合键（2 m 外盲按容易错）。
- **硬件判据**（买之前逐条核对）：
  1. **2.4G 无线，不是蓝牙**（`bluetooth.service` 实测 inactive；断连无人自愈）；
  2. **标准 HID**：插上就是键盘，**不要装 Windows 驱动/专用软件**；
  3. 按键能发出 **方向/Enter/Esc/空格**（或可自定义成这些）；
  4. **dongle 用 USB 延长线拉到机柜外、朝向座位** —— 金属机柜和机箱会屏蔽 2.4G，插在柜内后置口很容易时断时续；
  5. 可换电池/Type-C 充电，带实体开关（防桌上压键误触、省电）；
  6. 有防滑脚垫/配重（放腿上或桌面不滑）；
  7. **★ 更稳的接法：座位侧放一个 USB 2.0 延长线/小 hub（2 m 到机柜），把 9 键键盘和无线麦的 dongle 都插在座位边** —— 无线链路只剩桌面 0.5 m，金属机柜屏蔽也绕开，只走一根 USB 线回机柜。
- **与现有键盘共存**：两个键盘发同样编码时页面**不区分设备**（同一动作），主键盘继续做兜底；不需要配对切换；Linux 允许多个 HID 键盘同时存在。
- **PTT 键的取舍（要提前定）**：9 键若用 `Space` 长按做 PTT，**对话输入框聚焦时会打空格**（我们让输入框"放行"）；
  所以更干净的做法是 **脚踏专做 PTT**（座位下，2 m USB 延长线），或买 **12 键/可编程小键盘**把某个键设成 `F13`
  这类"主键盘没有、输入框也不产生字符"的专用键 —— 这样 **对话开着也能按住说话**，不用先 Esc。
  如果坚持 9 键 `Space`：那就"先 Esc 关对话，再按住说话"，多一步但能接受。
- **成本**：9 键 2.4G 小键盘 **¥30–80**；座位侧 USB 延长线/hub **¥15–40**；若要 PTT 更顺手加脚踏 **¥40–120**；无线麦另计（§3.2）。
- **验收**：人坐在 2 m 外，只动 9 键就能：切一圈面板 → 进 TIME 子框 → 开对话 → 按住说话 →
  Enter 发送 → Esc 关闭；全程**不碰机柜、不用鼠标**；连续按 100 次无掉键、无重复。
- **待定**：具体 9 键布局/型号（先用手头键盘把软件改好、**用现有键盘验证同一套键位**，再下单）。

### 2.12 2 m 外的屏幕可读性（"远距模式"）

**问题**：现在对话消息是 **16 px**、dock 提示 **12 px**；2 m 外看 1600×900，16 px 字高约 4.4 mm
（按 0.44 m 屏宽估），视角约 **0.12°**，偏小、需要眯眼/前倾；面板里的 20–30 px 大字反而没问题。

**建议（不换屏，只改页面；可做成设置项 `remoteMode`）**：

| 元素 | 现在 | 远距模式 | 理由 |
|---|---|---|---|
| 对话消息 `.msg` | 16 px | **≥22 px**（Agent 回复可 24–26 px） | 2 m 外清晰；40 字一条不费劲 |
| `#chat` 宽度 | 左下 50% | **2/3 屏或全宽**（至少回复区全宽） | 半宽 + 小字 = 2 m 外最难读 |
| 最近一条回复 | 在滚动日志里 | 对话层顶部加一条**大字"最新回复"**（26 px，不随滚动） | 不用滚、不用找 |
| dock 提示 | 12 px | **20–24 px** | 键位/状态要能远远看见 |
| 录音/识别状态 | `#chat-state` 13 px | **≥28 px** + 红色底条 | 坐着也知道"在录/在识别" |
| 回复正文 | 纯文本 | 可选 **TTS 朗读**（P2，需音箱） | 2 m 外"听"比"看"更省事 |

**验收**：坐在 2 m 外，不动地方，能读清一条 40 字 Agent 回复；能看清"正在录音/识别中/请确认"。

---

## 3. 语音输入

### 3.1 麦克风：当前设备上**没有**

| 证据 | 结果 |
|---|---|
| `arecord -D default -f cd -d 2` 采 2 s | `peak=0`、`mean_abs=0` ⇒ **全静音** |
| 为什么静音（排除了"软件静音"） | `Capture Switch=on`、`Capture Volume=39/63 (12 dB)`、`Rear Mic Boost=0 dB`；**`Rear Mic Jack=off`、`Front Mic Jack=off`**（HDA 插孔检测）⇒ **示例课程没插麦**，不是静音保护 |
| `lsusb` | 只有键盘与 2.4G 接收器，**没有 USB 音频设备** |
| `/proc/asound/card0/codec#0` | 主板集成声卡 有 **Rear Mic / Front Mic / Line In**，两个 Mic 口 `Pin-ctls: 0x24: IN VREF_80`（偏置已开） |
| 规格 | `arecord --dump-hw-params` 实测 **16 kHz / mono / S16_LE 直接支持** |
| `wpctl status` / `@DEFAULT_AUDIO_SOURCE@` | 有一个可录的 Source（56 "内置音频 模拟立体声"，无信号）；但 **`wpctl inspect @DEFAULT_AUDIO_SOURCE@` 实测解析到 id 55（`alsa_output…iec958-stereo`，`media.class=Audio/Sink`）** —— 默认源指向不对。插 USB 麦后**必须** `wpctl set-default <新 source>` 并确认 `media.class=Audio/Source`，否则仍可能录到空源 |

⇒ **语音输入的第一件事是买一个麦克风**（或临时把 3.5 mm 麦插前面板）。这不是软件能补的。

### 3.2 麦克风选型（**2 m 外坐座位场景：麦要跟人走**）

| 方案 | 优点 | 缺点 | 价格档 | 建议 |
|---|---|---|---|---|
| **★ 2.4G 无线耳麦（USB dongle，标准 UAC）** | 麦跟着人走，嘴到麦 5–10 cm，**2 m/房间距离无关**；拾音最稳、抗环境噪声；免驱 | 要戴一个轻耳机（可只戴麦杆/单耳）；要充电 | **¥100–300** | **本次场景主选**（坐着说话最省事） |
| **★ 2.4G 无线领夹麦（USB 接收器）** | 夹衣领、双手自由；不用戴耳机 | 接收器必须是 **UAC（插上就是声卡）**，不少是 3.5 mm/相机用；要充电 | ¥100–300 | 主选备选（讨厌戴耳机就选它） |
| USB 桌面麦 + USB 延长线放到**座位边** | 便宜、音质好 | 座位边要占一块地方；2 m 线要走好；**不能留在机柜上** | ¥60–160 + 延长线 ¥10–20 | 次选（不想戴东西） |
| USB 会议全向麦放**座位侧** | 2–3 m 拾音、硬件降噪/AEC、静音灯 | 体积大、贵；要 2 m USB 线拉到机柜 | ¥250–700 | 想"放桌上不用戴"就选它 |
| 3.5 mm 领夹/头戴麦 + 2 m 音频延长线插 主板集成声卡 | 最便宜、即插即用（声卡偏置已开） | 机箱 EMI/风扇底噪；要调 Boost/Capture；线跨 2 m | ¥30–90 | 仅临时验证 |
| 蓝牙麦克风/耳机 | 无需线 | 配对/延迟/断连，24×7 不可靠；`bluetooth.service` inactive | — | **不推荐** |

**要求（买之前核对）**：**标准 USB Audio Class（免驱 UAC1/2）**、16 kHz 及以上、**有示例课程静音键**、不要"仅 Windows 驱动/游戏声卡"。

**距离怎么选**（本场景的关键）：**麦要放在离嘴近的地方**，不是放机柜上——
- 嘴到麦 ≤0.3 m：领夹/头戴耳麦（最稳，2 m 距离完全无关）；
- 0.5–1 m：桌面麦放在**座位边**（用 USB 延长线）；
- ≥2 m：用会议麦（放座位侧）或把耳麦/领夹戴在身上；**不要把普通桌面麦留在机柜上靠"抬增益"硬收**（底噪会先爆）。

### 3.3 ASR：**不用自研、不用装包 —— DSH 已经带了**

这台机器的 DSH（`~/qyn/agent/cli/node_modules/@deepseek-ai/`，v0.2.0-rc.2）自带一整套本地语音包：

| 包 | 作用 |
|---|---|
| `dsh-experimental-speech-to-text-sensevoice` | **本地 SenseVoiceSmall ONNX**（INT8 / FP32）+ Silero VAD，托管一个 Node 工作进程 |
| `sherpa-onnx-node` + `sherpa-onnx-linux-x64` | 原生推理运行时（**已随 DSH 安装**，含 ONNX Runtime，不需要 Python/编译器/模型转换） |
| `dsh-experimental-api-speech-to-text` | 带认证的浏览器语音 Remote（16 kHz 单声道 PCM16 WAV；音频只在内存、不成会话事件） |
| `dsh-experimental-voice-input-bundle` / `client-ui-voice-input` | DSH Web UI 的麦克风控件（"录完插入可编辑草稿"，不自动发送、不常开麦） |

这些包**默认禁用**；但运行时和模型清单都在本机。模型资产（`…/sensevoice/runtime/assets.json`）：

| 文件 | 体积 | 来源 |
|---|---|---|
| `model.int8.onnx` | **239,233,841 B（~228 MiB）** | `huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`（固定 revision + SHA-256；**下载走 hf-mirror**） |
| `tokens.txt` | 315,894 B | 同上 |
| `silero_vad.onnx` | 1,807,522 B | `huggingface.co/csukuangfj/vad`（固定 revision + SHA-256；**下载走 hf-mirror**） |

**实测（2026-10-20，中控机真机）**：

| 项 | 结果 |
|---|---|
| 模型下载 | 从 **hf-mirror.com**（HF 官方 TLS 失败）下 239 MB，**77 s**；三个文件 SHA-256 与 DSH 资产清单**逐字节一致** |
| 推理环境 | Node v24.18.0 + `sherpa-onnx-node` 1.13.8（原生库 32 MB 随 DSH 已装），`numThreads=4`，CPU（无 GPU） |
| 模型加载 | **0.93–0.97 s**（服务常驻后只需一次） |
| 解码（短句） | 5.59 s 音频 **132–143 ms**；域内句 3.6–5.0 s 音频 **89–118 ms** ⇒ **RTF 0.023–0.026** |
| 解码（长音频） | 53.37 s 英文 **2.68 s** ⇒ **RTF 0.050** |
| 内存 | 进程 RSS **~400 MB**（模型加载后）；含 VAD/Node 的峰值 **559 MB** |
| 输出形态 | 带标点、数字归一（"8 点""10 号"）；语言可选 auto/中文/英语/粤语/日语/韩语 |
| 中文准确率（干净 TTS，5 句域内句） | **4/5 完全正确**；1 句错："下个月" → **"下各月"**（同音错） |
| 真实麦克风准确率 | **未测**（没有麦）；远场/噪声/口音只会更差 ⇒ 必须"草稿确认" |

> ★ 那个同音错就是**不做"语音直接发送"的实证理由**：干净合成音都会错，真实麦克风只会更差；而时间管理 Agent 会**写数据**，错一个字可能把作业登到错误日期，且用户几天后才发现。必须"转写 → 可编辑草稿 → 用户确认"。

**方案对比（为什么选它）**

| 方案 | 安装/依赖 | 中文 | 延迟/资源 | 结论 |
|---|---|---|---|---|
| **DSH 自带 sherpa-onnx-node + SenseVoiceSmall INT8** | **零安装**（运行时已随 DSH 落地；模型 239 MB 从 hf-mirror 下） | 好（实测） | RTF 0.024–0.050；~400 MB / 峰值 559 MB | **主选** |
| apt `whisper.cpp` 1.8.3 + ggml 模型 | 要 **sudo** 一次（deb 里 8 个二进制，**不含模型**）；模型在 HF（本机 TLS 失败） | **弱**：tiny/base 的日期/数字/课程名错得多 | 实测 53.37 s 音频：tiny RTF **0.175**、base **0.319**、small **1.366**；准的 turbo 估 ≥3 | 只作离线兜底，不必 |
| sherpa-onnx 预编译二进制 | Release 资产域超时 ⇒ **下不了** | — | — | ❌（也不需要：运行时已在） |
| Vosk 中文小模型 | 要 pip；本机无 pip/venv，且 pypi 可达性不稳 | 名字/长句差 | 轻 | 排除 |
| FunASR / torch | 要 pip + 数 GB 依赖 | 好 | 内存吃紧（4 GB 可用） | 排除 |
| 云端 ASR（阿里/腾讯/讯飞/火山） | 要账号/Key/费用；端点可达 | 最好 | 网络依赖；音频出内网 | 备用，默认关闭（出口是单点） |
| DSH Web UI 自带语音（`:8799`） | **零代码**：`qyn-web` profile 加一行 bundle + `modelOrigins:["https://hf-mirror.com"]` | 同 A | 同 A | 只对 DSH Web UI 生效，与大屏无关；**可顺手开**，但本方案不复用它的 RPC 层 |

### 3.4 录音链路（已实测可行）

```
按住 PTT（页面 keydown / 屏幕 🎤 按钮 / 脚踏）
  → 语音服务 spawn `arecord -D default -f S16_LE -r 16000 -c 1 -d 30 -t wav -`
  → 音频**直接走 stdout 管道**进内存（不落盘、无需事后删文件）
松开 PTT（keyup）
  → 对 arecord 发 SIGINT（文件模式实测收尾合法：0.89 s / 14309 frames；管道模式按其实际字节数截取，不依赖 RIFF size 字段）
  → 去掉 44 B WAV 头 → Int16→Float32 → VAD 切段 → 逐段 SenseVoice → 拼文本（~0.1 s）
```

- **为什么不用浏览器 `MediaRecorder`**：Firefox snap 要麦克风权限（portal）、录出来是 WebM/Opus（还得装 ffmpeg 解码，**本机没装**）、跨源上传还要处理 CORS；**原生 `arecord` 直接出 16 kHz WAV，零新依赖、零权限弹窗**。
- **插了 USB 麦必须做的事**：`wpctl set-default <新 source>`，并确认 `wpctl inspect @DEFAULT_AUDIO_SOURCE@` 的 `media.class=Audio/Source`（现在它实测解析到 id 55 的 IEC958 **sink**，默认源本身就不对）。**2.4G 无线耳麦/领夹 = 一个 USB 接收器（UAC）**，在 PipeWire 里和 USB 麦一样出现；接收器建议插**机柜外的 USB 延长线**（和 2.4G 小键盘 dongle 同理，金属柜会挡）。
- 增益：USB 麦用自带增益；3.5 mm 麦要 `amixer -c0 sset "Rear Mic Boost" 2` + `Capture 45`，用 `arecord --dump-hw-params`/峰值判断（安静 < -45 dBFS、说话 -20…-6 dBFS）。
- 可选增强：常驻服务保留 **300–500 ms 预录音环形缓冲**，避免"按住瞬间第一个字被吃"；代价是"麦克风在听"（只在内存、不落盘、有红色指示）。默认先不做，先测实际是否掉字。
- 时长上限 30 s（`-d 30` 兜底 + 父进程超时 kill）；静音/过短（VAD 无语音）回 `422 too_short/empty`，不自动重试。

### 3.5 服务形态与接口

**推荐：新增一个小服务 `CENTRAL/GUI/voice/`（Node，127.0.0.1:8092）**，只做"录音 + 转写"，不做任何语义。约 150–200 行。

```
GET  /health                       → {ok, engine, model_ready, mic:{present,muted,source,peak_dbfs},
                                      busy, disabled, rss_mb, last_error, rtf_last}
POST /api/voice/start              → {ok, session} | 409 busy | 503 no_mic/muted/disabled/model_not_ready
POST /api/voice/stop  {session}    → {ok, text, dur_ms, rtf} | 422 empty/too_short | 500 asr_failed
POST /api/voice/cancel {session}   → {ok}
POST /api/voice/disable {disabled} → {ok}          ← 隐私一键停用
POST /api/voice/transcribe         → 裸 WAV（16k mono PCM16）→ {ok,text,dur_ms,rtf}  ← 没麦也能自测
```

- 依赖：`sherpa-onnx-node@1.13.8` + `sherpa-onnx-linux-x64`。**优先 require DSH CLI 里已有的那份**（同一份运行时，零安装）；可在 `README` 写 `VOICE_SHERPA_DIR` 覆盖，便于 DSH 升级后调整。`registry.npmjs.org` 单次可达 200，但**未实际装过**，独立安装作为备选。
- 模型：**默认放 `~/qyn/.dsh/speech-to-text/sensevoice/`**（与 DSH 插件同一目录，一份模型两处用），`VOICE_MODEL_DIR` 可覆盖；**模型不进 git**；准备脚本按 DSH `assets.json` 的固定 revision + SHA-256 从 hf-mirror 下载（239 MB / 77 s）。
- 常驻策略：模型常驻（服务启动加载一次，RSS ~400 MB、峰值 559 MB）；只在 PTT 那几秒占 CPU 与麦克风；`MemoryMax=1500M` + `CPUQuota=250%`，失控时先杀语音不杀 Agent。
- systemd **用户级**单元 `qyn-voice.service`：`After=pipewire.service wireplumber.service`、`Restart=always`、`PATH` 加 `~/qyn/agent/node/bin`、日志 `~/qyn/logs/voice.log` 有界；`/var/lib/systemd/linger/user` 已存在 ⇒ **全程免 sudo**。
- **不选**"给 `time-panel` 桥加语音端点"：那是"只读桥 + `run_agent_turn`"，`CHAT_LOCK` 会阻塞到 900 s；ASR 是重 CPU，必须能独立重启/限流/降配。
- **不选** DSH Web UI 的语音 Remote：它是实验性 Typert/WebSocket + DSH token，`qyn`（headless）profile 没有 HTTP 路由；给 `:8090` 用要手写协议、把 token 塞进前端 JS，且 PTT 是一次 3–10 s 的请求-响应、用不上它的流式。**但我们复用它的"运行时 + 模型 + worker 配置"**——这是"用宿主已有能力、不自研"的正确姿势。若将来想在 DSH Web UI 里用语音：给 `qyn-web` profile 加一行 bundle + `modelOrigins:["https://hf-mirror.com"]`，**零代码**（只对 `:8799` 生效）。

### 3.6 PTT 交互流程（状态机）

```
IDLE/NAV ──按住 PTT──▶ REC（红色录音条 + 计时 + "松开结束"）
   REC ──松开──▶ ASR（≤1 s，显示"识别中…"）
   ASR ──成功──▶ DRAFT：打开对话层，转写文本填入输入框（可编辑、全选待确认）
   DRAFT ──Enter──▶ 发送（走 /api/chat，source=voice）
   DRAFT ──Esc/再按住──▶ 取消/重录
   REC/ASR ──超时/失败──▶ 显示原因 + "再说一次"（不自动发、不自动重试）
```

- **草稿确认，不自动发送**（理由见 §3.3 的同音错）。
- **PTT 触发方式（一期）**：

  | 方式 | 评价 |
  |---|---|
  | **屏幕上的 🎤 按钮长按**（`#chat-form` 里加一个按钮） | 0 成本、必好用；鼠标就在手边 |
  | **按住空格长按**（页面 `keydown/keyup`，焦点不在输入框时） | 像对讲机、手不用离开键盘；输入框聚焦时放行（打空格不受影响） |
  | **按住小键盘 0 / 9 键小键盘上的大键**（焦点不在输入框时） | 大键好按；与 P0 的派发器同一套闸门；**输入框聚焦时会打字，所以更推荐脚踏/专用 F13 键** |
  | **USB 脚踏开关**（映射 F13 等专用键，2 m 延长线拉到座位下） | **2 m 座位场景最推荐**：脚踩说话、双手自由，对话开着也能按（F13 不产生字符）；¥40–120 |
  | 系统级 evdev 热键 | ❌ 要 `input` 组 + udev（sudo）；Wayland 下全局热键不可控；kiosk 会吃键 |
- 录音时屏幕右侧（对话层未占用的半屏）显示大字状态："🔴 录音中 3.2 s · 松开结束"，2 米外能看见；转写后 `#chat-text` **全选**，直接打字覆盖。
- 输入框已有未发文字时**追加**而不是覆盖；识别结果为空/纯标点**不填**。
- 对话层已有 `chatBusy` 串行（`index.html:2668`）；语音轮次与文字轮次共用同一个 Agent 回合队列；语音 `/start` 并发第二路直接 **409**（人此时要"现在说"，排队 8 分钟比拒绝更糟）。

### 3.7 与 Agent / 并发 / 进度（**这部分是"已投运系统"的真正风险**）

来自本次同伴报告（`.runtime/tmp/feasibility/agent-runtime.md`，附录 B）的实测（62 个真实回合）：

| 指标 | 实测 |
|---|---|
| 墙对话延迟 | p50 **5.8 s**；空载 1.9 s；**写入/重排 17.6–33.6 s**；900 s 超时 |
| 定时/手机回合 | p50 **40.8 s**、max 216.8 s |
| 冷启动（每次 spawn） | 0.7–1.5 s；**模型占回合 92%** ⇒ 保活会话只省 ~1 s |
| 流式 | `dsh --json` 天生 NDJSON（session/status/text/tool_call/final，**step 级**）；桥现在用 `capture_output` 把流憋住了 |
| 并发 | **`CHAT_LOCK` 只锁墙内、只锁本进程；tick 链只有自己的 flock；内核 `qyn.py` 无进程锁** |

⇒ 语音接进来之前，**必须先补两件事**（否则多一个通道就多一份丢更新/重复登记的风险）：

1. **跨进程全局回合锁**：`~/qyn/agent/logs/agent-turn.lock`（`flock -n`），`time-panel` 的 `run_agent_turn` 与 `qyn-agent-tick.sh` 共用；忙 ⇒ 页面回 **429 `busy`（不排队、不自动重试）**，tick `exit 0` 等守卫补。
2. **幂等键 `client_msg_id`**：页面生成 UUID，服务端把 `id → 终态` 落 `~/qyn/chat-turns/`（TTL 20 min）；重发/超时重试直接回放，不再跑第二轮。

接口仍**复用 `POST /api/chat`**（加 `source:"voice"` / `client_msg_id` 字段；纯文字请求保持原样向后兼容），返回加 `error_code / turn_id / run_ms / queued_ms`。错误码：`400 empty` / `429 busy` / `503 model_unavailable` / `504 timeout` / `500 internal`。

**进度反馈（建议与语音一起上）**：把 `subprocess.run(capture_output)` 改成 `Popen` 逐行读 NDJSON → SSE 给页面（`thinking → tool → saying → final`），**DSH 零改动**。否则语音说完要干等 20–30 s，用户会以为没听见而重复说，进一步加重并发问题。语音租约建议 **120 s**（写入类 180 s），超时只"软取消"（前端停止等待、服务端跑完），**不硬杀**进程（杀在 apply 多文件落盘之间会造成半写）。

### 3.8 隐私与安全

- **默认不常听**：只有按住 PTT 才开麦；无唤醒词。
- **音频不落盘**：`arecord` 走 stdout 管道，内存里转写完即丢；日志不写音频、不写原始字节。
- **本机 ASR**：音频不出机器（云端方案才出内网；出口还是单点，不做出厂默认）。
- **一键停用**：设置里 `voiceEnabled=false` 时页面不显示麦克风/PTT 不响应；服务可 `systemctl --user stop qyn-voice`。
- **现有桥的安全缺口（顺带补）**：`:8091` 只绑回环，但 `Access-Control-Allow-Origin: *` + `text/plain` 无预检 ⇒ **同机任意网页都能指挥 Agent**（Agent 有写权限）。建议加 `Origin`/`Host` 白名单（`http://127.0.0.1:8090` 等，约 20 行），语音链路一样受保护。

### 3.9 测试与验收

**没有麦克风也能测（现在就能做）**

| # | 测什么 | 怎么测 | 判据 |
|---|---|---|---|
| 1 | 引擎能加载、能出字 | `POST /api/voice/transcribe` 喂 16 k WAV（测试 wav / Windows TTS 合成句） | 返回文本；RTF < 0.1 |
| 2 | 中文管线不崩 | 用 `~/qyn/voice-test/s1..s5.wav`（Huihui TTS 域内句）逐句比对 | 与 §3.3 一致：4/5 全对、1 句同音错 |
| 3 | 边界 | 0.1/0.3/40 s 静音或正弦、非 WAV、<44 B | `too_short/empty/too_large/invalid_input`，不崩、不涨内存 |
| 4 | 并发 | 同时 3 个 `/start` | 1×200 + 2×409，无僵尸 `arecord` |
| 5 | 无麦路径 | 不插麦 | `mic.present=false`、`/start` 503、按钮置灰 |
| 6 | 断网 | 预置模型后断网启动 | **照常工作**（本地推理）；只有"发送"那一步失败、草稿保留 |
| 7 | 模型缺失 | 临时 `mv` 走模型 | `model_not_ready`，不无限重试、不 OOM |
| 8 | 不拖垮 Agent | 转写同时看 `free -m` | 峰值增量 < 700 MB、`available` 仍 > 2 GB |

> ⚠️ espeak 之类"机器人音"只能证明管线通，**不能判中文准确率**。准确率看 Huihui 合成句（4/5）与**买麦后的真人录音**。

**真机验收（买到麦后按序做）**：插 USB 接收器/麦（`lsusb`/`arecord -l`/`wpctl status` 各多一项）→ 按硬件静音键确认 `/health` 报 `muted` → `wpctl inspect @DEFAULT_AUDIO_SOURCE@` 已切到新设备 → `arecord` 3 s 看峰值（安静 < -45 dBFS、说话 -20…-6）→ **坐在 2 m 外**按住 PTT 说一句 → 草稿出现 → 确认发送 → Agent 回复 → 拔麦提示"麦克风不可用"。**座位端到端验收（本次场景的核心）**：只带 9 键小键盘 + 无线麦坐到座位上，完成"切面板 → 进 TIME → 开对话 → 按住说话 → Enter 发送 → Esc 关闭"，**全程不起身、不碰鼠标**；回复在 2 m 外能读清。**域内词专项**（每句 3 遍记错误率）：`下周三下午三点提醒我交示例课程作业` / `10月25号之前交示例课程D实验报告` / `每周一三五早上八点有示例课程` / `下个月十五号交示例课程论文`。**真实麦克风下的中文 CER 目前未测**，这是唯一"可能不达预期"的验证项。

### 3.10 代价与分期

| 阶段 | 工作 | 依赖 | 估算 |
|---|---|---|---|
| 买麦克风 | 下单 USB 桌面麦（¥60–160，建议同款 2 支） | 用户拍板 | 1–3 天到货 |
| 语音服务 | `CENTRAL/GUI/voice/`（Node 150–200 行）+ 模型准备脚本 + systemd 单元 + 自检 | 模型下载（77 s） | 2–4 天（含测试） |
| PTT/页面 | 派发器加 PTT、录音条、草稿确认、`voiceEnabled` 开关 | P0 的派发器先落地 | 1–2 天 |
| 并发/进度 | 全局 flock + 幂等 + SSE（建议一起做） | 桥/tick 改动 | 2–3 天 |

---

## 4. 其他交互方式（"等"的候选）

| 方式 | 可行性 | 结论 / 理由 |
|---|---|---|
| **2.4G 无线 mini 键盘 + 触摸板**（遥控器形态） | ✅ | 一个设备包方向 + 点击 + 少量打字；¥80–200；**不想"两个外设"就选它**；中文输入仍不如语音顺手 |
| **无线耳麦/领夹（2.4G USB 接收器）** | ✅ | 2 m 外"麦跟人走"的最优形态；¥100–300；接收器要标准 UAC |
| **宏键盘 / 带标签的 6–12 键** | ✅ | 每个键一个专用动作（语音、重排、设置、对话）；¥50–150；可与 9 键并存；PTT 专用键最理想 |
| **USB 脚踏开关** | ✅ | PTT 最佳形态；¥40–120；插上就是一个 HID 键 |
| 旋钮（亮度/音量） | ⚠️ 要 DIY | 现成 USB 旋钮少；ESP32/Arduino 做 HID 旋钮可行（与 LED 控制那条外挂控制器思路同源），但属于新硬件项目，非本次必需 |
| 游戏手柄 | ✅ 但没必要 | HID 手柄可映射方向键，但体积/握持不适合常驻墙面；不如小键盘 |
| 触摸屏改造 | ❌ 暂不 | 现有面板非触摸；换 1600×900 触摸屏要钱，且页面依赖 hover、目标偏小，得为触摸重做一套布局 |
| 手势（摄像头） | ❌ | 可靠性/隐私/夜间光照都不过关，属"看着酷但没人用" |
| 存在感知（mmWave/蓝牙手机在家） | ⏸ 可选 | 只解决"人来亮屏/恢复轮转"，不是输入方式；可另立项 |
| 手机当遥控器（网页） | ✅ 但另做 | 墙页面只绑 `127.0.0.1`；要做手机遥控得另开只读/受限页面，与"墙边交互"是两件事 |
| 语音回话（TTS） | ⏸ P2 | 默认 sink 是 PCH 数字输出（IEC958），本机未确认有能出声的喇叭/音箱；`sherpa-onnx-node` 带 `OfflineTts`，但还要另下中文 TTS 模型；先把"输入"跑顺 |

---

## 5. 架构落点、部署与回滚

### 5.1 三层归属

| 层 | 本次新增 | 说明 |
|---|---|---|
| 智能体层（AI） | **不加代码**，只在 SKILL/AGENTS 层面补一句"语音转写可能有错字，先确认再写"（可选） | 语义仍全在 Agent |
| 数据工具层（内核） | **不改** `AGENT/qyn.py` / 数据文件 | 红线 |
| 控制层/装配 | 新 systemd 用户单元 `qyn-voice.service`（屏幕栈）；不改 DSH profile（不启用实验性语音 bundle） | 只装不启，`voiceEnabled` 默认 false |
| **展示/交互层（本次主战场）** | `CENTRAL/GUI/screen/web/index.html`（键位派发器、PTT、提示/图例）；新 `CENTRAL/GUI/voice/`（Node ASR 服务）；`CENTRAL/GUI/time-panel/serve.py`（source/client_msg_id、全局锁、SSE）；`CENTRAL/scripts/qyn-agent-tick.sh`（共用 flock） | 全部是"机制"，不判断语义 |

### 5.2 现网怎么更新（部署路径已核实）

- 屏幕/时间面板这一栈在 `~/qyn/current`（静态 release；现网文件是**就地更新**的：`~/qyn/current/screen/web/` 与 `time-panel/serve.py` 的 mtime 晚于 release 目录，见附录 A）。
- 小键盘 P0 只改 `index.html` → 覆盖 `~/qyn/current/screen/web/index.html` → `systemctl --user restart qyn-kiosk`（页面加载时定定时器，必须重载）。
- 语音 P1：新增 `~/qyn/current/voice/` + 模型到 `~/qyn/.dsh/speech-to-text/sensevoice/`（与 DSH 插件共用）+ `~/.config/systemd/user/qyn-voice.service` → `daemon-reload` → `enable --now`；页面加 `voiceEnabled` 设置（默认 off）。
- 回滚：页面回滚=恢复上一版 `index.html` + 重启 kiosk；语音回滚=停/禁用 `qyn-voice` + `voiceEnabled=false`；不涉及数据与内核，无迁移。

### 5.3 明确不做

- 不改 DSH 的 `qyn`（headless）profile、不启用实验性语音 bundle（保持 Agent 路径不变）；`qyn-web`（`:8799`）是否顺手开语音是**独立可选项**，只影响 DSH 自己的 Web UI。
- 不给墙页面引 CDN/框架；不新增 Python 第三方依赖。
- 不做常开麦克风、唤醒词、语音自动发送。
- 不许语音/按键链路直接读写 `AGENT/data/*.json`。

---

## 6. 风险清单

| # | 风险 | 等级 | 证据 / 缓解 |
|---|---|---|---|
| R1 | **跨通道并发丢更新/重复登记**（墙 + 手机 + 定时） | **高** | `CHAT_LOCK` 仅进程内、tick 仅自己的 flock、内核无锁（agent-runtime 实测）；**先补全局 flock + 幂等再上语音** |
| R2 | NumLock 状态不可见，键位时灵时不灵 | 高 | 现 NumLock=关（实测）；用 `e.code` 归一化 + 屏幕提示 |
| R3 | IME（libpinyin）与 Enter/数字键交互只能真机验证 | 中 | 逻辑上"输入框全放行 + 组合态全忽略"；人工验收 11 步 |
| R4 | 关对话后焦点残留、输入框方向键被劫持、Esc 双监听 | 中（现存 bug） | 与 P0 一起修（§2.3） |
| R5 | 语音识别域内词错（"下个月→下各月"实测） | 中 | 草稿确认 + Agent 回问；不自动发送 |
| R6 | 没有麦克风就什么都做不了 | 高（前置） | 先买麦；`/health` 里 `mic.present` 明确显示；没麦时按钮禁用并提示 |
| R7 | 模型下载依赖 hf-mirror / HF 不通 | 中 | 实测 hf-mirror 200、77 s、SHA-256 校验；下载脚本失败要能重试；模型放本机后离线可用 |
| R8 | 语音等 20–30 s 无反馈 → 用户重复说 → 并发更糟 | 中 | 立即显示"听到了/识别中"，SSE 进度（P1），租约 120 s |
| R9 | 现有桥 CORS `*` + 无 Origin 校验（同机任意页面可指挥 Agent） | 中 | Origin/Host 白名单；语音服务同样只绑回环 |
| R10 | 服务 24×7 稳定性（Node/模型/麦克风热插拔） | 中 | systemd `Restart=always`；`/health` 暴露 mic/model/rss；模型常驻（空闲保温）、异常重启 |
| R11 | 键盘/麦克风设备被拔插后 kiosk 不恢复 | 低 | 键鼠 HID 热插拔正常；USB 麦重插后 PipeWire 自动接管（需真机验证） |
| R12 | 把"语音命令词"硬编码进代码（违反 DEV_STYLE） | 低（设计约束） | **禁止**：转写文本原样交给 Agent，不在代码里做意图分类 |
| R13 | 新增服务让部署/回滚变复杂 | 低 | 只加一个用户单元，默认停用；`voiceEnabled` 开关；回滚见 §5.2 |

---

## 7. 分阶段实施建议

| 阶段 | 内容 | 需要买什么 | 验收判据 |
|---|---|---|---|
| **P0（立刻可做）** | ①把现有单一 keydown 抽成 `mapKey` 纯函数 + `dispatchKey`（`e.code` 归一化、9 键映射）；②§2.3 三个 bug 已在 00:58 并行改动里修，**复核验收即可**；③dock 上下文提示 + `/` 图例 + 动作反馈；④**远距模式（对话/回复 ≥22–24 px、状态 ≥28 px）**；⑤24 条 render-test 断言 + 人工验收（含 2 m 座位） | **¥0** | NumLock/9 键/主键盘都同一行为；输入框里方向键/数字/Enter 不被劫持；关对话后无隐藏输入；**坐在 2 m 外能读清回复、只按 9 键能完成整套操作**；263+24 项测试全绿 |
| **P1（要外设）** | ①买 9 键 2.4G 小键盘 + 2.4G 无线耳麦/领夹（或桌面麦放座位侧）；②`voice` 服务（录音+ASR，复用 DSH SenseVoice 运行时/模型）；③PTT（9 键大键 / 脚踏）；④转写草稿→确认发送；⑤全局 flock + `client_msg_id` + 429；⑥`voiceEnabled` 开关 | 小键盘 **¥30–80**；无线麦 **¥100–300**；脚踏可选 ¥40–120 | 按住说 → ≤1.5 s 出字 → 确认发送 → Agent 回复；**坐在座位上全程不起身**；没麦/断网/桥忙都有明确提示；不自动发送；并发时手机/定时不丢数据 |
| **P2（体验）** | ①SSE 进度（thinking/tool/saying）；②TTS 回话（需音箱）；③脚踏/宏键盘专用键；④预录音缓冲；⑤可选云端 ASR 兜底 | 音箱 ¥100–300 | 长回合能看到"正在查/正在登记"；语音回话可关；P2 不改变 P0/P1 的验收 |
| ⏸ 不做 | 触摸屏、手势、唤醒词、DSH Web UI 换前端、云端 ASR 作默认 | — | — |

---

## 8. 待用户拍板

1. **座位端设备形态**：**9 键 2.4G 无线小键盘**（¥30–80，推荐）还是 **2.4G 无线 mini 键盘 + 触摸板**（¥80–200，一个设备包方向/点击/少量打字）？建议**座位侧放 2 m USB 延长线/小 hub，把两个 dongle 都插在座位边**（¥15–40），无线链路最短、也绕开金属机柜。
2. **9 键怎么分**：按 §2.11 的建议（4 方向 + Enter + Esc + Space 长按说话 + 2 个可选），还是你手上的 9 键布局有固定键位（贴个图/型号给我，我按它设计）？9 键不够时要不要上 12 键/脚踏？
3. **麦克风形态（2 m 场景）**：**2.4G 无线耳麦**（¥100–300，最稳）/**2.4G 领夹麦**（¥100–300，不用戴耳机）/**USB 桌面麦放座位边 + 延长线**（¥60–160）/**会议麦**（¥250–700，放座位侧）？谁去买/什么时候到？
4. **语音范围**：本轮只做"语音输入"（推荐），还是要连"语音回话"（需另加音箱）？
5. **识别后**：确认"转写进草稿、用户按 Enter 才发送"（推荐，防"下各月"这类错字直接登记）？还是希望"说完就发"？
6. **PTT 触发**：**脚踏（座位下）最推荐**（¥40–120，对话开着也能按）；或 12 键/可编程小键盘上的专用 `F13` 键；若坚持 9 键 Space 长按，就要接受"先 Esc 关对话再按住说话"多一步。你倾向哪种？
7. **远距模式**：同意把对话/回复字号放大到 ≥22–24 px、`#chat` 加宽/回复区全宽吗？（也可以做成设置项，默认开）
8. **按键改造范围**：3 个输入 bug 已由 00:58 的并行改动修掉；要不要接着把 `bindKeys()` 重构为"纯函数 `mapKey` + 派发器"（~150 行 + 24 条测试，补 NumLock/9 键/远距模式），还是先 15 行 `e.code` 补丁？
9. **并发**：是否同意先补"跨进程全局回合锁 + `client_msg_id` + 429"？忙时**拒绝并提示（不自动重试、不排队）**——语音 `/start` 并发第二路直接 409，Agent 忙时页面提示"草稿已保留，稍后点发送"。
10. **安全**：是否顺带加 `Origin`/`Host` 白名单（`:8091` 现在任何本机网页都能指挥 Agent）？
11. **模型放哪**：共用 `~/qyn/.dsh/speech-to-text/sensevoice/`（一份模型两处用，推荐）还是独立 `~/qyn/voice/models/`？
12. **下一步交付**：这轮只要研究报告，还是接着出 `CENTRAL/GUI/voice/` 的代码 + `install.sh` + 自检（全程免 sudo）？
13. **要不要顺手开 `:8799` DSH Web UI 的语音**（给 `qyn-web` profile 加一行 bundle，零代码但只对 DSH 自己的界面生效）？
14. **研究遗留**：`~/qyn/voice-test/` 有 239 MB 已校验模型 + 5 句测试 wav，先留着当 `install.sh` 的初值（省一次下载），还是现在清掉？

---

## 附录 A：实测方法与原始输出（摘要）

- 环境：`ssh qyn-central`，只读；脚本经 `Get-Content -Raw … | ssh qyn-central 'bash -s'` 执行（注意 CRLF：末行会被 PowerShell 加 `\r`，脚本末尾放 `echo __END__` 兜底）。
- 键盘/NumLock：`gsettings get org.gnome.desktop.peripherals.keyboard numlock-state` → `false`；`/sys/class/leds/input6::numlock/brightness` → `0`；`/proc/bus/input/devices` 里键盘的 `capabilities/key` 覆盖 `KP0..KP9/KPENTER/KPPLUS/KPMINUS/KPSLASH/KPSTAR/NUMLOCK/方向键`。
- 麦克风：`arecord -D default -f cd -d 2 -t wav /tmp/x.wav` → `peak=0 mean_abs=0`；`amixer -c0 sget "Capture"` → `Front L/R: 39 [62%] [12.00dB] [on]`、`Rear Mic Boost` → `0 [0.00dB]`；`amixer -c0 contents | grep Jack` → **`Front Mic Jack: off`、`Rear Mic Jack: off`**；`lsusb` 无音频设备；`wpctl status` 仅一个无信号 Source（56 模拟立体声）、Sink=IEC958；**`wpctl inspect @DEFAULT_AUDIO_SOURCE@` 解析到 id 55（Audio/Sink）** ⇒ 默认源指向不对；`arecord --dump-hw-params` 显示 16 kHz/mono/S16 支持。
- 录音收尾：`arecord -f S16_LE -r 16000 -c 1 … &` → 1.2 s 后 `kill -INT` → WAV 头合法（0.89 s，14309 frames）；`arecord` 直连 stdout 管道可行（服务方案用管道、不落盘）。
- ASR：
  - 下载：`hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/2365bae…/model.int8.onnx`（239,233,841 B，77 s）；`tokens.txt`；`silero_vad.onnx`；SHA-256 与 DSH `assets.json` 一致。
  - 运行：`~/qyn/agent/node/bin/node` + `sherpa-onnx-node`（`~/qyn/agent/cli/node_modules/`；原生 `sherpa-onnx-linux-x64` 含 `libonnxruntime.so`），SenseVoice 配置照 `dsh-experimental-speech-to-text-sensevoice/lib/worker.js:70-100`。
  - 结果：`{"text":"开饭时间早上9点至下午5点。","audioSec":5.59,"loadMs":933,"decodeMs":143,"rtf":0.026,"rssMB":407}`；长音频 53.37 s 英文 2.68 s（RTF 0.050，峰值 559 MB）；域内 5 句见 §3.3。
  - whisper.cpp 对照（`apt-get download` + `dpkg -x` 到 /tmp，**未 install**）：53.37 s 音频 `-t 4` ⇒ tiny 9.36 s（RTF 0.175）、base 17.04 s（0.319）、small 72.92 s（1.366）；deb 不含模型、需 sudo 装。
- 目录：`~/qyn/voice-test/` 保留着已校验模型 + 5 句测试 wav（239 MB，**没删**，留给 `install.sh` 当首次安装的模型源）；正式实现放 `~/qyn/.dsh/speech-to-text/sensevoice/`（模型）与 `CENTRAL/GUI/voice/`（代码）。
- 2 m 可读性估算：1600×900 面板约 0.44 m 宽 ⇒ 0.275 mm/px；16 px ≈ 4.4 mm，2 m 外视角约 0.12°（偏小）；24 px ≈ 6.6 mm，约 0.19°（可读）。这就是"远距模式"要 ≥22–24 px 的依据（§2.12）。
- 现场证据：机柜在座位约 2 m 外（用户 2026-10-20 说明）；现有键盘/鼠标在机柜侧，所以"9 键 + 无线麦"的方案价值在于**不用起身**。
- 现网部署证据：`readlink ~/qyn/current` → `releases/20261013-192904`；`~/qyn/current/screen/web/`、`time-panel/serve.py` 的 mtime 晚于该 release ⇒ **现网是就地更新**（不是每次发新 release）。

## 附录 B：依据与参考

- 代码/文档：`CENTRAL/GUI/screen/web/index.html`、`CENTRAL/GUI/screen/render-test.js`、`CENTRAL/GUI/time-panel/serve.py`、`CENTRAL/scripts/qyn-agent-tick.sh`、`CENTRAL/GUI/SCREEN-PROBES.md`、`CENTRAL/HARDWARE/README.md`、`docs/ARCHITECTURE.md`、`docs/memory/DEV_STYLE.md`。
- DSH 语音资产：`~/qyn/agent/cli/node_modules/@deepseek-ai/dsh-experimental-speech-to-text-sensevoice/`（`runtime/assets.json`、`README.zh.md`、`lib/worker.js`）、`dsh-experimental-voice-input-bundle/README.zh.md`、`dsh-experimental-api-speech-to-text/README.zh.md`、`sherpa-onnx-node`。
- Web 规范：MDN `KeyboardEvent.key` / `KeyboardEvent.code`（<https://developer.mozilla.org/en-US/docs/Web/API/KeyboardEvent>）。
- 模型来源：HF 模型页（经 hf-mirror 访问）：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`；VAD：`csukuangfj/vad`。
- 同伴报告（本次研究产出，gitignored）：`.runtime/tmp/feasibility/input-path.md`、`voice-stack.md`、`agent-runtime.md`（含 DSH voice bundle 启用步骤、`/remote` 不复用理由、麦克风配置命令、whisper.cpp 实测与验收清单）。
