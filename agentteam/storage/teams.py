"""teams 表的读写:Team 配置持久化。"""
from __future__ import annotations

import json

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.domain.team import Team
from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class TeamRepo(BaseSqliteRepo):
    """teams 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。
    """

    def upsert(self, team: Team) -> None:
        """INSERT OR REPLACE,序列化为 JSON。"""
        team_dict = team_to_dict(team)
        config = json.dumps(team_dict, ensure_ascii=False)
        now = _now()
        self._execute(
            "INSERT INTO teams (name, description, config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "description=excluded.description, config=excluded.config, updated_at=excluded.updated_at",
            (team.name, team.description, config, now, now),
        )

    def get(self, name: str) -> Team | None:
        """SELECT config,反序列化为 Team。"""
        row = self._fetchone("SELECT config FROM teams WHERE name = ?", (name,))
        if row is None:
            return None
        team_dict = json.loads(row["config"])
        return team_from_dict(team_dict)

    def list_all(self) -> list[Team]:
        """SELECT all,反序列化为 Team 列表。"""
        rows = self._fetchall("SELECT config FROM teams ORDER BY name")
        result: list[Team] = []
        for r in rows:
            team_dict = json.loads(r["config"])
            result.append(team_from_dict(team_dict))
        return result

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        cur = self._execute("DELETE FROM teams WHERE name = ?", (name,))
        return cur.rowcount > 0
