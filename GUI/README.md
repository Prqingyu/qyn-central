# CENTRAL/GUI —— 后续开发区

> 建立：2026-10-13
> 关联：[../STATUS_WALL.md](../STATUS_WALL.md)（状态墙架构）、[../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)（三层架构）、[../legacy/README.md](../legacy/README.md)（归档区）

## 这里是什么

中控机**面向屏幕/人看的那一层**的开发目录。当前内容：
**状态墙 / 大屏**（挂在那块 HDMI 1600×900 上的常驻看板，架构见 [../STATUS_WALL.md](../STATUS_WALL.md)），
以及大屏的**交互方式研究**（小键盘 / 语音等，见 [INTERACTION-FEASIBILITY.md](INTERACTION-FEASIBILITY.md)）。

```
CENTRAL/GUI/
├── INTERACTION-FEASIBILITY.md  # ★ 大屏交互方式（小键盘/语音等）可行性研究（2026-10-20）
├── TASKS.md              # ★ 状态墙任务拆分（WBS）：契约 / 后端 / 前端 / 部署 / 质量
├── screen/               # 统一屏幕壳（:8090，面板 + 对话层 + 键位）
├── time-panel/           # TIME/AGENT 面板的只读桥（:8091，含 /api/chat）
└── status-wall/          # 状态墙：采集(collect) + 状态层(state) + 展示(server/web)
```

> 采集与展示**都在这一棵里**（`metrics` 只产出样本、`sinks` 只负责送出去、
> `state` 只存最后已知值、`server` 只读状态与推事件）—— 分层是为了让同一份采集逻辑
> 能被 MQTT 上报器 / 页面 / Agent / 测试各自复用，不是为了拆成多个目录。

## 现在到哪一步了

**S1 全链路已通**：采集 → 上报 → 状态层 → 页面，测试 9/9；kiosk 与 mosquitto 等真机项
（S1 尾项、S2–S5）在 [../STATUS_WALL.md](../STATUS_WALL.md) §8 排着。

**下一步怎么拆**（契约 / 后端 / 前端 / 部署 / 质量，含依赖与并行波次）见 [TASKS.md](TASKS.md)。
拆分里点出了三个决定做法的缺口：MQTT 只有"发"没有"收"、前端是单文件（挡住并行）、
"应有的在线性"写死在前端。

**交互方式（2026-10-20 新增研究）**：目标场景是**机柜在 2 m 外、人坐座位不站起**——
**9 键无线小键盘**负责方向/开对话/发送/关闭，**语音**负责输入文字（ASR 引擎与模型已在机器上，
DSH 自带 sherpa-onnx + SenseVoice；**唯一硬缺口是麦克风**，且 2 m 场景要选"跟人走"的无线耳麦/领夹）。
结论、键位表、接口草稿、分阶段方案与待拍板项见 [INTERACTION-FEASIBILITY.md](INTERACTION-FEASIBILITY.md)。

## 跑起来（本机，不出 Windows）

```bash
python CENTRAL/GUI/status-wall/serve.py --port 8080 --db :memory:
python CENTRAL/GUI/status-wall/collect.py --sink http --url http://127.0.0.1:8080/api/telemetry --node win-dev --interval 5
python CENTRAL/GUI/status-wall/test_wall.py
```

浏览器开 `http://127.0.0.1:8080`。细节见 [status-wall/README.md](status-wall/README.md)。

> ⚠️ 两条一直有效的约束（来自 [../STATUS_WALL.md](../STATUS_WALL.md) §1/§9）：
> **前端零 CDN**（局域网环境不能假设有外网）、**依赖越少越可靠**（展示层刻意用纯 stdlib）。
