"""v2 路由总装(workers/tickets/stream/health)。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.v2.api.deps import V2Components


def build_v2_router(v2: V2Components) -> APIRouter:
    router = APIRouter()

    from agentteam.v2.api.workers import workers_router
    from agentteam.v2.api.tickets import tickets_router
    from agentteam.v2.api.stream import stream_router
    from agentteam.v2.api.attentions import attentions_router
    from agentteam.v2.api.threads import threads_router
    from agentteam.v2.api.sources import sources_router
    from agentteam.v2.api.org import org_router

    router.include_router(workers_router(v2))
    router.include_router(tickets_router(v2))
    router.include_router(attentions_router(v2))
    router.include_router(threads_router(v2))
    router.include_router(sources_router(v2))
    router.include_router(org_router(v2))
    router.include_router(stream_router(v2))

    @router.get("/dashboard")
    def dashboard():
        """B8 总览: 工单/预算/worker/注意件/outbox/来源 速率与状态。"""
        by_status = v2.ticket_repo.count_by_status()
        return {
            "tickets": {
                "by_status": by_status,
                "total": sum(by_status.values()),
            },
            "tokens_total": v2.event_repo.total_tokens(),
            "workers": {
                "total": len(v2.worker_repo.list_all()),
                "enabled": len(v2.worker_repo.list_all(include_disabled=False)),
            },
            "attentions_pending": len(v2.attention_repo.list_by_status("pending")),
            "outbox": v2.outbox_repo.counts(),
            "sources": {
                "total": len(v2.source_repo.list_all()),
                "enabled": len([s for s in v2.source_repo.list_all() if s["enabled"]]),
            },
            "threads": len(v2.thread_repo.list_threads(limit=500)),
            "events_recent_1h": v2.event_repo.recent_count(1.0),
        }

    @router.get("/skills")
    def list_skills():
        """可用技能(skills/ 目录 .md 文件名;worker.skills 引用)。"""
        return {"skills": v2.skill_loader.list_available()}

    @router.get("/memory")
    def list_memory():
        """P4/P5 经验记忆库:教训清单 + 入站研判校准规则(均不含正文)。"""
        if v2.memory_store is None:
            return {"enabled": False, "lessons": [], "inbound_rules": []}
        return {
            "enabled": True,
            "lessons": v2.memory_store.list_lessons(),
            "inbound_rules": v2.distiller.lines() if v2.distiller else [],
        }

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
