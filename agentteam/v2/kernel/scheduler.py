"""Scheduler — Ticket 调度器(替代 v1 的 826 行 RunManager 上帝对象)。

职责单一化:
- submit_request: Router 决策 → 建 Ticket → 入队
- _dispatch_loop: 有界线程池抢占 pending Ticket(CAS 防双派工)
- _execute_ticket: 建 Session + workspace → 运行 → 终态/等待人
- resume_attention: 人工决定后恢复会话(dsh: 审批 = 授权延续/终止)
- cancel: 撤权(任意非终态;dsh: 人的停止 = revoked_by_human)
- on_pool_changed: worker pool 配置热更新 → 在途 Ticket 记授权依据过期注记

内存态:一把锁 + 在跑集合 + 撤权标记集合;状态全在 SQLite。
"""
from __future__ import annotations

import json
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
from agentteam.v2.kernel.attention import AttentionRepo  # noqa: F401 (re-export)
from agentteam.v2.kernel.router import Router
from agentteam.v2.kernel.session import WorkerSession
from agentteam.v2.kernel.skills import SkillLoader
from agentteam.v2.kernel.workspace import WorkspaceManager
from agentteam.v2.notify.outbox import format_message
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.outbox import OutboxRepo
from agentteam.v2.storage.threads import SubscriptionRepo, ThreadRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo
from agentteam.v2.storage.workers import WorkerRepo

logger = get_logger("v2.kernel.scheduler")

# 人工决定语义 → attention.decide 记录值
_DECISIONS = {"approve", "reject", "answer", "revoked"}
# 需要外发 IM 通知的事件(dsh: 对外出口唯一——outbox 先存后发)
_NOTIFY_EVENTS = frozenset({
    ev.TICKET_STATUS_CHANGED, ev.TICKET_CANCELLED, ev.ATTENTION_CREATED,
})


class Scheduler:
    def __init__(
        self,
        *,
        ticket_repo: TicketRepo,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        event_repo: EventRepo,
        attention_repo: AttentionRepo,
        worker_repo: WorkerRepo,
        bus: EventBus,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        skill_loader: SkillLoader,
        workspace_root: Path,
        default_model: ModelRef,
        max_session_workers: int = 8,
        thread_repo: ThreadRepo | None = None,
        subscription_repo: SubscriptionRepo | None = None,
        outbox_repo: OutboxRepo | None = None,
        notify_webhook_url: str = "",
        console_url: str = "",
    ) -> None:
        self.tickets = ticket_repo
        self.sessions = session_repo
        self.messages = message_repo
        self.events = event_repo
        self.attentions = attention_repo
        self.workers = worker_repo
        self.threads = thread_repo
        self.subscriptions = subscription_repo
        self.outbox = outbox_repo
        self.notify_webhook_url = notify_webhook_url
        self.console_url = console_url
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
        self._cancelled: set[str] = set()  # ticket_ids 要求中断(协作式)
        self._policy_noted: dict[str, str] = {}  # ticket_id → 已注记的当前版本(防重复注记)
        self._follow_ups: dict[str, list[tuple[str, str]]] = {}  # ticket_id → [(text, by)]

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
        """崩溃恢复:pending 重排;孤儿 running session 标 failed;
        waiting_human 的 Attention 保留(重启后人工决定仍可恢复——transcript 在库)。"""
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
        # dsh Subscription: 线程来源的工单自动绑定(Ticket↔Thread 多对多)
        if thread_id and self.subscriptions is not None:
            self.subscriptions.bind(ticket.id, thread_id)
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

    # —— 追问直通(dsh Subscription fan-out: 人 → 认领 worker,只入不回) ——
    def deliver_follow_up(self, ticket_id: str, text: str, by: str = "human") -> dict:
        """同线程追问直达 worker 会话(过 Router)。

        dsh 红线:消息必须显式指向工单(本 API 即显式寻址);只入不回——
        worker 的回答仍走正常汇报链。终态工单不自动复活,提示重新提交。
        """
        ticket = self.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"ticket '{ticket_id}' not found")
        if ticket.status.is_terminal():
            raise RuntimeError(
                "工单已终态;不自动复活(dsh: ended ticket → 请重新提交并明确续跑)"
            )
        with self._lock:
            self._follow_ups.setdefault(ticket_id, []).append(((text or "").strip(), by))
        self._emit(ticket_id, ev.FOLLOW_UP_DELIVERED, actor=by, payload={
            "text": (text or "")[:500], "state": ticket.status.value,
        })
        self._wake.set()  # 若工单还在 pending 队列,唤醒调度
        return {"ok": True, "state": ticket.status.value}

    # —— 取消(撤权) ——
    def cancel(self, ticket_id: str, by: str = "human") -> bool:
        """撤权:dsh 语义「人的停止 = 撤权」;pending/running/waiting 均可。"""
        ticket = self.tickets.get(ticket_id)
        if ticket is None or ticket.status.is_terminal():
            return False
        ok = self.tickets.cancel(ticket_id)
        if not ok:
            return False
        self._cancelled.add(ticket_id)
        self._emit(ticket_id, ev.TICKET_CANCELLED, actor=by, payload={
            "revoked_by_human": True, "from_status": ticket.status.value,
        })
        # waiting_human 的未决 Attention 一并撤销(不再可决定)
        for att in self.attentions.list_by_status("pending"):
            if att["ticket_id"] == ticket_id:
                self.attentions.decide(att["id"], decision="revoked", decided_by=by,
                                       reason="工单已撤权")
                self._emit(ticket_id, ev.ATTENTION_DECIDED, actor=by, payload={
                    "attention_id": att["id"], "decision": "revoked",
                })
        return True

    def is_cancelled(self, ticket_id: str) -> bool:
        return ticket_id in self._cancelled

    # —— Attention 恢复(dsh: 审批 = 授权延续;回答 = 解除阻塞) ——
    def resume_attention(
        self, attention_id: str, decision: str, decided_by: str = "human",
        answer: str | None = None, reason: str | None = None,
    ) -> dict:
        """人工决定一个 Attention 并恢复会话。

        - approval + approve: 恢复会话并真实执行该工具
        - approval + reject: 注入拒绝结果(worker 可改道)
        - question + answer: 注入人工回答
        终态工单/已撤权工单的 Attention 不可决定(409)。
        """
        if decision not in _DECISIONS:
            raise ValueError(f"invalid decision: {decision}")
        att = self.attentions.get(attention_id)
        if att is None:
            raise KeyError(f"attention '{attention_id}' not found")
        if att["status"] != "pending":
            raise RuntimeError(f"attention already decided: {att['decision']}")
        ticket = self.tickets.get(att["ticket_id"])
        if ticket is None or ticket.status.is_terminal():
            raise RuntimeError(f"ticket is terminal ({ticket.status.value if ticket else 'gone'})")

        if not self.attentions.decide(attention_id, decision=decision,
                                      decided_by=decided_by, reason=reason):
            raise RuntimeError("attention already decided (race)")
        self._emit(ticket.id, ev.ATTENTION_DECIDED, actor=decided_by, session_id=att["session_id"],
                   payload={"attention_id": attention_id, "decision": decision,
                            "type": att["type"], "answer": (answer or "")[:500]})

        # waiting_human → running;线程池恢复会话
        self.sessions.cas_status(att["session_id"], SessionStatus.WAITING_HUMAN, SessionStatus.RUNNING)
        self.tickets.cas_status(ticket.id, TicketStatus.WAITING_HUMAN, TicketStatus.RUNNING)
        self._emit(ticket.id, ev.TICKET_STATUS_CHANGED, payload={"to": "running"})
        self._pool.submit(
            self._resume_session, ticket.id, attention_id, decision, answer,
        )
        return {"ok": True, "attention_id": attention_id, "decision": decision}

    def _resume_session(
        self, ticket_id: str, attention_id: str, decision: str, answer: str | None,
    ) -> None:
        attention = self.attentions.get(attention_id)
        if attention is None:
            return
        self._run_session(self.tickets.get(ticket_id), session_id=attention["session_id"],
                          attention=attention, decision=decision, answer=answer)

    # —— worker pool 热更新(dsh: 变更通知到在途工单,不中断) ——
    def on_pool_changed(self) -> int:
        """worker pool 配置变化后,给在途 Ticket 记授权依据过期注记(每版本一次)。"""
        current = self._policy_version()
        noted = 0
        for status in (TicketStatus.PENDING, TicketStatus.RUNNING, TicketStatus.WAITING_HUMAN):
            for t in self.tickets.list_by_status(status):
                if not (t.mandate and t.mandate.policy_version):
                    continue
                if t.mandate.policy_version == current:
                    continue
                if self._policy_noted.get(t.id) == current:
                    continue  # 该版本已注记过
                self._policy_noted[t.id] = current
                self._emit(t.id, ev.POLICY_EXPIRED, payload={
                    "run_policy_version": t.mandate.policy_version,
                    "current_policy_version": current,
                    "note": "工人池配置已热更新,该工单的授权依据已过期(不中断执行)",
                })
                noted += 1
        return noted

    # —— 内部 ——
    def _policy_version(self) -> str:
        return policy_version_for_pool([w.to_dict() for w in self.workers.list_all()])

    def _emit(self, ticket_id: str, event_type: str, actor: str = "system",
              session_id: str | None = None, payload: dict | None = None) -> None:
        envelope = self.events.insert(ticket_id, event_type, actor=actor,
                                      session_id=session_id, payload=payload)
        self.bus.publish(ticket_id, envelope)
        # IM 出站(先存后发):关键事件进 outbox,OutboxWorker 消费
        if self.outbox is not None and self.notify_webhook_url and event_type in _NOTIFY_EVENTS:
            body = format_message(event_type, {**(payload or {}), "ticket_id": ticket_id},
                                  self.console_url)
            self.outbox.enqueue(ticket_id, event_type, self.notify_webhook_url,
                                json.dumps(body, ensure_ascii=False))

    def _pop_follow_ups(self, ticket_id: str) -> list[tuple[str, str]]:
        """取走该工单积压的追问(session 在迭代边界消费)。"""
        with self._lock:
            items = self._follow_ups.pop(ticket_id, [])
        return items

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
            return
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
        if not self.tickets.cas_status(ticket_id, TicketStatus.PENDING, TicketStatus.RUNNING):
            return
        self._emit(ticket_id, ev.TICKET_STATUS_CHANGED, payload={"to": "running"})
        self._run_session(ticket, worker_name=worker.name)

    def _run_session(
        self, ticket: Ticket | None, worker_name: str | None = None,
        session_id: str | None = None,
        attention: dict | None = None, decision: str | None = None,
        answer: str | None = None,
    ) -> None:
        """执行(或恢复)一个会话;在线程池内运行。"""
        if ticket is None:
            return
        if worker_name:
            worker = self.workers.get(worker_name)
        else:
            worker = self.workers.get(ticket.worker_name or "")
        if worker is None:
            return
        if session_id is None:
            session_id = uuid4().hex
            workspace = self.workspaces.for_ticket(ticket.id)
            model_dict = worker.model or {
                "provider": self.default_model.provider, "name": self.default_model.name,
            }
            self.sessions.create(
                session_id, ticket.id, worker.name, str(workspace),
                worker.max_iterations, model_dict,
            )
        session = WorkerSession(
            ticket=ticket, session_id=session_id, worker=worker,
            workspace_dir=Path(self.sessions.get(session_id)["workspace_dir"]),
            model_provider=self.mp, tool_registry=self.registry,
            skill_loader=self.skill_loader,
            ticket_repo=self.tickets, session_repo=self.sessions,
            message_repo=self.messages, event_repo=self.events,
            attention_repo=self.attentions, bus=self.bus,
            default_model=self.default_model,
            is_cancelled=self.is_cancelled,
            pop_follow_ups=lambda: self._pop_follow_ups(ticket.id),
        )
        if attention is not None:
            result = session.resume_after_decision(attention, decision or "answer", answer)
        else:
            result = session.run()

        if result.status == SessionStatus.DONE:
            self.tickets.cas_status(ticket.id, TicketStatus.RUNNING, TicketStatus.DONE, result=result.reply)
            self._emit(ticket.id, ev.TICKET_STATUS_CHANGED, payload={"to": "done"})
        elif result.status == SessionStatus.FAILED:
            self.tickets.cas_status(ticket.id, TicketStatus.RUNNING, TicketStatus.FAILED, result=result.error)
            self._emit(ticket.id, ev.TICKET_STATUS_CHANGED, payload={
                "to": "failed", "error": (result.error or "")[:300],
            })
        elif result.status == SessionStatus.WAITING_HUMAN:
            self.tickets.cas_status(ticket.id, TicketStatus.RUNNING, TicketStatus.WAITING_HUMAN)
            self._emit(ticket.id, ev.TICKET_STATUS_CHANGED, payload={
                "to": "waiting_human", "attention_id": result.attention_id,
            })
        elif result.status == SessionStatus.CANCELLED:
            # cancel() 已落状态;这里只补会话级事件
            self._emit(ticket.id, ev.SESSION_ENDED, payload={
                "status": "cancelled", "note": "revoked_by_human",
            })
