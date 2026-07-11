# agentteam/runtime/trace.py
"""执行轨迹写入器：将 AuditEvent 写入持久化存储。"""
from __future__ import annotations

from typing import Any, Protocol

from agentteam.storage.audit import AuditRepo


class TraceWriter(Protocol):
    """执行轨迹写入器协议。节点通过它 emit 审计事件。"""

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        """写入一条审计事件。"""
        ...


class SqliteTraceWriter:
    """将 AuditEvent 写入 SQLite run_events 表。"""

    def __init__(self, audit_repo: AuditRepo) -> None:
        self._repo = audit_repo

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        self._repo.add_event(run_id, event_type, actor, payload, duration_ms, tokens)


class FakeTraceWriter:
    """测试用轨迹写入器，收集事件到内存列表。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        self.events.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "actor": actor,
                "payload": payload,
                "duration_ms": duration_ms,
                "tokens": tokens,
            }
        )
