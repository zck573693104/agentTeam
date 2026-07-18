"""FastAPI app 工厂。"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver
from starlette.staticfiles import StaticFiles

from agentteam.api.events import EventBus
from agentteam.api.routes.dashboard import dashboard_router
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelProvider
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry

# 哨兵：web_dist 参数未显式传入时使用此值,区分"用默认路径"和"显式禁用挂载"。
_DEFAULT: object = object()

# 默认前端静态文件目录(生产构建产物)。计算一次,避免每次 create_app 都 resolve。
_DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def create_app(
    db_path: str = "data/agentteam.db",
    model_provider: ModelProvider | None = None,
    tool_registry: ToolRegistry | None = None,
    agent_library: AgentLibrary | None = None,
    web_dist: Path | None | object = _DEFAULT,
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
    lib = agent_library or AgentLibrary()

    saver = SqliteSaver(conn)
    saver.lock = conn_lock  # 让 SqliteSaver 也用同一把锁
    assert saver.lock is conn_lock  # 防御：若 langgraph 改名 lock 属性则静默失效
    saver.setup()

    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, mp, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver, agent_library=lib,
        )
    )
    app.include_router(dashboard_router(run_repo, audit_repo))

    # 挂载前端静态文件(生产模式)。
    # - web_dist=_DEFAULT(默认): 使用 _DEFAULT_WEB_DIST,目录存在才挂载
    # - web_dist=None: 显式禁用挂载(用于测试)
    # - web_dist=<Path>: 使用调用者指定的目录,目录存在才挂载
    if web_dist is _DEFAULT:
        web_dist_path: Path | None = _DEFAULT_WEB_DIST
    elif web_dist is None:
        web_dist_path = None
    else:
        web_dist_path = web_dist  # type: ignore[assignment]

    if web_dist_path is not None and web_dist_path.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist_path), html=True), name="web")

    return app
