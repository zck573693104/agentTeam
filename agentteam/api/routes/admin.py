"""POST /api/admin/reload 端点:从 DB 重新加载内存缓存。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary


def admin_router(team_store: TeamStore, library: AgentLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.post("/reload")
    def reload_from_db():
        """从 DB 重新加载 TeamStore + AgentLibrary 到内存缓存。

        用于外部修改 SQLite 后强制刷新内存视图。
        在纯内存模式下(repo=None)无效果,返回 200 但 reloaded_count=0。
        """
        team_count = team_store.reload_from_db()
        lib_count = library.reload_from_db()
        return {"teams_reloaded": team_count, "agents_reloaded": lib_count}

    return router
