"""统一事件流仓库(SSE 回放源)+ 轻量 EventBus(订阅先于回放 + 游标去重)。

v1 验证过的设计:SQLite 落库为真相源,内存 bus 只做实时扇出;
客户端 SSE 断线重连按 id 游标补发。
"""
from __future__ import annotations

import json
import queue
import threading

from agentteam.config import get_settings
from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.storage.base import BaseSqliteRepo


class EventRepo(BaseSqliteRepo):
    def insert(
        self, ticket_id: str, event_type: str, actor: str = "system",
        session_id: str | None = None, payload: dict | None = None,
    ) -> dict:
        """落库并返回完整 envelope(调用方同时 publish 到 EventBus)。"""
        now = _now()
        cur = self._execute(
            "INSERT INTO events (ticket_id, session_id, type, actor, payload_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (ticket_id, session_id, event_type, actor,
             json.dumps(payload or {}, ensure_ascii=False), now),
        )
        return {
            "id": cur.lastrowid,
            "ticket_id": ticket_id,
            "session_id": session_id,
            "type": event_type,
            "actor": actor,
            "payload": payload or {},
            "created_at": now,
        }

    def list_after(self, ticket_id: str, after_id: int = 0, limit: int = 500) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM events WHERE ticket_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (ticket_id, after_id, limit),
        )
        out = []
        for r in rows:
            out.append({
                "id": r["id"],
                "ticket_id": r["ticket_id"],
                "session_id": r["session_id"],
                "type": r["type"],
                "actor": r["actor"],
                "payload": json.loads(r["payload_json"] or "{}"),
                "created_at": r["created_at"],
            })
        return out

    def latest_event_id(self, ticket_id: str) -> int:
        row = self._fetchone(
            "SELECT MAX(id) AS m FROM events WHERE ticket_id = ?", (ticket_id,)
        )
        return row["m"] if row and row["m"] is not None else 0

    def recent_count(self, hours: float = 1.0) -> int:
        """B8 dashboard: 近 N 小时事件量(事件速率)。"""
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE created_at >= "
            "datetime('now', ?)",
            (f"-{hours} hours",),
        )
        return row["n"]

    def total_tokens(self) -> int:
        """A1/B8: 全部会话 token 总量。"""
        row = self._fetchone(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS t FROM sessions"
        )
        return int(row["t"] or 0)


class EventBus:
    """按 ticket 分主题的实时扇出(有界队列,满丢最旧并计数)。"""

    def __init__(self, queue_size: int | None = None) -> None:
        self._size = queue_size or get_settings().event_queue_size
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()
        self._dropped = 0

    def subscribe(self, topic: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._size)
        with self._lock:
            self._subs.setdefault(topic, []).append(q)
        return q

    def unsubscribe(self, topic: str, q: queue.Queue) -> None:
        with self._lock:
            lst = self._subs.get(topic, [])
            if q in lst:
                lst.remove(q)

    def publish(self, topic: str, event: dict) -> None:
        with self._lock:
            queues = list(self._subs.get(topic, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()  # 丢最旧
                    q.put_nowait(event)
                except queue.Empty:
                    pass
                self._dropped += 1

    @property
    def dropped_count(self) -> int:
        return self._dropped
