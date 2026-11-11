# 状态墙 (qynwall)

信息中心各节点的状态看板。架构见 [../../STATUS_WALL.md](../../STATUS_WALL.md)，
开发流程见 [../../DEV_WORKFLOW.md](../../DEV_WORKFLOW.md)。
本目录属于 **`CENTRAL/GUI/`**（后续开发区，见 [../README.md](../README.md)）。

## 目录

```
status-wall/
├── requirements.txt
├── config.example.toml
├── collect.py            # 采集器入口：组装「采集 + 上报」，本身无业务逻辑
├── serve.py              # 服务入口：组装「状态层 + 展示层」
├── test_wall.py          # 端到端测试（内存库 + 随机端口，9 项）
├── web/
│   └── index.html        # 展示页，★ 零 CDN，全内联
└── qynwall/              # 应用包
    ├── sysinfo.py        # ★ 平台差异的唯一收敛点（Win/Linux）
    ├── metrics.py        # 指标采集 —— 纯逻辑，不做任何 I/O
    ├── sinks.py          # 输出去向（stdout / mqtt / http）
    ├── state.py          # 状态层：最后已知值 + 陈旧度（SQLite）
    └── server.py         # 展示层：HTTP + SSE + ingest（纯 stdlib）
```

**分层意图**：`metrics` 只管产出样本，`sinks` 只管把样本送出去，`state` 只管存最后已知值，
`server` 只管读状态与推事件。同一份采集逻辑因此能被 MQTT 上报器、本地页面、Agent、
测试各自复用；换上报方式只需加一个 Sink 子类。

**为什么展示层是纯 stdlib**（STATUS_WALL.md §5.5 写的是"FastAPI 或纯 stdlib"）：
中控机没有外网、没有 pip。S1 只需要"读状态 + 推事件"，`http.server` 够用，
依赖越少，离线环境越可靠。

## 跑起来

```bash
# 1) 只验采集（不出本机，不需要 broker）
python CENTRAL/GUI/status-wall/collect.py --once --node win-dev

# 2) 起状态墙（内存库，本地开发用）
python CENTRAL/GUI/status-wall/serve.py --port 8080 --db :memory:
#    浏览器打开 http://127.0.0.1:8080

# 3) 采集器推到状态墙（另开一个终端）
python CENTRAL/GUI/status-wall/collect.py --sink http --url http://127.0.0.1:8080/api/telemetry \
    --node win-dev --interval 5

# 4) 端到端测试
python CENTRAL/GUI/status-wall/test_wall.py

# 发布到 MQTT（主通道，需要 broker）
python -m pip install paho-mqtt
python CENTRAL/GUI/status-wall/collect.py --sink mqtt --broker 127.0.0.1 --node win-dev

# 用配置文件
cp CENTRAL/GUI/status-wall/config.example.toml CENTRAL/GUI/status-wall/config.toml
python CENTRAL/GUI/status-wall/collect.py --config CENTRAL/GUI/status-wall/config.toml
```

> **HTTP 与 MQTT 的分工**：MQTT 是主通道（retained + LWT，离线检测白送）；
> HTTP ingest 是**给装不了 MQTT 库的节点**的兜底（MC 服务器、未来的 STM32）。
> 两者最终落到同一个 `StateStore`，所以展示层只认一种数据形态。

### 离线是常态，不是故障

`state.py` 存的是**最后已知值**（upsert），不是时序。节点按需开机、不在线时，
页面显示最后值 + "多久以前的数据"，而不是空白；只有"本该在线却离线"才标红
（见 `web/index.html` 里的 `EXPECTED_ONLINE`）。

## 指标模型

见 [../../STATUS_WALL.md](../../STATUS_WALL.md) §5.2。两个容易误判为故障的点：

| 现象 | 真相 |
|---|---|
| `cpu.load1 = null` | **Windows 没有"平均负载"这个概念**，不是故障 |
| `net.*_kbps = null` | 首次采样没有参照点算不出速率，第 2 次起有效 |

### CPU 测量窗口为什么是 10 秒

`psutil.cpu_percent` 测的是**窗口内的平均值**。窗口太短会被瞬时抖动主导
（一次 GC、一次编译就能把 0.5 秒拉到 30%），数字没有参考价值。
默认用 10 秒窗口，代价只是启动时多等 10 秒——对 24×7 服务无所谓。

`--cpu-window` 可调。**循环采样时第 2 次起是非阻塞的**，测量窗口由 `--interval` 决定，
所以两者应设成同一量级。

## 为什么不采温度

**主板自带温控保护，温度不是可行动的信息。**

技术上还有一层原因：温度采集**因机器、因系统而异**，硬编码必然在别的机器上翻车——
实测（2026-09-15，Windows 11 25H2）：

| 数据 | Linux | Windows |
|---|---|---|
| CPU 温度 | `psutil.sensors_temperatures()` 可得 | ❌ psutil **没这个接口**；WMI 需管理员且常返回无效值；只能靠 HWiNFO / LibreHardwareMonitor 等**每台机器都装一个**的第三方软件 |

既然温度不需要展示，就没必要为它背上"每台机器传感器各不相同"的复杂度。
曾经实现过的可插拔探针层（`sensors.py`）已按此决定删除。

> 若将来确实需要 GPU 利用率之类的数据，那是**利用率**不是温度，应作为单独的
> 议题重新评估，而不是把探针框架整个搬回来。

## 多机器部署注意

采集器要在**多台电脑**上复用，这几条是硬要求：

- **节点 ID 必须显式配置**，不要依赖主机名。实测有台机器主机名是 `某台机器`——
  节点 ID 会进 MQTT 主题，必须可预测。`sanitize_node_id()` 会兜底成哈希，
  但**可读性差**，正解是在 `config.toml` 里写 `node = "win-dev"`
- **控制台编码强制 UTF-8**：Windows 默认 GBK，非 ASCII 输出会直接崩
- **平台差异只写在 `sysinfo.py`**，其余模块不出现 `sys.platform` / `os.name`
- 行尾与编码由仓库根 `.gitattributes` 强制（LF + UTF-8）——踩过坑，别绕过
- 兼容性下限是 **Python 3.12**（Windows 开发机版本，中控机 3.14 更新）
