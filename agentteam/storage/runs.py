from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepo:
    """runs 表的读写。

    当与 SqliteSaver 等组件共享同一 sqlite3.Connection 时，须传入同一个
    lock 以串行化所有连接访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def create_run(self, team_name: str, task: str) -> str:
        run_id = uuid.uuid4().hex
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, team_name, task, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (run_id, team_name, task, now, now),
            )
            self._conn.commit()
        return run_id

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
            return cur.fetchone()

    def update_status(self, run_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), run_id),
            )
            self._conn.commit()

    def end_run(self, run_id: str, status: str, total_tokens: int = 0) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, updated_at = ?, total_tokens = ? "
                "WHERE id = ?",
                (status, now, now, total_tokens, run_id),
            )
            self._conn.commit()

    def list_runs(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC")
            return cur.fetchall()
