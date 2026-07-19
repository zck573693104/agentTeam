"""FastAPI app 工厂。"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver
from starlette.staticfiles import StaticFiles

from agentteam.api.events import EventBus
from agentteam.api.routes.admin import admin_router
from agentteam.api.routes.dashboard import dashboard_router
from agentteam.api.routes.evolution import evolution_router
from agentteam.api.routes.library import library_router
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.skills import skills_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.config import get_settings
from agentteam.domain.library import AgentLibrary
from agentteam.logging_config import get_logger, init_logging
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.runtime.evolution import EvolutionEngine
from agentteam.runtime.skills import SkillLoader
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.evolution import EvolutionRepo
from agentteam.storage.library import LibraryRepo
from agentteam.storage.runs import RunRepo
from agentteam.storage.teams import TeamRepo
from agentteam.tools.registry import ToolRegistry

logger = get_logger("api.server")

# 哨兵：web_dist 参数未显式传入时使用此值,区分"用默认路径"和"显式禁用挂载"。
_DEFAULT: object = object()

# 默认前端静态文件目录(生产构建产物)。计算一次,避免每次 create_app 都 resolve。
_DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def create_app(
    db_path: str | None = None,
    model_provider: ModelProvider | None = None,
    tool_registry: ToolRegistry | None = None,
    agent_library: AgentLibrary | None = None,
    skills_dir: Path | None = None,
    web_dist: Path | None | object = _DEFAULT,
    log_level: str | None = None,
) -> FastAPI:
    # 从集中式 Settings 读取配置(env_prefix=AGENTTEAM_ 自动覆盖)
    settings = get_settings()
    # 初始化 logging:显式 log_level 优先,否则用 Settings.log_level
    init_logging(level=log_level or settings.log_level)
    # 解析 DB 路径:显式参数优先,否则用 Settings.db_path
    if db_path is None:
        db_path = settings.db_path
    logger.info("starting AgentTeam, db_path=%s", db_path)
    conn = init_db(db_path)

    # BUG-07:用 lifespan 在 app shutdown 时显式 close conn,
    # 避免 Windows 上 SQLite 文件锁残留(原实现 conn 仅在 app GC 时释放)。
    # 注意:只有 `with TestClient(app):` 或真实 uvicorn 启停才触发 lifespan;
    # 直接 TestClient(app) 不触发(与原行为一致,不影响现有测试)。
    #
    # 同时调用 run_manager.shutdown():优雅关闭后台线程池,
    # 确保正在执行的 run/evolution 任务有机会完成,避免 daemon 线程被强杀
    # 导致 audit event 丢失或 SQLite 写入半截。
    @asynccontextmanager
    async def lifespan(app):
        yield
        run_manager.shutdown(wait=True)
        conn.close()

    app = FastAPI(title="AgentTeam", lifespan=lifespan)

    # 共享锁：SqliteSaver / RunRepo / AuditRepo 共用同一 sqlite3.Connection，
    # 必须用同一把锁串行化所有连接访问，否则多线程下会触发
    # sqlite3.InterfaceError: bad parameter or other API misuse。
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_repo = TeamRepo(conn, lock=conn_lock)
    library_repo = LibraryRepo(conn, lock=conn_lock)
    team_store = TeamStore(repo=team_repo)
    event_bus = EventBus()

    saver = SqliteSaver(conn)
    saver.lock = conn_lock  # 让 SqliteSaver 也用同一把锁
    # 防御:若 langgraph 改名 lock 属性则 saver.lock 不会指向 conn_lock,
    # 此时 SqliteSaver 会用自己内部的锁,与其他 repo 不互斥,多线程下触发
    # sqlite3.InterfaceError。用显式 raise 而非 assert(避免 python -O 被剥离)。
    if saver.lock is not conn_lock:
        raise RuntimeError(
            "SqliteSaver.lock does not share conn_lock; "
            "langgraph API may have changed."
        )
    saver.setup()

    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary(repo=library_repo)
    skill_loader = SkillLoader(skills_dir)

    # 插件自动发现:注册内置工具 + entry_points 声明的第三方工具/preset/skill。
    # 修复 incidental bug:此前 create_app 不调 register_builtin_skills,
    # 导致 read_file/search_web 等内置工具在 API 服务端未注册,agent.tools
    # 引用它们时运行时 KeyError。仅在新建 registry 时发现;调用方传入预置
    # registry(如测试 mock)时跳过自动发现,保持调用方控制。
    if tool_registry is None:
        from agentteam.plugins import discover_all
        discover_all(tr)

    evolution_repo = EvolutionRepo(conn, lock=conn_lock)
    evolution_engine = EvolutionEngine(
        model_provider=mp,
        agent_library=lib,
        evolution_repo=evolution_repo,
        run_repo=run_repo,
        audit_repo=audit_repo,
        default_model=ModelRef("qwen", "qwen-max"),
        skill_loader=skill_loader,
        skills_dir=skills_dir,
    )

    run_manager = RunManager(
        run_repo, audit_repo, event_bus,
        checkpointer=saver, evolution_engine=evolution_engine,
    )

    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, mp, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver, agent_library=lib, skill_loader=skill_loader,
        )
    )
    app.include_router(dashboard_router(run_repo, audit_repo))
    app.include_router(library_router(lib))
    app.include_router(admin_router(team_store, lib))
    app.include_router(skills_router(skill_loader))
    app.include_router(evolution_router(evolution_repo, lib))

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
