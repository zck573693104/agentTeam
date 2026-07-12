"""内存团队注册表，按 team.name 索引。不持久化。"""
from __future__ import annotations

from agentteam.domain.team import Team


class TeamStore:
    """团队注册表。重启后清空。"""

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}

    def register(self, team: Team) -> None:
        self._teams[team.name] = team

    def get(self, name: str) -> Team | None:
        return self._teams.get(name)

    def list_all(self) -> list[Team]:
        return list(self._teams.values())

    def delete(self, name: str) -> bool:
        if name in self._teams:
            del self._teams[name]
            return True
        return False
