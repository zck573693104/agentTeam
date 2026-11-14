"""Outbox 仓库(移植自 v1 storage/notify.py;run_id → ticket_id)。"""
from __future__ import annotations

from agentteam.storage.utils import utcnow_iso as _epoch
from agentteam.v2.storage.base import BaseSqliteRepo


class OutboxRepo(BaseSqliteRepo):
    def enqueue(self, ticket_id: str, event: str, url: str, payload_json: str) -> int:
        cur = self._execute(
            "INSERT INTO outbox (ticket_id, event, url, payload_json, status, "
            "next_retry_at, created_at) VALUES (?,?,?,?, 'pending', 0, ?)",
            (ticket_id, event, url, payload_json, _epoch()),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def pending_rows(self) -> list:
        """待发行,按 (ticket_id, id) 排序——同工单串行(「已收到」先于结果)。"""
        return self._fetchall(
            "SELECT * FROM outbox WHERE status = 'pending' ORDER BY ticket_id, id"
        )

    def mark_sent(self, row_id: int, sent_at: float) -> None:
        self._execute(
            "UPDATE outbox SET status = 'sent', sent_at = ? WHERE id = ?",
            (sent_at, row_id),
        )

    def mark_retry(self, row_id: int, attempts: int, next_retry_at: float,
                   last_error: str) -> None:
        self._execute(
            "UPDATE outbox SET attempts = ?, next_retry_at = ?, last_error = ? WHERE id = ?",
            (attempts, next_retry_at, last_error[:500], row_id),
        )

    def mark_dropped(self, row_id: int, attempts: int, last_error: str) -> None:
        self._execute(
            "UPDATE outbox SET status = 'dropped', attempts = ?, last_error = ? WHERE id = ?",
            (attempts, last_error[:500], row_id),
        )

    def counts(self) -> dict[str, int]:
        """B8 dashboard: outbox 各状态计数。"""
        rows = self._fetchall("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def list_for_ticket(self, ticket_id: str, limit: int = 100) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM outbox WHERE ticket_id = ? ORDER BY id ASC LIMIT ?",
            (ticket_id, min(limit, 500)),
        )
        return [dict(r) for r in rows]
