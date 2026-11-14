"""Scheduler — Ticket 调度器(替代 v1 的 826 行 RunManager 上帝对象)。

职责单一化(对照 v1 教训):
- submit_request: Router 决策 → 建 Ticket → 入队
- _dispatch_loop: 有界线程池抢占 pending Ticket(CAS 防双派工)
- _execute_ticket: 建 Session + workspace → 运行 → 落终态
- 崩溃恢复: 启动时重排 pending、孤儿 running session 标 failed
- cancel(P1 完整撤权;P0 仅 pending)

内存态只有一把锁 + 一个在跑集合;状态全在 SQLite(v1 教训:6 个 dict 全内存)。
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.tools.registry import ToolRegistry
from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import SessionStatus, TicketStatus
from agentteam.v2.domain.mandate import Mandate, policy_version_for_pool
from agentteam.v2.domain.ticket import Ticket
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.router import DispatchDecision, Router
from agentteam.v2.kernel.session import WorkerSession
from agentteam.v2.kernel.skills import SkillLoader
from agentteam.v2.kernel.workspace import WorkspaceManager
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo
from agentteam.v2.storage.workers import WorkerRepo

logger = get_logger("v2.kernel.scheduler")


class Scheduler:
    def __init__(
        self,
        *,
        ticket_repo: TicketRepo,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        event_repo: EventRepo,
        worker_repo: WorkerRepo,
        bus: EventBus,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        skill_loader: SkillLoader,
        workspace_root: Path,
        default_model: ModelRef,
        max_session_workers: int = 8,
    ) -> None:
        self.tickets = ticket_repo
        self.sessions = session_repo
        self.messages = message_repo
        self.events = event_repo
        self.workers = worker_repo
        self.bus = bus
        self.mp = model_provider
        self.registry = tool_registry
        self.skill_loader = skill_loader
        self.workspaces = WorkspaceManager(workspace_root)
        self.default_model = default_model
        self.router = Router(model_provider, default_model)
        self._pool = ThreadPoolExecutor(
            max_workers=max_session_workers, thread_name_prefix="v2-session",
        )
        self._queue: queue.Queue[str] = queue.Queue()
        self._dispatch_thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop_flag = False
        self._lock = threading.Lock()

    # —— 生命周期 ——
    def start(self) -> None:
        self._stop_flag = False
        self._recover()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="v2-dispatch", daemon=True,
        )
        self._dispatch_thread.start()

    def shutdown(self, wait: bool = True) -> None:
        self._stop_flag = True
        self._wake.set()
        if self._dispatch_thread:
            self._dispatch_thread.join(timeout=5)
        self._pool.shutdown(wait=wait)

    def _recover(self) -> None:
        """崩溃恢复:pending 重排;孤儿 running session 标 failed(v0 无跨进程恢复)。"""
        for ticket_id in self.tickets.list_pending_ids():
            self._queue.put(ticket_id)
        for s in self.sessions.list_running():
            self.sessions.cas_status(s["id"], SessionStatus.RUNNING, SessionStatus.FAILED)
            self.tickets.cas_status(
                s["ticket_id"], TicketStatus.RUNNING, TicketStatus.FAILED,
                result="进程重启中断(会话孤儿)",
            )

    # —— 入口 ——
    def submit_request(
        self, text: str, submitted_by: str = "api",
        worker_name: str | None = None, thread_id: str | None = None,
    ) -> dict:
        """人/Source 的请求入口:Router 决策 → Ticket(pending) → 入队。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("request text is empty")
        decision = self.router.decide(text, self.workers.list_all(), requested_worker=worker_name)

        if decision.action == "reply":
            # 不立项:Router 直接答复(dsh: 不是所有消息都要变成工单)
            return {"action": "reply", "reply": decision.reply}

        ticket = Ticket(
            id=uuid4().hex,
            title=(decision.note or text)[:60],
            task=decision.goal,
            status=TicketStatus.PENDING,
            worker_name=decision.worker_name,
            thread_id=thread_id,
            mandate=Mandate(
                basis="explicit", submitted_by=submitted_by,
                policy_version=self._policy_version(),
                scope=None,
            ),
        )
        self.tickets.create(ticket)
        self._emit(ticket.id, ev.TICKET_CREATED, actor=submitted_by, payload={
            "title": ticket.title, "task": ticket.task,
            "worker": ticket.worker_name, "via": decision.via,
            "mandate": ticket.mandate.to_dict(),
        })
        self._emit(ticket.id, ev.TICKET_DISPATCHED, payload={
            "worker": ticket.worker_name, "note": decision.note, "acceptance": decision.acceptance,
        })
        self._queue.put(ticket.id)
        self._wake.set()
        return {
            "action": "dispatch",
            "ticket_id": ticket.id,
            "worker": ticket.worker_name,
            "note": decision.note,
        }

    def cancel_pending(self, ticket_id: str, by: str = "human") -> bool:
        """P0: 仅 pending 可取消;运行中撤权在 P1(cooperative cancel)。"""
        ok = self.tickets.cas_status(ticket_id, TicketStatus.PENDING, TicketStatus.CANCELLED)
        if ok:
            self._emit(ticket_id, ev.TICKET_CANCELLED, actor=by, payload={
                "revoked_by_human": True, "stage": "pending",
            })
        return ok

    # —— 内部 ——
    def _policy_version(self) -> str:
        return policy_version_for_pool([w.to_dict() for w in self.workers.list_all()])

    def _emit(self, ticket_id: str, event_type: str, actor: str = "system",
              session_id: str | None = None, payload: dict | None = None) -> None:
        envelope = self.events.insert(ticket_id, event_type, actor=actor,
                                      session_id=session_id, payload=payload)
        self.bus.publish(ticket_id, envelope)

    def _dispatch_loop(self) -> None:
        while not self._stop_flag:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while not self._stop_flag:
                try:
                    ticket_id = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._try_execute(ticket_id)

    def _try_execute(self, ticket_id: str) -> None:
        ticket = self.tickets.get(ticket_id)
        if ticket is None or ticket.status != TicketStatus.PENDING:
            return  # 已取消/已终态
        worker = self.workers.get(ticket.worker_name or "") if ticket.worker_name else None
        if worker is None or not worker.enabled:
            self.tickets.cas_status(
                ticket_id, TicketStatus.PENDING, TicketStatus.FAILED,
                result=f"worker '{ticket.worker_name}' 不存在或已禁用",
            )
            self._emit(ticket_id, ev.TICKET_STATUS_CHANGED, payload={
                "to": "failed", "reason": "worker missing",
            })
            return
        # CAS 抢占:pending → running(并发双派工由 CAS 拦截)
        if not self.tickets.cas_status(ticket_id, TicketStatus.PENDING, TicketStatus.RUNNING):
            return
        self._emit(ticket_id, ev.TICKET_STATUS_CHANGED, payload={"to": "running"})
        self._pool.submit(self._execute_ticket, ticket_id, worker)

    def _execute_ticket(self, ticket_id: str, worker: WorkerDef) -> None:
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            return
        session_id = uuid4().hex
        workspace = self.workspaces.for_ticket(ticket_id)
        model_dict = worker.model or {
            "provider": self.default_model.provider, "name": self.default_model.name,
        }
        self.sessions.create(
            session_id, ticket_id, worker.name, str(workspace),
            worker.max_iterations, model_dict,
        )
        session = WorkerSession(
            ticket=ticket, session_id=session_id, worker=worker,
            workspace_dir=workspace,
            model_provider=self.mp, tool_registry=self.registry,
            skill_loader=self.skill_loader,
            ticket_repo=self.tickets, session_repo=self.sessions,
            message_repo=self.messages, event_repo=self.events, bus=self.bus,
            default_model=self.default_model,
        )
        result = session.run()
        if result.status == SessionStatus.DONE:
            self.tickets.cas_status(
                ticket_id, TicketStatus.RUNNING, TicketStatus.DONE, result=result.reply,
            )
            self._emit(ticket_id, ev.TICKET_STATUS_CHANGED, payload={"to": "done"})
        elif result.status == SessionStatus.FAILED:
            self.tickets.cas_status(
                ticket_id, TicketStatus.RUNNING, TicketStatus.FAILED, result=result.error,
            )
            self._emit(ticket_id, ev.TICKET_STATUS_CHANGED, payload={
                "to": "failed", "error": (result.error or "")[:300],
            })
        # waiting_human(P1): ticket 保持 running→waiting_human 由 Attention 流程迁移
