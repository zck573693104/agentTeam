from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepo:
    """runs 表的读写。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_run(self, team_name: str, task: str) -> str:
        run_id = uuid.uuid4().hex
        now = _now()
        self._conn.execute(
            "INSERT INTO runs (id, team_name, task, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (run_id, team_name, task, now, now),
        )
        self._conn.commit()
        return run_id

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        return cur.fetchone()

    def update_status(self, run_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), run_id),
        )
        self._conn.commit()

    def end_run(self, run_id: str, status: str, total_tokens: int = 0) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE runs SET status = ?, ended_at = ?, updated_at = ?, total_tokens = ? "
            "WHERE id = ?",
            (status, now, now, total_tokens, run_id),
        )
        self._conn.commit()

    def list_runs(self) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC")
        return cur.fetchall()
