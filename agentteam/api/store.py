"""团队注册表:可选 DB-backed 持久化。

- repo=None:纯内存模式(测试用,向后兼容)
- repo 提供:所有操作同步到 DB,同时维护内存缓存加速读取
"""
from __future__ import annotations

import threading

from agentteam.domain.team import Team


class TeamStore:
    """团队注册表。

    默认纯内存(重启后清空);传入 TeamRepo 后变为 DB-backed,
    初始化时从 DB 加载到内存缓存,所有写操作同步到 DB。

    并发安全(BUG-01/03):
    - 所有写操作(register/register_if_absent/delete/update)持 `self._lock`,
      check-then-act 步骤原子化,避免并发 POST 同名 team 互相覆盖。
    - 写操作遵循 "DB 先、内存后" 顺序:DB 失败则抛异常、内存保持不变,
      避免"内存已改 DB 未改"的状态分裂(重启后 reload_from_db 回到旧数据)。
    """

    def __init__(self, repo=None) -> None:
        self._repo = repo
        self._cache: dict[str, Team] = {}
        self._lock = threading.Lock()
        if repo is not None:
            for t in repo.list_all():
                self._cache[t.name] = t

    def register(self, team: Team) -> None:
        """覆盖式注册(upsert)。同步 DB。

        DB 先、内存后:DB 失败则内存不变(BUG-03)。
        """
        with self._lock:
            if self._repo is not None:
                self._repo.upsert(team)
            self._cache[team.name] = team

    def register_if_absent(self, team: Team) -> bool:
        """原子注册:不存在则注册并返回 True,已存在返回 False(不覆盖)。

        check-then-set 在同一把锁内完成,并发 POST 同名 team 时恰好一个
        返回 True(BUG-01)。DB 先、内存后(BUG-03)。
        """
        with self._lock:
            if team.name in self._cache:
                return False
            if self._repo is not None:
                self._repo.upsert(team)
            self._cache[team.name] = team
            return True

    def get(self, name: str) -> Team | None:
        return self._cache.get(name)

    def list_all(self) -> list[Team]:
        return list(self._cache.values())

    def delete(self, name: str) -> bool:
        """删除 team。不存在返回 False。DB 先、内存后(BUG-03)。

        DB delete 失败时抛异常,内存缓存保留该 team,避免"内存已删 DB 还在"
        导致重启后已删除 team 复活。
        """
        with self._lock:
            if name not in self._cache:
                return False
            if self._repo is not None:
                self._repo.delete(name)
            del self._cache[name]
            return True

    def update(self, team: Team) -> bool:
        """更新已存在的 team。不存在返回 False(不创建)。同步 DB。

        DB 先、内存后(BUG-03):DB 失败则内存保留旧值。
        """
        with self._lock:
            if team.name not in self._cache:
                return False
            if self._repo is not None:
                self._repo.upsert(team)
            self._cache[team.name] = team
            return True

    def reload_from_db(self) -> int:
        """从 DB 重新加载所有 teams 到内存缓存。返回加载数量。

        无 repo 时返回 0(no-op,内存数据保留)。
        """
        if self._repo is None:
            return 0
        self._cache = {t.name: t for t in self._repo.list_all()}
        return len(self._cache)
