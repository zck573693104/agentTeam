from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any

from agentteam.storage.utils import utcnow_iso as _now


class AuditRepo:
    """run_events 与 approvals 表的读写，对标 AgentLoop 执行轨迹。

    当与 SqliteSaver 等组件共享同一 sqlite3.Connection 时，须传入同一个
    lock 以串行化所有连接访问（sqlite3.Connection 在多线程下非线程安全，
    即使 check_same_thread=False）。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def add_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> int:
        with self._lock:
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

    def list_events(
        self, run_id: str, limit: int | None = None, offset: int = 0
    ) -> list[sqlite3.Row]:
        """按 id 升序返回 run 的事件,支持分页。

        limit=None 不分页(向后兼容,但长 run 建议传 limit 避免全量加载);
        limit=N 只返回前 N 条;offset 跳过前 offset 条(配合 limit 翻页)。
        """
        with self._lock:
            if limit is None:
                cur = self._conn.execute(
                    "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC",
                    (run_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
                    (run_id, limit, offset),
                )
            return cur.fetchall()

    def list_events_after(self, run_id: str, after_id: int) -> list[sqlite3.Row]:
        """游标增量读取:返回 id > after_id 的事件,按 id ASC 排序。

        用于 SSE 重连只补发新增事件,避免每次重连全表扫描。
        走 (run_id, id) 联合索引 idx_run_events_run_id_id。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (run_id, after_id),
            )
            return cur.fetchall()

    def latest_event_id(self, run_id: str) -> int:
        """返回 run 当前最大 event id(无事件返回 0)。

        用于 SSE 初始订阅时获取 last_id 游标,跳过历史已发事件。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(id) AS m FROM run_events WHERE run_id = ?", (run_id,)
            )
            row = cur.fetchone()
            return (row["m"] if row and row["m"] is not None else 0)

    def add_approval(self, run_id: str) -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO approvals (id, run_id, status, requested_at) VALUES (?, ?, 'pending', ?)",
                (approval_id, run_id, _now()),
            )
            self._conn.commit()
        return approval_id

    def get_approval(self, approval_id: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            return cur.fetchone()

    def decide_approval(
        self, approval_id: str, decision: str, decider: str, reason: str | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decider = ?, reason = ? WHERE id = ?",
                (decision, _now(), decider, reason, approval_id),
            )
            self._conn.commit()

    def list_pending_approvals(self, run_id: str) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
            )
            return cur.fetchall()

    def list_approvals(
        self, run_id: str, limit: int | None = None, offset: int = 0
    ) -> list[sqlite3.Row]:
        """列出某 run 的所有审批记录（含已决策），按请求时间排序,支持分页。"""
        with self._lock:
            if limit is None:
                cur = self._conn.execute(
                    "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at",
                    (run_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at LIMIT ? OFFSET ?",
                    (run_id, limit, offset),
                )
            return cur.fetchall()
