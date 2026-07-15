"""EventBus 线程安全事件队列 + BroadcastTraceWriter 双写。"""
from __future__ import annotations

import queue as queue_mod
import threading
from typing import Any

from agentteam.storage.audit import AuditRepo

# 每个订阅者队列上限。事件已持久化到 SQLite，慢消费者溢出时丢弃旧事件，
# SSE 重连后可从 SQLite 回放补全。
_MAX_QUEUE_SIZE = 1000


class EventBus:
    """线程安全事件总线：桥接后台线程（TraceWriter）→ SSE 端点。

    每个 run_id 可有多个订阅者（多 SSE 客户端）。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[queue_mod.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: str) -> queue_mod.Queue:
        """订阅 run_id 的事件流，返回一个 Queue。"""
        q: queue_mod.Queue = queue_mod.Queue(maxsize=_MAX_QUEUE_SIZE)
        with self._lock:
            if run_id not in self._subscribers:
                self._subscribers[run_id] = []
            self._subscribers[run_id].append(q)
        return q

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """向 run_id 的所有订阅者发布事件。无订阅者时 no-op。

        队列满时丢弃最旧事件腾出空间——事件已在 SQLite 持久化，
        SSE 重连后可从历史回放补全。
        """
        with self._lock:
            subs = list(self._subscribers.get(run_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue_mod.Full:
                try:
                    q.get_nowait()  # 丢最旧
                except queue_mod.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue_mod.Full:
                    pass  # 极端情况：放弃

    def unsubscribe(self, run_id: str, q: queue_mod.Queue) -> None:
        """取消订阅。清理空列表。"""
        with self._lock:
            if run_id in self._subscribers:
                try:
                    self._subscribers[run_id].remove(q)
                except ValueError:
                    pass
                if not self._subscribers[run_id]:
                    del self._subscribers[run_id]


class BroadcastTraceWriter:
    """实现 TraceWriter 协议：写 SQLite（持久）+ 发 EventBus（实时）。

    emit() 捕获 AuditRepo.add_event() 返回的 SQLite 行 ID，
    放入发布到 EventBus 的事件 dict 中，供 SSE 回放去重。
    """

    def __init__(self, audit_repo: AuditRepo, bus: EventBus) -> None:
        self._audit_repo = audit_repo
        self._bus = bus

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        event_id = self._audit_repo.add_event(
            run_id, event_type, actor, payload, duration_ms, tokens
        )
        self._bus.publish(
            run_id,
            {
                "id": event_id,
                "run_id": run_id,
                "event_type": event_type,
                "actor": actor,
                "payload": payload or {},
                "duration_ms": duration_ms,
                "tokens": tokens,
            },
        )
