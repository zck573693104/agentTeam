"""Attention 仓库(dsh: Attention = 等待人的结构化问题,一次最多一个)。"""
from __future__ import annotations

import json
from uuid import uuid4

from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.storage.base import BaseSqliteRepo


class AttentionRepo(BaseSqliteRepo):
    def create(
        self, ticket_id: str, session_id: str, attention_type: str,
        payload: dict,
    ) -> dict:
        att_id = uuid4().hex
        now = _now()
        self._execute(
            "INSERT INTO attentions (id, ticket_id, session_id, type, payload_json, "
            "status, created_at) VALUES (?,?,?,?,?, 'pending', ?)",
            (att_id, ticket_id, session_id, attention_type,
             json.dumps(payload, ensure_ascii=False), now),
        )
        return {
            "id": att_id, "ticket_id": ticket_id, "session_id": session_id,
            "type": attention_type, "payload": payload, "status": "pending",
            "decision": None, "decided_by": None, "reason": None,
            "created_at": now, "decided_at": None,
        }

    def get(self, attention_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM attentions WHERE id = ?", (attention_id,))
        return self._row(row) if row else None

    def list_by_status(self, status: str = "pending", limit: int = 100) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM attentions WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (status, min(limit, 500)),
        )
        return [self._row(r) for r in rows]

    def has_pending_for_session(self, session_id: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM attentions WHERE session_id = ? AND status = 'pending'",
            (session_id,),
        )
        return row is not None

    def decide(
        self, attention_id: str, decision: str, decided_by: str = "human",
        reason: str | None = None,
    ) -> bool:
        """决定一个 attention(pending → decided)。返回是否成功(CAS 防重复决定)。"""
        cur = self._execute(
            "UPDATE attentions SET status = 'decided', decision = ?, decided_by = ?, "
            "reason = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (decision, decided_by, reason, _now(), attention_id),
        )
        return cur.rowcount > 0

    def _row(self, r) -> dict:
        return {
            "id": r["id"],
            "ticket_id": r["ticket_id"],
            "session_id": r["session_id"],
            "type": r["type"],
            "payload": json.loads(r["payload_json"] or "{}"),
            "status": r["status"],
            "decision": r["decision"],
            "decided_by": r["decided_by"],
            "reason": r["reason"],
            "created_at": r["created_at"],
            "decided_at": r["decided_at"],
        }
