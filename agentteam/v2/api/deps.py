"""v2 组件容器:一次装配,API/测试共用(对照 v1 server.py 20 组件散装装配的教训)。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.tools.registry import ToolRegistry
from agentteam.v2.kernel.scheduler import Scheduler
from agentteam.v2.kernel.skills import SkillLoader
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.db import init_db
from agentteam.v2.storage.events import EventBus, EventRepo
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
    bus: EventBus
    scheduler: Scheduler
    default_model: ModelRef


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
) -> V2Components:
    """装配 v2 全部组件(独立 DB;model_provider/tool_registry 可与 v1 共享实例)。"""
    conn = init_db(db_path)
    lock = threading.Lock()
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    skill_loader = SkillLoader(skills_dir)
    default_model = ModelRef(default_provider, default_model_name)

    worker_repo = WorkerRepo(conn, lock=lock)
    ticket_repo = TicketRepo(conn, lock=lock)
    session_repo = SessionRepo(conn, lock=lock)
    message_repo = MessageRepo(conn, lock=lock)
    event_repo = EventRepo(conn, lock=lock)
    attention_repo = AttentionRepo(conn, lock=lock)
    bus = EventBus()

    scheduler = Scheduler(
        ticket_repo=ticket_repo, session_repo=session_repo,
        message_repo=message_repo, event_repo=event_repo,
        attention_repo=attention_repo,
        worker_repo=worker_repo, bus=bus,
        model_provider=mp, tool_registry=tr, skill_loader=skill_loader,
        workspace_root=workspace_root, default_model=default_model,
        max_session_workers=max_session_workers,
    )
    return V2Components(
        conn=conn, lock=lock, model_provider=mp, tool_registry=tr,
        skill_loader=skill_loader, worker_repo=worker_repo,
        ticket_repo=ticket_repo, session_repo=session_repo,
        message_repo=message_repo, event_repo=event_repo,
        attention_repo=attention_repo, bus=bus,
        scheduler=scheduler, default_model=default_model,
    )
