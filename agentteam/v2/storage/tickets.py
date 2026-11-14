"""Ticket + Session 仓库(状态机 CAS 变更,对齐 v1 try_claim 模式)。"""
from __future__ import annotations

import json
import threading

from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.domain.events import SessionStatus, TicketStatus
from agentteam.v2.domain.mandate import Mandate
from agentteam.v2.domain.ticket import Ticket
from agentteam.v2.storage.base import BaseSqliteRepo


class TicketRepo(BaseSqliteRepo):
    def _row_to_ticket(self, row) -> Ticket:
        return Ticket(
            id=row["id"],
            title=row["title"],
            task=row["task"],
            status=TicketStatus(row["status"]),
            worker_name=row["worker_name"],
            thread_id=row["thread_id"],
            mandate=Mandate.from_dict(row["mandate_json"]),
            result=row["result"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ended_at=row["ended_at"],
        )

    def create(self, t: Ticket) -> None:
        now = _now()
        if t.created_at is None:
            t.created_at = now
        self._execute(
            "INSERT INTO tickets (id, title, task, status, worker_name, thread_id, "
            "mandate_json, result, created_at, updated_at, ended_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.id, t.title, t.task, t.status.value, t.worker_name, t.thread_id,
                json.dumps(t.mandate.to_dict(), ensure_ascii=False) if t.mandate else None,
                t.result, t.created_at, now, t.ended_at,
            ),
        )

    def get(self, ticket_id: str) -> Ticket | None:
        row = self._fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return self._row_to_ticket(row) if row else None

    def list_by_status(self, status: TicketStatus | None = None, limit: int = 200) -> list[Ticket]:
        if status is None:
            rows = self._fetchall(
                "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            )
        return [self._row_to_ticket(r) for r in rows]

    def list_pending_ids(self) -> list[str]:
        rows = self._fetchall(
            "SELECT id FROM tickets WHERE status = 'pending' ORDER BY created_at ASC"
        )
        return [r["id"] for r in rows]

    def cas_status(
        self, ticket_id: str, expected: TicketStatus, new: TicketStatus,
        *, worker_name: str | None = None, result: str | None = None,
    ) -> bool:
        """条件状态变更:仅当当前 status == expected 时更新(防并发双派工)。

        终态写入 ended_at;非终态清空。
        返回是否成功。
        """
        now = _now()
        ended_at = now if new.is_terminal() else None
        sets = ["status = ?", "updated_at = ?", "ended_at = COALESCE(?, ended_at)"]
        params: list = [new.value, now, ended_at]
        if worker_name is not None:
            sets.append("worker_name = ?")
            params.append(worker_name)
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        params.append(ticket_id)
        cur = self._execute(
            f"UPDATE tickets SET {', '.join(sets)} WHERE id = ? AND status = ?",
            (*params, expected.value),
        )
        return cur.rowcount > 0


class SessionRepo(BaseSqliteRepo):
    def create(
        self, session_id: str, ticket_id: str, worker_name: str,
        workspace_dir: str, max_iterations: int, model_json: dict | None,
    ) -> None:
        now = _now()
        self._execute(
            "INSERT INTO sessions (id, ticket_id, worker_name, workspace_dir, status, "
            "iterations, max_iterations, model_json, created_at, updated_at) "
            "VALUES (?,?,?,?, 'running', 0, ?, ?, ?, ?)",
            (
                session_id, ticket_id, worker_name, workspace_dir, max_iterations,
                json.dumps(model_json, ensure_ascii=False) if model_json else None,
                now, now,
            ),
        )

    def get(self, session_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def list_for_ticket(self, ticket_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM sessions WHERE ticket_id = ? ORDER BY created_at ASC",
            (ticket_id,),
        )
        return [dict(r) for r in rows]

    def list_running(self) -> list[dict]:
        """全部 running 会话(崩溃恢复:孤儿会话判定)。"""
        rows = self._fetchall("SELECT * FROM sessions WHERE status = 'running'")
        return [dict(r) for r in rows]

    def cas_status(
        self, session_id: str, expected: SessionStatus, new: SessionStatus,
    ) -> bool:
        cur = self._execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new.value, _now(), session_id, expected.value),
        )
        return cur.rowcount > 0

    def set_iterations(self, session_id: str, iterations: int) -> None:
        self._execute(
            "UPDATE sessions SET iterations = ?, updated_at = ? WHERE id = ?",
            (iterations, _now(), session_id),
        )


class MessageRepo(BaseSqliteRepo):
    """会话 transcript(v2 的 checkpoint:每轮落库,恢复时重载)。"""

    def append(
        self, session_id: str, role: str, content: str = "",
        tool_calls: list[dict] | None = None, tool_call_id: str | None = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO messages (session_id, role, content, tool_calls_json, tool_call_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                session_id, role, content,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                tool_call_id, _now(),
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def list_for_session(self, session_id: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        )
        out = []
        for r in rows:
            d = dict(r)
            d["tool_calls"] = json.loads(d.pop("tool_calls_json")) if d["tool_calls_json"] else None
            out.append(d)
        return out
