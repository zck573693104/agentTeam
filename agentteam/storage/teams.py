"""teams 表的读写:Team 配置持久化。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.domain.team import Team


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamRepo:
    """teams 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def upsert(self, team: Team) -> None:
        """INSERT OR REPLACE,序列化为 JSON。"""
        config = json.dumps(team_to_dict(team), ensure_ascii=False)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO teams (name, description, config, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "description=excluded.description, config=excluded.config, updated_at=excluded.updated_at",
                (team.name, team.description, config, now, now),
            )
            self._conn.commit()

    def get(self, name: str) -> Team | None:
        """SELECT config,反序列化为 Team。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT config FROM teams WHERE name = ?", (name,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return team_from_dict(json.loads(row["config"]))

    def list_all(self) -> list[Team]:
        """SELECT all,反序列化为 Team 列表。"""
        with self._lock:
            cur = self._conn.execute("SELECT config FROM teams ORDER BY name")
            rows = cur.fetchall()
        return [team_from_dict(json.loads(r["config"])) for r in rows]

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM teams WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0
