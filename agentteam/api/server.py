"""FastAPI app 工厂。"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.dashboard import dashboard_router
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.models.provider import ModelProvider
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry


def create_app(
    db_path: str = "data/agentteam.db",
    model_provider: ModelProvider | None = None,
    tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    app = FastAPI(title="AgentTeam")

    conn = init_db(db_path)
    # 共享锁：SqliteSaver / RunRepo / AuditRepo 共用同一 sqlite3.Connection，
    # 必须用同一把锁串行化所有连接访问，否则多线程下会触发
    # sqlite3.InterfaceError: bad parameter or other API misuse。
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()

    saver = SqliteSaver(conn)
    saver.lock = conn_lock  # 让 SqliteSaver 也用同一把锁
    assert saver.lock is conn_lock  # 防御：若 langgraph 改名 lock 属性则静默失效
    saver.setup()

    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, mp, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    app.include_router(dashboard_router(run_repo, audit_repo))

    # 挂载前端静态文件(生产模式)。web/dist 不存在时跳过,不影响 API。
    WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if WEB_DIST.is_dir():
        from starlette.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")

    return app
