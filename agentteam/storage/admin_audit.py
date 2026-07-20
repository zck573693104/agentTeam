"""admin_events 表的读写:管理操作审计(P-A3 对标阿里云 AgentTeams "安全审计")。

记录 Team/Library/Evolution/Quota 等管理面 CRUD 操作,与 run_events(执行面)分离:
- run_events: actor 是 agent/system,记录 run 执行轨迹
- admin_events: actor 是 operator/api-user,记录管理操作

典型 event_type:
- team_created / team_updated / team_deleted
- library_agent_created / library_agent_updated / library_agent_deleted
- quota_set / quota_deleted
- evolution_rolled_back
"""
from __future__ import annotations

import json
from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class AdminAuditRepo(BaseSqliteRepo):
    """admin_events 表的读写。

    当与其他 Repo 共享同一 sqlite3.Connection 时,须传入同一个 lock 串行化访问。
    """

    def add_event(
        self,
        event_type: str,
        resource: str,
        resource_id: str | None = None,
        actor: str = "api-user",
        payload: dict[str, Any] | None = None,
    ) -> int:
        """记录一条管理操作事件,返回自增 id。"""
        now = _now()
        payload_str = json.dumps(payload or {}, ensure_ascii=False, default=str)
        cur = self._execute(
            "INSERT INTO admin_events (event_type, resource, resource_id, actor, timestamp, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_type, resource, resource_id, actor, now, payload_str),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else 0

    def list_events(
        self,
        limit: int = 100,
        offset: int = 0,
        resource: str | None = None,
        actor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        event_type: str | None = None,
    ) -> list:
        """按时间倒序查询管理事件,支持 resource/actor/time_range/event_type 过滤。

        P-B8: 新增 start_time/end_time(ISO8601 字符串)实现时间范围检索,
        对标阿里云 AgentTeams "审计日志时间范围查询"。
        新增 event_type 过滤,支持按事件类型(如 team_created/quota_set)精确查询。
        """
        sql = "SELECT * FROM admin_events"
        conditions: list[str] = []
        params: list[Any] = []
        if resource is not None:
            conditions.append("resource = ?")
            params.append(resource)
        if actor is not None:
            conditions.append("actor = ?")
            params.append(actor)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        return self._fetchall(sql, tuple(params))

    def count_events(
        self,
        resource: str | None = None,
        actor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """统计管理事件总数(分页用),支持与 list_events 相同的过滤条件。"""
        sql = "SELECT COUNT(*) AS n FROM admin_events"
        conditions: list[str] = []
        params: list[Any] = []
        if resource is not None:
            conditions.append("resource = ?")
            params.append(resource)
        if actor is not None:
            conditions.append("actor = ?")
            params.append(actor)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        row = self._fetchone(sql, tuple(params))
        return int(row["n"]) if row else 0
