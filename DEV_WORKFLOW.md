# 开发流程 — 在 Windows 开发，跑在 Linux

> 版本: v1.0
> 日期: 2026-09-15
> 关联: [CONNECTION.md](CONNECTION.md)、[STATUS_WALL.md](STATUS_WALL.md)

---

## 1. 两端的家底

| | Windows 开发机 | 中控机 CENTRAL（目标运行环境） |
|---|---|---|
| Python | **3.12.10** | **3.14.4** ⚠️ 版本不同 |
| pip | ✓ | ❌ **没有** |
| git | 2.55.0 | ❌ **没有** |
| node | v24.18.0 | — |
| VS Code | ✓（`code` 在 PATH） | — |
| psutil | — | ✓ 7.1.0 |
| 外网 | ✓（校园 WiFi） | ❌ |

## 2. 三条硬约束

> ✅ **2026-10-13 更新：约束 1 已解除** —— 内网已有外网出口（认证由路由器链承担），
> 中控机可直接 `apt` / `pip`。中继工具**保留作备用**（出口是单点）。见 [CONNECTION.md](CONNECTION.md) §3。

1. ~~**中控机没有外网** —— 装任何东西都要走局域网中继~~ ⇒ **已解除**（见上）
2. **中控机没有 pip** —— Ubuntu 新版把 python3 做成 externally-managed，系统 pip 装不进去，必须用 venv
3. **两端 Python 版本不同**（3.12 vs 3.14）—— 代码和依赖都要在**两个版本上都能跑**

---

## 3. 总原则

> **代码只有一份（Windows 工作区，git 管理），但设计成两边都能跑。**

```
┌─ 代码层 ──────────────────────────────────────────┐
│  D:\path\to\repo  (git，唯一的源)            │
│  纯 Python + 跨平台约定，Windows/Linux 都能跑       │
└────────────────┬─────────────────────────────────┘
                 │
      ┌──────────┴──────────┐
      ▼                     ▼
┌─ 本机运行 ──────┐   ┌─ 部署运行 ───────────────┐
│ Windows 上跑    │   │ 中控机上跑（24×7）        │
│ 连中控机的 MQTT │   │ systemd 托管              │
│ 用于快速迭代     │   │ 面向真实屏幕              │
└─────────────────┘   └──────────────────────────┘
```

**大部分迭代不需要碰中控机** —— 组件在 Windows 上跑起来，数据连中控机的 MQTT broker，效果一样看得到。只有验证 kiosk / systemd / 断电自恢复时才需要部署。

---

## 4. 代码约定（"两边都能跑"的前提）

这几条不是洁癖，每一条都对应一个真实的坑：

| 约定 | 为什么 |
|---|---|
| **`.gitattributes` 强制 `* text=auto eol=lf`** | 我们已经在 `.bat` 的 GBK 编码上栽过一次；**shell 脚本带 CRLF 在 Linux 上直接报 `bad interpreter`** |
| **全 UTF-8，文件头不加 BOM** | Windows 默认 GBK，中文会变乱码（也是刚踩过的坑） |
| **控制台输出强制 UTF-8**（`sys.stdout.reconfigure`） | Windows 控制台是 GBK，打任何非 ASCII 就抛 `UnicodeEncodeError` 直接崩。实测某台机器主机名带 emoji，一打印就挂 |
| **节点 ID 必须显式配置，不依赖主机名** | 实测主机名可能是 `某台机器`；节点 ID 会进 MQTT 主题，必须跨机器可预测 |
| **纯 Python，不用需要编译的 C 扩展** | 中控机没外网，装编译工具链很麻烦 |
| **平台差异收敛到 `sysinfo.py` 一个模块** | 不让 `os.name` / `sys.platform` 分支散落各处。**别叫 `platform`**——会遮蔽标准库 |
| **采集与上报分离**（metrics / sinks） | 同一份采集逻辑要被多个消费者复用，这是"复用到多台电脑"的前提 |
| **配置外置**（TOML + 环境变量），不硬编码路径 | Windows `\` vs Linux `/` |
| **依赖固定在 `requirements.txt`，装在 venv 里** | 中控机是 externally-managed；venv 也让依赖可整体搬迁 |
| **以 Windows 的 3.12 为兼容下限** | 中控机是 3.14（更新），写给旧的用不会错 |
| **前端零 CDN** | 局域网无外网，这是 STATUS_WALL.md §1 就定下的 |

---

## 5. 目录结构（拟）

```
CENTRAL/
├── CONNECTION.md            # 本机连接手册
├── STATUS_WALL.md           # 状态墙架构
├── DEV_WORKFLOW.md          # 本文
├── dsh/                     # 中控机 DSH profile + qyn-time 插件（装配层）
├── legacy/                  # 🗄 归档：过时文档与代码（读，不要跑）
├── scripts/
│   ├── check.sh             # 连接自检
│   ├── bootstrap-central.sh # 中控机一次性环境准备
│   └── deploy.sh            # 部署到中控机
├── tools/
│   ├── lan_http_proxy.py    # 局域网中继（apt 代理 + 文件投递）
│   └── relay/               # 投递给中控机的脚本
└── GUI/status-wall/         # ★ 状态墙应用（后续开发，见 GUI/README.md）
    ├── requirements.txt
    ├── config.example.toml
    ├── collect.py           # 入口：组装「采集 + 上报」，无业务逻辑
    ├── serve.py             # 入口：组装「状态层 + 展示层」
    ├── test_wall.py         # 端到端测试（9 项）
    ├── web/index.html       # 展示页（零 CDN，全内联）
    └── qynwall/             # 应用包
        ├── sysinfo.py       # ★ 平台差异的唯一收敛点
        ├── metrics.py       # 指标采集 —— 纯逻辑，不做任何 I/O
        ├── sinks.py         # 输出去向（stdout / mqtt / http）
        ├── state.py         # 状态层：最后已知值 + 陈旧度（SQLite）
        └── server.py        # 展示层：HTTP + SSE + ingest（纯 stdlib）
```

> ⚠️ **平台差异模块不能叫 `platform`** —— 会遮蔽标准库的 `platform` 模块。所以叫 `sysinfo`。
>
> **采集与上报分离**：`metrics` 只产出样本，`sinks` 只负责送出去。同一份采集逻辑
> 因此能被 MQTT 上报器、本地仪表盘、Agent、测试各自复用——这是"采集器复用到多台电脑"的前提。

---

## 6. 开发循环

### 6.1 快速循环（不出 Windows）

```bash
# 在 Windows 上跑某个组件，连中控机的 broker
python CENTRAL/GUI/status-wall/collect.py --sink mqtt --broker 127.0.0.1 --node win-dev
python CENTRAL/GUI/status-wall/serve.py --port 8080 --db :memory:
```

改代码 → 重启组件 → 浏览器开 `http://localhost:8080` 看效果。
**这一圈几秒钟，是日常迭代的主力，完全不碰中控机。**

### 6.2 部署循环（要验证真实环境时）

```bash
bash scripts/deploy.sh
```

做三件事：打包 → 传到中控机 `~/qyn/releases/<时间戳>/` → 切 `current` 软链 + 重启服务。
**保留历次 release，回滚就是把 `current` 指回去。**

---

## 7. 一次性 bootstrap（中控机）

中控机缺 pip、缺 venv，需要一次性准备（**出口已通，直接 `apt` 即可**；中继留作备用）：

```bash
# 中控机上（中继在 Windows 侧已启动）
sudo apt install -y python3-venv python3-pip
```

然后建 venv + 装依赖（**venv 仍然必须** —— externally-managed；pip 现在可直连，下面两行代理可省）：

```bash
python3 -m venv ~/qyn/status-wall/.venv
export http_proxy=http://192.0.2.10:8080 https_proxy=$http_proxy
~/qyn/status-wall/.venv/bin/pip install -r requirements.txt
```

**做完这一步，依赖就固化在 venv 里了，之后离线也能跑。** 这是关键——中控机不需要反复联网。

> 注意中继地址：Windows 的以太网 IP 是动态的（曾从 `.3` 漂到 `.6`）。`tools/lan_http_proxy.py` 已改成自动探测，但**建议给 Windows 也做 DHCP 保留**（CONNECTION.md §9）。

---

## 8. 部署到中控机

用 **tar over SSH**，不需要中控机装 git：

```bash
tar czf - --exclude=.venv --exclude=__pycache__ -C CENTRAL/GUI/status-wall . \
  | ssh central 'mkdir -p ~/qyn/releases/$(date +%Y%m%d-%H%M%S) && tar xzf - -C $_'
```

服务用 **systemd 用户服务**（`systemctl --user`），**不需要 sudo**：

```
~/.config/systemd/user/qyn-wall.service
```

需要一次性 `sudo loginctl enable-linger user`，让用户服务在无人登录时也常驻。

---

## 9. 为什么不推荐 VS Code Remote-SSH

**看着很美**：Windows 上编辑，实际跑在 Linux，没有同步问题。VS Code 也确实装了。

**但这个环境下很脆**：Remote-SSH 首次连接要在**中控机上下载 VS Code Server**，而中控机没有外网。可行的绕法是手工把 server 包传过去，但——

- VS Code 每次升级，server 版本对不上，**要重传**
- 没有外网，扩展市场也装不了装到远端

**结论：等中控机能自己上网（minieap 做完）之后再考虑。** 在那之前，§6 的两级循环足够用。

---

## 10. 待确认

- [ ] 中控机应用代码放 `~/qyn/` 还是别的路径
- [ ] 用 systemd **用户服务**（免 sudo）还是**系统服务**
- [ ] 是否给 Windows 开发机也做 DHCP 保留（中继地址会漂）
