"""v2 组件容器:一次装配,API/测试共用(对照 v1 server.py 20 组件散装装配的教训)。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.tools.registry import ToolRegistry
from agentteam.v2.kernel.eval_engine import EvalEngine
from agentteam.v2.kernel.inbound import InboundEngine
from agentteam.v2.kernel.memory import MemoryStore
from agentteam.v2.kernel.postmortem import PostmortemEngine
from agentteam.v2.kernel.scheduler import Scheduler
from agentteam.v2.kernel.skills import SkillLoader
from agentteam.v2.notify.outbox import OutboxWorker
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.db import init_db
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.outbox import OutboxRepo
from agentteam.v2.storage.org import OrgRepo
from agentteam.v2.storage.sources import EvaluationRepo, SourceRepo
from agentteam.v2.storage.threads import SubscriptionRepo, ThreadRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo
from agentteam.v2.storage.workers import WorkerRepo

logger = get_logger("v2.api.deps")


@dataclass
class V2Components:
    """v2 全部组件的持有者。"""

    conn: object
    lock: threading.Lock
    model_provider: ModelProvider
    tool_registry: ToolRegistry
    skill_loader: SkillLoader
    worker_repo: WorkerRepo
    ticket_repo: TicketRepo
    session_repo: SessionRepo
    message_repo: MessageRepo
    event_repo: EventRepo
    attention_repo: AttentionRepo
    thread_repo: ThreadRepo
    subscription_repo: SubscriptionRepo
    outbox_repo: OutboxRepo
    outbox_worker: OutboxWorker
    source_repo: SourceRepo
    evaluation_repo: EvaluationRepo
    inbound_engine: InboundEngine
    source_daemon: object | None
    bus: EventBus
    scheduler: Scheduler
    default_model: ModelRef
    aux_model: ModelRef | None
    memory_store: MemoryStore | None
    postmortem: PostmortemEngine | None
    distiller: object | None
    org_repo: OrgRepo


def create_v2_components(
    *,
    db_path: str,
    workspace_root: Path,
    model_provider: ModelProvider | None = None,
    tool_registry: ToolRegistry | None = None,
    skills_dir: Path | None = None,
    default_provider: str = "qwen",
    default_model_name: str = "qwen-max",
    max_session_workers: int = 8,
    notify_webhook_url: str = "",
    console_url: str = "",
    notify_retry_max: int = 5,
    notify_backoff_base: float = 5.0,
    notify_timeout_seconds: float = 5.0,
    source_poll_interval: float = 10.0,
    memory_dir: Path | None = None,        # P4 记忆库根目录;None = 停用
    aux_provider: str = "",                # P4 轻量辅助模型(Eval/复盘);空 = 回落默认
    aux_model_name: str = "",
    postmortem_enabled: bool = True,
    memory_evict_days: int = 0,            # P5: 从未命中教训的归档天数;0 = 关闭
    workspace_archive_days: int = 0,       # P5: 终态工作区归档天数;0 = 关闭
    eval_distill_threshold: int = 0,       # P5: 确认样本蒸馏阈值;0 = 关闭
) -> V2Components:
    """装配 v2 全部组件(独立 DB;model_provider/tool_registry 可与 v1 共享实例)。"""
    conn = init_db(db_path)
    lock = threading.Lock()
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    skill_loader = SkillLoader(skills_dir)
    default_model = ModelRef(default_provider, default_model_name)
    aux_model = ModelRef(aux_provider or default_provider, aux_model_name or default_model_name) \
        if (aux_provider or aux_model_name) else None

    # P4 学习闭环:文件式记忆 + 终态复盘(Letta sleep-time 范式;未配目录则整体停用)
    memory_store = MemoryStore(memory_dir) if memory_dir is not None else None
    postmortem = None
    # P5: 观察模式确认信号蒸馏器(postmortem housekeeping 与 EvalEngine 都要用,先建)
    distiller = None
    if memory_store is not None and eval_distill_threshold > 0:
        from agentteam.v2.kernel.eval_distill import InboundDistiller
        distiller = InboundDistiller(
            memory_dir / "inbound", mp, aux_model or default_model,
            threshold=eval_distill_threshold,
        )

    worker_repo = WorkerRepo(conn, lock=lock)
    ticket_repo = TicketRepo(conn, lock=lock)
    session_repo = SessionRepo(conn, lock=lock)
    message_repo = MessageRepo(conn, lock=lock)
    event_repo = EventRepo(conn, lock=lock)
    attention_repo = AttentionRepo(conn, lock=lock)
    thread_repo = ThreadRepo(conn, lock=lock)
    subscription_repo = SubscriptionRepo(conn, lock=lock)
    outbox_repo = OutboxRepo(conn, lock=lock)
    org_repo = OrgRepo(conn, lock=lock)   # P6: 组织架构树(任意深度)
    bus = EventBus()

    scheduler = Scheduler(
        ticket_repo=ticket_repo, session_repo=session_repo,
        message_repo=message_repo, event_repo=event_repo,
        attention_repo=attention_repo,
        worker_repo=worker_repo, bus=bus,
        model_provider=mp, tool_registry=tr, skill_loader=skill_loader,
        workspace_root=workspace_root, default_model=default_model,
        max_session_workers=max_session_workers,
        thread_repo=thread_repo, subscription_repo=subscription_repo,
        outbox_repo=outbox_repo if notify_webhook_url else None,
        notify_webhook_url=notify_webhook_url, console_url=console_url,
        memory_store=memory_store, org_repo=org_repo,
    )
    if memory_store is not None and postmortem_enabled:
        # P5 工作区归档回调(终态工单只增不减的欠账;sleep-time 角色顺带保洁)
        sweeper = None
        if workspace_archive_days > 0:
            def _sweep():
                return scheduler.workspaces.archive_stale(
                    [(t.id, t.ended_at) for t in ticket_repo.list_ended()],
                    max_age_days=workspace_archive_days,
                )
            sweeper = _sweep
        postmortem = PostmortemEngine(
            memory_store=memory_store,
            model_provider=mp,
            aux_model=aux_model or default_model,
            ticket_repo=ticket_repo, session_repo=session_repo,
            message_repo=message_repo, event_repo=event_repo,
            attention_repo=attention_repo, bus=bus,
            memory_evict_days=memory_evict_days,
            distiller=distiller,
            workspace_sweeper=sweeper,
        )
        scheduler.postmortem = postmortem
    outbox_worker = OutboxWorker(
        outbox_repo, retry_max=notify_retry_max, backoff_base=notify_backoff_base,
        send_timeout=notify_timeout_seconds,
    )

    # —— P3: Source 入站管线(gate → Eval → 立项/观察)+ 常驻 daemon ——
    source_repo = SourceRepo(conn, lock=lock)
    evaluation_repo = EvaluationRepo(conn, lock=lock)
    eval_engine = EvalEngine(mp, aux_model or default_model,
                             rules_provider=distiller.lines if distiller else None)
    inbound_engine = InboundEngine(
        source_repo=source_repo, thread_repo=thread_repo,
        evaluation_repo=evaluation_repo, eval_engine=eval_engine,
        event_repo=event_repo, bus=bus,
        submit_from_source=scheduler.submit_from_source,
        fanout=scheduler.fan_out_thread,
        on_confirmed=distiller.record_confirm if distiller else None,
    )
    # poll 型源骨架(github/feishu)随库配置注册;webhook 型由 HTTP 驱动不进 daemon
    from agentteam.v2.sources.daemon import SourceDaemon
    from agentteam.v2.sources.poll import FeishuSource, GitHubSource

    poll_sources = []
    for src in source_repo.list_all():
        if not src["enabled"]:
            continue
        if src["kind"] == "github":
            poll_sources.append(GitHubSource(src["name"], src["config"], inbound_engine.handle))
        elif src["kind"] == "feishu":
            poll_sources.append(FeishuSource(src["name"], src["config"], inbound_engine.handle))
    source_daemon = SourceDaemon(poll_sources, inbound_engine.handle,
                                 poll_interval=source_poll_interval)

    return V2Components(
        conn=conn, lock=lock, model_provider=mp, tool_registry=tr,
        skill_loader=skill_loader, worker_repo=worker_repo,
        ticket_repo=ticket_repo, session_repo=session_repo,
        message_repo=message_repo, event_repo=event_repo,
        attention_repo=attention_repo, thread_repo=thread_repo,
        subscription_repo=subscription_repo, outbox_repo=outbox_repo,
        outbox_worker=outbox_worker, source_repo=source_repo,
        evaluation_repo=evaluation_repo, inbound_engine=inbound_engine,
        source_daemon=source_daemon, bus=bus,
        scheduler=scheduler, default_model=default_model,
        aux_model=aux_model, memory_store=memory_store, postmortem=postmortem,
        distiller=distiller, org_repo=org_repo,
    )
