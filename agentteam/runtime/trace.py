# agentteam/runtime/trace.py
"""执行轨迹写入器:将 AuditEvent 写入持久化存储。

P-B3 全链路 Trace(对标阿里云 AgentTeams "调用链/工具链/决策链"):
- TraceWriter.emit 支持 trace_id / parent_span_id / chain 三个新参数
- chain 由 event_type 自动推断(_CHAIN_MAP),调用方也可显式指定
- 一次 run 共享一个 trace_id(由 RunManager 启动时生成)
- parent_span_id 用于嵌套关系:tool_call 事件的 parent 是 worker span
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from agentteam.storage.audit import AuditRepo, _infer_chain


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
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        chain: str | None = None,
    ) -> None:
        """写入一条审计事件。

        - trace_id:同一次 run 内共享的根 trace id(None 时由实现层兜底)
        - parent_span_id:父 span(嵌套关系)
        - chain:'call'/'tool'/'decision',None 时按 event_type 推断
        """
        ...


class SqliteTraceWriter:
    """将 AuditEvent 写入 SQLite run_events 表。"""

    def __init__(self, audit_repo: AuditRepo) -> None:
        self._repo = audit_repo
        # P-B3:run_id → trace_id 映射,首次见到 run 时生成 trace_id
        # 后续该 run 所有事件复用同一 trace_id,实现"一次 run = 一条完整 trace"
        self._trace_ids: dict[str, str] = {}

    def _get_trace_id(self, run_id: str) -> str:
        """获取或创建 run 的 trace_id。"""
        tid = self._trace_ids.get(run_id)
        if tid is None:
            tid = uuid.uuid4().hex
            self._trace_ids[run_id] = tid
        return tid

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        chain: str | None = None,
    ) -> None:
        # 调用方未显式传 trace_id 时,自动按 run_id 生成/复用
        if trace_id is None:
            trace_id = self._get_trace_id(run_id)
        if chain is None:
            chain = _infer_chain(event_type)
        self._repo.add_event(
            run_id, event_type, actor, payload,
            duration_ms=duration_ms,
            tokens=tokens,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            chain=chain,
        )


class FakeTraceWriter:
    """测试用轨迹写入器,收集事件到内存列表。"""

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
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        chain: str | None = None,
    ) -> None:
        if chain is None:
            chain = _infer_chain(event_type)
        self.events.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "actor": actor,
                "payload": payload,
                "duration_ms": duration_ms,
                "tokens": tokens,
                "trace_id": trace_id,
                "parent_span_id": parent_span_id,
                "chain": chain,
            }
        )
