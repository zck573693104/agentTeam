"""EvolutionRepo:agent 进化历史的 SQLite 持久化。"""
from __future__ import annotations

import sqlite3
import threading


class EvolutionRepo:
    """evolution_history 表的 CRUD 仓库。

    表 schema(由 init_db 创建):
        id, agent_name, version, dimension, before_value, after_value,
        diff, reason, run_id, success, error, timestamp

    线程安全:与 RunRepo/AuditRepo 共享同一 sqlite3.Connection,
    必须传入同一把 threading.Lock 串行化所有访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def add_record(
        self,
        agent_name: str,
        version: int,
        dimension: str,
        before_value: str,
        after_value: str,
        diff: str,
        reason: str,
        run_id: str | None,
        success: bool,
        error: str | None = None,
    ) -> int:
        """插入一条 history 记录,返回新 id。

        dimension: 'prompt' | 'params' | 'skill_gen' | 'skill_select' | 'rollback'
        success=False 时 error 字段记录失败原因。
        run_id=None 表示用户触发的 rollback(不关联 run)。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO evolution_history
                    (agent_name, version, dimension, before_value, after_value,
                     diff, reason, run_id, success, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (agent_name, version, dimension, before_value, after_value,
                 diff, reason, run_id, 1 if success else 0, error),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_history(self, agent_name: str, limit: int = 20) -> list[dict]:
        """按 timestamp 倒序返回该 agent 的 history(最多 limit 条)。"""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (agent_name, limit),
            )
            return [dict(row) for row in cur.fetchall()]
