"""输出去向（sink）。

采集与上报分离：metrics.py 只管产出样本，本模块只管把样本送出去。
换上报方式 = 加一个 Sink 子类，采集代码一行不动。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


class Sink:
    name = "sink"

    def start(self, node_id: str) -> None:
        raise NotImplementedError

    def emit(self, sample: dict) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass


class StdoutSink(Sink):
    """打到标准输出 —— 没有 broker 时验证采集逻辑用。"""

    name = "stdout"

    def start(self, node_id: str) -> None:
        pass

    def emit(self, sample: dict) -> None:
        # flush 必须显式加：stdout 接管道时是块缓冲的，
        # 进程被信号杀掉时缓冲区会整个丢失（实测踩过，systemd 下就是这么丢日志的）
        print(json.dumps(sample, ensure_ascii=False), flush=True)


class MqttSink(Sink):
    """发布到 MQTT。

    两个主题（见 STATUS_WALL.md §5.1）：
        <prefix>/node/<node>/telemetry   retained   指标快照
        <prefix>/node/<node>/status      retained   在线状态（含 LWT 遗嘱）

    ★ LWT 是关键：连接时向 broker 登记"我断线了就把我标 offline"，
      离线检测由 broker 代劳，我们不用写轮询或超时判断。
    """

    name = "mqtt"

    def __init__(self, broker: str, port: int = 1883, prefix: str = "qyn"):
        self.broker = broker
        self.port = port
        self.prefix = prefix
        self._client = None
        self._node = None

    def start(self, node_id: str) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise SystemExit(
                "缺少 paho-mqtt。安装：python -m pip install paho-mqtt"
            )

        self._node = node_id
        status_topic = f"{self.prefix}/node/{node_id}/status"

        try:  # paho-mqtt 2.x 要求显式指定回调 API 版本
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:  # 1.x
            client = mqtt.Client()

        # 遗嘱：异常断线时由 broker 代发 offline
        client.will_set(status_topic,
                        payload=json.dumps({"online": False}), qos=1, retain=True)
        client.connect(self.broker, self.port, keepalive=30)
        client.loop_start()
        client.publish(status_topic,
                       json.dumps({"online": True}), qos=1, retain=True)
        self._client = client
        print(f"[mqtt] 已连接 {self.broker}:{self.port}", file=sys.stderr)

    def emit(self, sample: dict) -> None:
        topic = f"{self.prefix}/node/{sample['node']}/telemetry"
        self._client.publish(topic, json.dumps(sample, ensure_ascii=False),
                             qos=0, retain=True)

    def stop(self) -> None:
        if not self._client:
            return
        # 正常退出：显式标 offline，别让 broker 空等遗嘱超时
        self._client.publish(f"{self.prefix}/node/{self._node}/status",
                             json.dumps({"online": False}), qos=1, retain=True)
        time.sleep(0.2)
        self._client.loop_stop()
        self._client.disconnect()


class HttpSink(Sink):
    """POST 到状态墙的 ingest 端点（纯 stdlib，零新依赖）。

    与 MQTT 的分工：MQTT 是主通道（retained + LWT，离线检测白送）；
    HTTP 是**给装不了 MQTT 库的节点**的兜底（MC 服务器、未来的 STM32）——
    服务端收到后落到同一个 StateStore，展示层只认一种数据形态。

    ★ 上报失败**不能杀掉采集循环**：网络抖一下就让整个采集器退出是最糟的行为。
      这里只记一行 stderr，下一轮继续。失败次数可在页面上看到。
    """

    name = "http"

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = float(timeout)
        self.failures = 0

    def start(self, node_id: str) -> None:
        print(f"[http] 上报目标 {self.url}", file=sys.stderr)

    def emit(self, sample: dict) -> None:
        data = json.dumps(sample, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()
        except Exception as exc:  # noqa: BLE001 —— 上报失败不致命，下一轮重试
            self.failures += 1
            print(f"[http] 上报失败（第 {self.failures} 次）: {exc}", file=sys.stderr)


SINKS = {c.name: c for c in (StdoutSink, MqttSink, HttpSink)}
