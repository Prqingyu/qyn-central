"""状态层 —— 最后已知值 + 陈旧度。

★ 核心设计（STATUS_WALL.md §5.4）：**离线是常态，不是故障。**

节点按需开机（Windows 机、存储工作站），不在线时页面**不该显示空白**，
而应显示「最后已知值 + 这是多久以前的数据」。所以这里存的是 upsert 语义的
最后快照，不存时序 —— 要看趋势是另一件事，需要时再上时序表。

纯 stdlib（sqlite3），无第三方依赖。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_state (
    node        TEXT PRIMARY KEY,
    ts          TEXT,            -- 节点自报时间（ISO8601，可能为空）
    payload     TEXT NOT NULL,   -- 原始样本 JSON，原样保存（不裁剪）
    received_at REAL NOT NULL    -- 服务端收到的时间（陈旧度以此为准）
);
"""


class StateStore:
    """节点的最后已知状态。`path=":memory:"` 用于测试。"""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # check_same_thread=False：HTTP 服务是多线程的，写点在 ingest 线程
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- 写 ----

    def upsert(self, sample: dict, received_at: float | None = None) -> dict:
        # 只接受 JSON 对象：非 dict 进来会让下面的 .get 直接抛，且可能存进坏行
        if not isinstance(sample, dict):
            raise ValueError("样本必须是 JSON 对象")
        node = str(sample.get("node") or "").strip()
        if not node:
            # 节点 ID 必须显式给出：实测有台机器主机名是 emoji，靠主机名猜会污染主题
            raise ValueError("样本缺 node 字段（节点 ID 必须显式给出）")
        now = time.time() if received_at is None else received_at
        self._conn.execute(
            "INSERT INTO node_state (node, ts, payload, received_at) VALUES (?,?,?,?) "
            "ON CONFLICT(node) DO UPDATE SET "
            "ts=excluded.ts, payload=excluded.payload, received_at=excluded.received_at",
            (node, sample.get("ts"), json.dumps(sample, ensure_ascii=False), now),
        )
        self._conn.commit()
        return {"node": node, "received_at": now}

    def remove(self, node: str) -> None:
        self._conn.execute("DELETE FROM node_state WHERE node = ?", (node,))
        self._conn.commit()

    def prune(self, max_age_s: float = 30 * 86400) -> list[str]:
        """丢掉**很久没动静**的节点（默认 30 天），返回被丢掉的节点名。

        用户 2026-10-17："清理干净测试节点" —— 一次自测/演示推上来的节点（例如
        `svc-egress-selftest`）原先会**永久**留在墙上 ✗（实测留了近 40 小时）。
        这里给一条保留期；真节点/离线机不受影响（它们一直在推，`received_at` 一直被刷新）。
        真的停推 30 天以上，卡片也只是退化成"未上报"，不会丢历史配置。
        """
        cutoff = time.time() - max_age_s
        names = [r["node"] for r in self._conn.execute(
            "SELECT node FROM node_state WHERE received_at < ?", (cutoff,))]
        for n in names:
            self._conn.execute("DELETE FROM node_state WHERE node = ?", (n,))
        if names:
            self._conn.commit()
        return names

    # ---- 读 ----

    def all(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = self._conn.execute(
            "SELECT node, payload, received_at FROM node_state ORDER BY node"
        ).fetchall()
        return [self._decorate(r, now) for r in rows]

    def get(self, node: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        row = self._conn.execute(
            "SELECT node, payload, received_at FROM node_state WHERE node = ?", (node,)
        ).fetchone()
        return self._decorate(row, now) if row else None

    @staticmethod
    def _decorate(row: sqlite3.Row, now: float) -> dict:
        """把一行数据库记录变成能发出去的 dict。

        ★★ **一条坏行绝不允许把服务打崩**（2026-10-15 实测的事故）：
        库里曾有一条 `payload` 为 NULL 的行（旧 schema 没有 NOT NULL），于是
        `json.loads(None)` 抛 TypeError ⇒ 读这个节点就崩 ⇒ **SSE 广播中断** ⇒
        采集端收到 "Remote end closed connection" 上报失败 ✗。
        现在坏行退化成"这个节点读不出来"，其余节点照常，服务不倒。
        """
        raw = row["payload"]
        if not isinstance(raw, (str, bytes, bytearray)):
            # 旧库遗留 / 被外部改过：如实报"这条坏了"，不假装它有数据
            return {
                "node": row["node"],
                "corrupt": True,
                "error": "库里这条样本的 payload 是空的（旧 schema 遗留）",
                "received_at": row["received_at"],
                "stale_seconds": round(max(now - row["received_at"], 0.0), 1),
            }
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return {
                "node": row["node"],
                "corrupt": True,
                "error": "这条样本的 payload 不是合法 JSON",
                "received_at": row["received_at"],
                "stale_seconds": round(max(now - row["received_at"], 0.0), 1),
            }
        if not isinstance(payload, dict):
            # 例如库里存的是字面量 null / 数组 —— 后面还要往里塞 received_at，必须是 dict
            return {
                "node": row["node"],
                "corrupt": True,
                "error": "这条样本的 payload 不是对象",
                "received_at": row["received_at"],
                "stale_seconds": round(max(now - row["received_at"], 0.0), 1),
            }
        payload["received_at"] = row["received_at"]
        # 陈旧度由**服务端**算：节点自报的 ts 可能是错的/被篡改的
        payload["stale_seconds"] = round(max(now - row["received_at"], 0.0), 1)
        return payload

    def close(self) -> None:
        self._conn.close()
