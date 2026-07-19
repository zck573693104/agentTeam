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

    def get_version_snapshot(self, agent_name: str, version: int) -> list[dict]:
        """取指定 version 的所有 history 记录(可能多条,因一次 trigger 触发 4 维度)。

        用于回滚:把该 version 所有 dimension 的 before_value 应用回 Agent。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ? AND version = ?
                ORDER BY id ASC
                """,
                (agent_name, version),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_recent_runs(self, agent_name: str, limit: int = 5) -> list[dict]:
        """取该 agent 最近 N 次成功的进化记录(用于 ParamTuner 统计历史指标)。

        按 timestamp 倒序,只返回 success=True 的记录。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ? AND success = 1
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (agent_name, limit),
            )
            return [dict(row) for row in cur.fetchall()]
