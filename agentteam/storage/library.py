"""library_agents 表的读写:AgentLibrary 配置持久化。"""
from __future__ import annotations

import json

from agentteam.domain.serializer import _agent_from_dict, _agent_to_dict
from agentteam.domain.agent import Agent
from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class LibraryRepo(BaseSqliteRepo):
    """library_agents 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo / TeamRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。
    """

    def upsert(self, agent: Agent) -> None:
        """INSERT OR REPLACE,序列化为 JSON。"""
        config = json.dumps(_agent_to_dict(agent), ensure_ascii=False)
        now = _now()
        self._execute(
            "INSERT INTO library_agents (name, config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "config=excluded.config, updated_at=excluded.updated_at",
            (agent.name, config, now, now),
        )

    def get(self, name: str) -> Agent | None:
        """SELECT config,反序列化为 Agent。"""
        row = self._fetchone("SELECT config FROM library_agents WHERE name = ?", (name,))
        if row is None:
            return None
        return _agent_from_dict(json.loads(row["config"]))

    def list_all(self) -> list[Agent]:
        """SELECT all,反序列化为 Agent 列表。"""
        rows = self._fetchall("SELECT config FROM library_agents ORDER BY name")
        return [_agent_from_dict(json.loads(r["config"])) for r in rows]

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        cur = self._execute("DELETE FROM library_agents WHERE name = ?", (name,))
        return cur.rowcount > 0
