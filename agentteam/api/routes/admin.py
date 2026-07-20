"""POST /api/admin/reload + GET /api/admin/audit + /api/admin/quotas 端点。

P-A3 管理操作审计 + P-A4 Token 配额管理。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary
from agentteam.storage.admin_audit import AdminAuditRepo
from agentteam.storage.quotas import QuotaRepo


class QuotaSetRequest(BaseModel):
    """PUT /api/admin/quotas/{team_name} 请求体。"""
    token_limit: int = Field(ge=0, description="Token 配额上限,0=不限")
    period_seconds: int = Field(default=86400, ge=1, description="统计周期(秒),默认 86400=1 天")
    description: str = Field(default="", max_length=1024, description="配额说明")


def admin_router(
    team_store: TeamStore,
    library: AgentLibrary,
    admin_audit: AdminAuditRepo | None = None,
    quota_repo: QuotaRepo | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.post("/reload")
    def reload_from_db():
        """从 DB 重新加载 TeamStore + AgentLibrary 到内存缓存。

        用于外部修改 SQLite 后强制刷新内存视图。
        在纯内存模式下(repo=None)无效果,返回 200 但 reloaded_count=0。
        """
        team_count = team_store.reload_from_db()
        lib_count = library.reload_from_db()
        if admin_audit is not None:
            admin_audit.add_event(
                "cache_reloaded", "system", None,
                payload={"teams_reloaded": team_count, "agents_reloaded": lib_count},
            )
        return {"teams_reloaded": team_count, "agents_reloaded": lib_count}

    # ---- P-A3 管理操作审计查询 ----

    @router.get("/audit")
    def list_admin_events(
        limit: int = Query(default=100, ge=1, le=1000, description="每页数量"),
        offset: int = Query(default=0, ge=0, description="偏移量"),
        resource: str | None = Query(default=None, description="按 resource 过滤(team/library_agent/agent/system)"),
        actor: str | None = Query(default=None, description="按 actor 过滤"),
    ):
        """查询管理操作审计事件(按时间倒序)。

        对标阿里云 AgentTeams "安全审计":支持按时间、人员、Agent 等多维度检索。
        """
        if admin_audit is None:
            return {"events": [], "total": 0, "limit": limit, "offset": offset}
        events = admin_audit.list_events(limit=limit, offset=offset, resource=resource, actor=actor)
        total = admin_audit.count_events(resource=resource, actor=actor)
        return {
            "events": [dict(e) for e in events],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ---- P-A4 Token 配额管理 ----

    @router.get("/quotas")
    def list_quotas():
        """列出所有 Team 的配额配置。"""
        if quota_repo is None:
            return []
        return quota_repo.list_all()

    @router.get("/quotas/{team_name}")
    def get_quota(team_name: str):
        """查询单个 Team 的配额 + 当前周期已用量。

        返回:
        - token_limit / period_seconds / description:配置
        - used:当前周期窗口内已用 token
        - allowed:是否可启动新 run
        """
        if quota_repo is None:
            raise HTTPException(status_code=503, detail="Quota service not available")
        check = quota_repo.check_quota(team_name)
        return check

    @router.put("/quotas/{team_name}")
    def set_quota(team_name: str, req: QuotaSetRequest):
        """设置/更新 Team 配额。"""
        if quota_repo is None:
            raise HTTPException(status_code=503, detail="Quota service not available")
        quota_repo.upsert(
            team_name=team_name,
            token_limit=req.token_limit,
            period_seconds=req.period_seconds,
            description=req.description,
        )
        if admin_audit is not None:
            admin_audit.add_event(
                "quota_set", "quota", team_name,
                payload={
                    "token_limit": req.token_limit,
                    "period_seconds": req.period_seconds,
                },
            )
        return {"ok": True, "team_name": team_name}

    @router.delete("/quotas/{team_name}")
    def delete_quota(team_name: str):
        """删除 Team 配额(等价于不限)。"""
        if quota_repo is None:
            raise HTTPException(status_code=503, detail="Quota service not available")
        if not quota_repo.delete(team_name):
            raise HTTPException(status_code=404, detail=f"Quota for team '{team_name}' not found")
        if admin_audit is not None:
            admin_audit.add_event("quota_deleted", "quota", team_name)
        return {"ok": True}

    return router
