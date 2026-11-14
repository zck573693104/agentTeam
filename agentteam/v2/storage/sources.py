"""Source / Evaluation 仓库(P3: 入站源配置 + gate/Eval 决定,观察模式账本)。"""
from __future__ import annotations

import json
from uuid import uuid4

from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.storage.base import BaseSqliteRepo


class SourceRepo(BaseSqliteRepo):
    def create(self, name: str, kind: str, config: dict, secret_hash: str | None = None) -> dict:
        self._execute(
            "INSERT INTO sources (name, kind, config_json, secret_hash, enabled, created_at) "
            "VALUES (?,?,?,?,1,?)",
            (name, kind, json.dumps(config, ensure_ascii=False), secret_hash, _now()),
        )
        return self.get(name)  # type: ignore[return-value]

    def get(self, name: str) -> dict | None:
        row = self._fetchone("SELECT * FROM sources WHERE name = ?", (name,))
        return self._row(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM sources ORDER BY name")
        return [self._row(r) for r in rows]

    def update(self, name: str, config: dict, secret_hash: str | None = None,
               enabled: bool | None = None) -> dict | None:
        if self.get(name) is None:
            return None
        self._execute(
            "UPDATE sources SET config_json = ?, "
            "secret_hash = COALESCE(?, secret_hash), "
            "enabled = COALESCE(?, enabled) WHERE name = ?",
            (json.dumps(config, ensure_ascii=False), secret_hash,
             None if enabled is None else (1 if enabled else 0), name),
        )
        return self.get(name)

    def delete(self, name: str) -> bool:
        return self._execute("DELETE FROM sources WHERE name = ?", (name,)).rowcount > 0

    def get_secret_hash(self, name: str) -> str | None:
        """取秘钥哈希(webhook 校验专用;list/get 不外泄)。"""
        row = self._fetchone("SELECT secret_hash FROM sources WHERE name = ?", (name,))
        return row["secret_hash"] if row else None

    def _row(self, r) -> dict:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json") or "{}")
        d.pop("secret_hash", None)  # 不外泄哈希
        d["enabled"] = bool(d["enabled"])
        return d


class EvaluationRepo(BaseSqliteRepo):
    """gate/Eval 决定账本;observation = would_accept(观察模式,先记录不执行)。"""

    def create(
        self, trigger_id: str, gate: dict, decision: str,
        reason: str = "", ticket_id: str | None = None,
    ) -> dict:
        eval_id = uuid4().hex
        self._execute(
            "INSERT INTO evaluations (id, trigger_id, gate_json, decision, reason, "
            "ticket_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (eval_id, trigger_id, json.dumps(gate, ensure_ascii=False),
             decision, reason[:1000], ticket_id, _now()),
        )
        return self.get(eval_id)  # type: ignore[return-value]

    def get(self, eval_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM evaluations WHERE id = ?", (eval_id,))
        return self._row(row) if row else None

    def set_ticket(self, eval_id: str, ticket_id: str, decision: str | None = None) -> None:
        if decision:
            self._execute(
                "UPDATE evaluations SET ticket_id = ?, decision = ? WHERE id = ?",
                (ticket_id, decision, eval_id),
            )
        else:
            self._execute(
                "UPDATE evaluations SET ticket_id = ? WHERE id = ?", (ticket_id, eval_id),
            )

    def list_by_decision(self, decision: str, limit: int = 100) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM evaluations WHERE decision = ? ORDER BY created_at DESC LIMIT ?",
            (decision, min(limit, 500)),
        )
        return [self._row(r) for r in rows]

    def _row(self, r) -> dict:
        d = dict(r)
        d["gate"] = json.loads(d.pop("gate_json") or "{}")
        return d
