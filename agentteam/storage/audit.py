from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepo:
    """run_events 与 approvals 表的读写，对标 AgentLoop 执行轨迹。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO run_events (run_id, event_type, actor, timestamp, payload, duration_ms, tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event_type,
                actor,
                _now(),
                json.dumps(payload or {}, ensure_ascii=False),
                duration_ms,
                tokens,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_events(self, run_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC", (run_id,)
        )
        return cur.fetchall()

    def add_approval(self, run_id: str) -> str:
        approval_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO approvals (id, run_id, status, requested_at) VALUES (?, ?, 'pending', ?)",
            (approval_id, run_id, _now()),
        )
        self._conn.commit()
        return approval_id

    def get_approval(self, approval_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return cur.fetchone()

    def decide_approval(
        self, approval_id: str, decision: str, decider: str, reason: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decider = ?, reason = ? WHERE id = ?",
            (decision, _now(), decider, reason, approval_id),
        )
        self._conn.commit()

    def list_pending_approvals(self, run_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
        )
        return cur.fetchall()
