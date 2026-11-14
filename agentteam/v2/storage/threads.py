"""Thread/Trigger 仓库(dsh: Thread = Trigger 的时间序,Eval 的上下文单元)。"""
from __future__ import annotations

import json
from uuid import uuid4

from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.storage.base import BaseSqliteRepo


class ThreadRepo(BaseSqliteRepo):
    def get_or_create(self, source: str, thread_key: str, title: str | None = None) -> dict:
        """按 (source, thread_key) 幂等聚合线程(dsh: 线程是入站上下文单元)。"""
        row = self._fetchone(
            "SELECT * FROM threads WHERE source = ? AND thread_key = ?",
            (source, thread_key),
        )
        if row:
            return dict(row)
        self._execute(
            "INSERT INTO threads (id, source, thread_key, title, created_at) VALUES (?,?,?,?,?)",
            (uuid4().hex, source, thread_key, title, _now()),
        )
        row = self._fetchone(
            "SELECT * FROM threads WHERE source = ? AND thread_key = ?",
            (source, thread_key),
        )
        return dict(row)

    def get(self, thread_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM threads WHERE id = ?", (thread_id,))
        return dict(row) if row else None

    def list_threads(self, limit: int = 100) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM threads ORDER BY created_at DESC LIMIT ?", (min(limit, 500),)
        )
        return [dict(r) for r in rows]

    def append_trigger(
        self, thread_id: str, actor: str, text: str, raw: dict | None = None,
    ) -> dict:
        """入站事件 → Trigger(不可变,含 actor)。"""
        trigger_id = uuid4().hex
        now = _now()
        self._execute(
            "INSERT INTO triggers (id, thread_id, actor, text, raw_json, at) VALUES (?,?,?,?,?,?)",
            (trigger_id, thread_id, actor, text,
             json.dumps(raw or {}, ensure_ascii=False), now),
        )
        return {"id": trigger_id, "thread_id": thread_id, "actor": actor,
                "text": text, "at": now}

    def list_triggers(self, thread_id: str, limit: int = 200) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM triggers WHERE thread_id = ? ORDER BY at ASC LIMIT ?",
            (thread_id, min(limit, 500)),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["raw"] = json.loads(d.pop("raw_json") or "{}")
            out.append(d)
        return out


class SubscriptionRepo(BaseSqliteRepo):
    """Ticket↔Thread 多对多(dsh: 同线程追问 fan-out 直达认领 worker)。"""

    def bind(self, ticket_id: str, thread_id: str) -> bool:
        try:
            self._execute(
                "INSERT OR IGNORE INTO subscriptions (ticket_id, thread_id, created_at) "
                "VALUES (?,?,?)",
                (ticket_id, thread_id, _now()),
            )
            return True
        except Exception:
            return False

    def threads_for_ticket(self, ticket_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT t.* FROM threads t JOIN subscriptions s ON s.thread_id = t.id "
            "WHERE s.ticket_id = ?",
            (ticket_id,),
        )
        return [dict(r) for r in rows]

    def tickets_for_thread(self, thread_id: str) -> list[str]:
        rows = self._fetchall(
            "SELECT ticket_id FROM subscriptions WHERE thread_id = ?", (thread_id,)
        )
        return [r["ticket_id"] for r in rows]
