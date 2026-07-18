"""团队注册表:可选 DB-backed 持久化。

- repo=None:纯内存模式(测试用,向后兼容)
- repo 提供:所有操作同步到 DB,同时维护内存缓存加速读取
"""
from __future__ import annotations

from agentteam.domain.team import Team


class TeamStore:
    """团队注册表。

    默认纯内存(重启后清空);传入 TeamRepo 后变为 DB-backed,
    初始化时从 DB 加载到内存缓存,所有写操作同步到 DB。
    """

    def __init__(self, repo=None) -> None:
        self._repo = repo
        self._cache: dict[str, Team] = {}
        if repo is not None:
            for t in repo.list_all():
                self._cache[t.name] = t

    def register(self, team: Team) -> None:
        self._cache[team.name] = team
        if self._repo is not None:
            self._repo.upsert(team)

    def get(self, name: str) -> Team | None:
        return self._cache.get(name)

    def list_all(self) -> list[Team]:
        return list(self._cache.values())

    def delete(self, name: str) -> bool:
        if name not in self._cache:
            return False
        del self._cache[name]
        if self._repo is not None:
            self._repo.delete(name)
        return True
