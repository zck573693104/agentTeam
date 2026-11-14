"""FastAPI app 工厂(v2 内核:dsh 式 Ticket/Router/Worker Session)。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from agentteam.config import get_settings
from agentteam.logging_config import get_logger, init_logging
from agentteam.tools.registry import ToolRegistry
from agentteam.v2.api.deps import create_v2_components
from agentteam.v2.api.router import build_v2_router

logger = get_logger("api.server")

# 哨兵:web_dist 参数未显式传入时使用默认路径,区分"默认"与"显式禁用挂载"。
_DEFAULT: object = object()
_DEFAULT_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def create_app(
    db_path: str | None = None,
    model_provider=None,
    tool_registry: ToolRegistry | None = None,
    skills_dir: Path | None = None,
    web_dist: Path | None | object = _DEFAULT,
    log_level: str | None = None,
) -> FastAPI:
    """构建 v2 应用:Ticket/Attention/Thread/Source 全套 API + SSE + SPA 静态托管。"""
    settings = get_settings()
    init_logging(level=log_level or settings.log_level)
    if db_path is None:
        db_path = settings.db_path
    logger.info("starting AgentTeam v2, db_path=%s", db_path)

    if skills_dir is None:
        _skills_env = os.environ.get("AGENTTEAM_SKILLS_DIR", "")
        skills_dir = Path(_skills_env) if _skills_env else None

    v2 = create_v2_components(
        db_path=db_path,
        workspace_root=(
            Path(settings.v2_workspace_root) if settings.v2_workspace_root
            else Path(db_path).parent / "workspaces"
        ),
        model_provider=model_provider,
        tool_registry=tool_registry,
        skills_dir=skills_dir,
        default_provider=settings.v2_default_provider,
        default_model_name=settings.v2_default_model,
        max_session_workers=settings.v2_max_session_workers,
        notify_webhook_url=settings.notify_webhook_url,
        console_url=settings.console_url,
        notify_retry_max=settings.notify_retry_max,
        notify_backoff_base=settings.notify_backoff_base,
        notify_timeout_seconds=settings.notify_timeout_seconds,
        source_poll_interval=settings.v2_source_poll_interval,
    )
    # 内置工具自动发现(仅新建 registry 时;调用方传入的 registry 保持其控制权)
    if tool_registry is None:
        from agentteam.plugins import discover_all
        discover_all(v2.tool_registry)

    @asynccontextmanager
    async def lifespan(app):
        # 后台组件随服务起停:调度器 → 出站 Outbox → 入站 SourceDaemon
        v2.scheduler.start()
        v2.outbox_worker.start()
        await v2.source_daemon.start()
        yield
        await v2.source_daemon.stop()
        v2.outbox_worker.stop()
        v2.scheduler.shutdown(wait=True)
        v2.conn.close()

    app = FastAPI(title="AgentTeam", lifespan=lifespan)
    app.include_router(build_v2_router(v2), prefix="/api")

    # 前端静态托管(SPA 回退:/v2/* 等前端路由刷新直达时回退 index.html)
    if web_dist is _DEFAULT:
        web_dist_path: Path | None = _DEFAULT_WEB_DIST
    elif web_dist is None:
        web_dist_path = None
    else:
        web_dist_path = web_dist  # type: ignore[assignment]

    class _SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                looks_like_asset = "." in path.rsplit("/", 1)[-1]
                is_reserved = path.startswith(
                    ("api", "docs", "openapi.json", "redoc")
                )
                if (
                    exc.status_code == 404
                    and not looks_like_asset
                    and not is_reserved
                ):
                    return await super().get_response("index.html", scope)
                raise

    if web_dist_path is not None and web_dist_path.is_dir():
        app.mount(
            "/", _SPAStaticFiles(directory=str(web_dist_path), html=True),
            name="web",
        )

    return app
