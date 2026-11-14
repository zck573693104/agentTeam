"""v2 路由总装(workers/tickets/stream/health)。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.v2.api.deps import V2Components


def build_v2_router(v2: V2Components) -> APIRouter:
    router = APIRouter()

    from agentteam.v2.api.workers import workers_router
    from agentteam.v2.api.tickets import tickets_router
    from agentteam.v2.api.stream import stream_router

    router.include_router(workers_router(v2))
    router.include_router(tickets_router(v2))
    router.include_router(stream_router(v2))

    @router.get("/health")
    def health():
        return {
            "ok": True,
            "workers": len(v2.worker_repo.list_all()),
            "default_model": {
                "provider": v2.default_model.provider, "name": v2.default_model.name,
            },
        }

    return router
