from __future__ import annotations

import sqlite3
import threading
import uuid

from agentteam.storage.utils import utcnow_iso as _now


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

    def end_run_if_status(
        self, run_id: str, expected_status: str, status: str, total_tokens: int = 0
    ) -> bool:
        """条件 end_run:仅当当前 status == expected_status 时才 end_run。

        用于 worker 自然完成时避免覆盖 cancel_run 设置的 cancelling 状态:
        _handle_invoke_result 调用 end_run_if_status(run_id, "running", "completed"),
        若 status 已被 cancel 改为 cancelling,则返回 False,不覆盖,
        让 _finalize_cancellation 推进到 cancelled。

        与 try_claim 的区别:try_claim 只更新 status(不设 ended_at/total_tokens),
        适合中间态转换(running→interrupted);本方法设 ended_at + total_tokens,
        适合终态写入(running→completed/cancelled/failed)。
        """
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, updated_at = ?, total_tokens = ? "
                "WHERE id = ? AND status = ?",
                (status, now, now, total_tokens, run_id, expected_status),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_runs(self, limit: int | None = None, offset: int = 0) -> list[sqlite3.Row]:
        """按创建时间倒序返回 runs,支持分页。

        limit=None 不分页(向后兼容);limit=N 只返回前 N 条;
        offset 跳过前 offset 条(常与 limit 配合做翻页)。
        """
        with self._lock:
            if limit is None:
                cur = self._conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC"
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            return cur.fetchall()

    def count_runs(self) -> int:
        """返回 runs 表总行数(用于分页元数据)。"""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM runs")
            row = cur.fetchone()
            return row[0] if row else 0

    def aggregate_by_status(self) -> dict[str, int]:
        """SELECT status, COUNT(*) GROUP BY status — 用于 dashboard。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
            )
            return {row["status"]: row["n"] for row in cur.fetchall()}

    def aggregate_by_team(self) -> dict[str, int]:
        """SELECT team_name, COUNT(*) GROUP BY team_name — 用于 dashboard。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT team_name, COUNT(*) AS n FROM runs GROUP BY team_name"
            )
            return {row["team_name"]: row["n"] for row in cur.fetchall()}

    def sum_total_tokens(self) -> int:
        """SELECT SUM(total_tokens) — 用于 dashboard。"""
        with self._lock:
            cur = self._conn.execute("SELECT COALESCE(SUM(total_tokens), 0) AS s FROM runs")
            row = cur.fetchone()
            return row["s"] if row else 0

    def try_claim(
        self, run_id: str, expected_status: str, new_status: str
    ) -> bool:
        """原子地条件更新 run 状态。

        若当前 status == expected_status，则更新为 new_status 并返回 True；
        否返回 False。用 SQL 的 WHERE 条件保证检查与更新的原子性，
        避免并发 approve 请求的双竞态（check-then-act 非原子问题）。
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (new_status, _now(), run_id, expected_status),
            )
            self._conn.commit()
            return cur.rowcount > 0
