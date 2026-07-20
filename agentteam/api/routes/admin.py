"""POST /api/admin/reload + GET /api/admin/audit + /api/admin/quotas 端点。

P-A3 管理操作审计 + P-A4 Token 配额管理。
P-B4 PEP 策略管理 + P-B5 Skill 供应链管理(注册/可见性/ACL/撤销)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary
from agentteam.runtime.pep import PEPRepo
from agentteam.storage.admin_audit import AdminAuditRepo
from agentteam.storage.quotas import QuotaRepo
from agentteam.storage.skills_meta import SkillMetaRepo


class QuotaSetRequest(BaseModel):
    """PUT /api/admin/quotas/{team_name} 请求体。"""
    token_limit: int = Field(ge=0, description="Token 配额上限,0=不限")
    period_seconds: int = Field(default=86400, ge=1, description="统计周期(秒),默认 86400=1 天")
    description: str = Field(default="", max_length=1024, description="配额说明")
    warn_threshold: int = Field(
        default=0, ge=0,
        description="P-B7 告警阈值(0=不告警)。used >= warn_threshold 时返回 status='warned'"
    )


class PEPolicyRequest(BaseModel):
    """PUT /api/admin/pep/{name} 请求体(P-B4 PEP 策略管理)。"""
    effect: str = Field(description="'allow' 或 'deny'")
    principal: str = Field(description="主体(agent 名 / team 名 / '*' 通配)")
    action: str = Field(description="动作(如 'tool:invoke' / 'skill:load' / 'mcp:call' / '*')")
    resource: str = Field(description="资源(工具名 / skill 名 / MCP server 名 / '*')")
    condition: dict | None = Field(default=None, description="条件 JSON,后续扩展")


class SkillMetaRequest(BaseModel):
    """PUT /api/admin/skills/{name} 请求体(P-B5 Skill 供应链管理)。"""
    version: int = Field(default=1, ge=1, description="版本号")
    status: str = Field(default="published", description="状态: draft/published/deprecated/revoked")
    visibility: str = Field(default="public", description="可见性: public/private/protected")
    owner_team: str | None = Field(default=None, description="所有者 team(对 private/protected 必填)")
    description: str = Field(default="", max_length=2048, description="Skill 描述")


def admin_router(
    team_store: TeamStore,
    library: AgentLibrary,
    admin_audit: AdminAuditRepo | None = None,
    quota_repo: QuotaRepo | None = None,
    pep_repo: PEPRepo | None = None,
    skill_meta_repo: SkillMetaRepo | None = None,
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
        event_type: str | None = Query(default=None, description="按 event_type 过滤(如 team_created/quota_set)"),
        start_time: str | None = Query(
            default=None,
            description="P-B8 起始时间(ISO8601),包含。如 2026-07-01T00:00:00+00:00",
        ),
        end_time: str | None = Query(
            default=None,
            description="P-B8 截止时间(ISO8601),包含。如 2026-07-31T23:59:59+00:00",
        ),
    ):
        """查询管理操作审计事件(按时间倒序)。

        对标阿里云 AgentTeams "安全审计":支持按时间、人员、Agent、事件类型等多维度检索。
        P-B8: 新增 start_time/end_time 时间范围检索 + event_type 精确过滤。
        """
        if admin_audit is None:
            return {"events": [], "total": 0, "limit": limit, "offset": offset}
        events = admin_audit.list_events(
            limit=limit, offset=offset,
            resource=resource, actor=actor,
            event_type=event_type,
            start_time=start_time, end_time=end_time,
        )
        total = admin_audit.count_events(
            resource=resource, actor=actor,
            event_type=event_type,
            start_time=start_time, end_time=end_time,
        )
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
            warn_threshold=req.warn_threshold,
        )
        if admin_audit is not None:
            admin_audit.add_event(
                "quota_set", "quota", team_name,
                payload={
                    "token_limit": req.token_limit,
                    "period_seconds": req.period_seconds,
                    "warn_threshold": req.warn_threshold,
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

    # ---- P-B4 PEP 策略管理 ----

    @router.get("/pep")
    def list_pep_policies():
        """列出所有 PEP 策略。"""
        if pep_repo is None:
            return []
        return pep_repo.list_policies()

    @router.put("/pep/{name}")
    def upsert_pep_policy(name: str, req: PEPolicyRequest):
        """创建或更新 PEP 策略(以 name 为唯一键)。"""
        if pep_repo is None:
            raise HTTPException(status_code=503, detail="PEP service not available")
        try:
            pep_repo.upsert_policy(
                name=name,
                effect=req.effect,
                principal=req.principal,
                action=req.action,
                resource=req.resource,
                condition=req.condition,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if admin_audit is not None:
            admin_audit.add_event(
                "pep_policy_set", "pep", name,
                payload={
                    "effect": req.effect,
                    "principal": req.principal,
                    "action": req.action,
                    "resource": req.resource,
                },
            )
        return {"ok": True, "name": name}

    @router.delete("/pep/{name}")
    def delete_pep_policy(name: str):
        """删除 PEP 策略。"""
        if pep_repo is None:
            raise HTTPException(status_code=503, detail="PEP service not available")
        if not pep_repo.delete_policy(name):
            raise HTTPException(status_code=404, detail=f"PEP policy '{name}' not found")
        if admin_audit is not None:
            admin_audit.add_event("pep_policy_deleted", "pep", name)
        return {"ok": True}

    @router.post("/pep/evaluate")
    def evaluate_pep(principal: str, action: str, resource: str):
        """评估 PEP 策略(预演用,不执行实际拦截)。

        返回 (allowed, reason),便于运维验证策略配置是否生效。
        """
        if pep_repo is None:
            return {"allowed": True, "reason": "PEP not configured (allowed by default)"}
        allowed, reason = pep_repo.evaluate(principal, action, resource)
        return {"allowed": allowed, "reason": reason}

    # ---- P-B5 Skill 供应链管理 ----

    @router.get("/skills")
    def list_skill_metas(
        status: str | None = Query(default=None, description="按 status 过滤"),
        visibility: str | None = Query(default=None, description="按 visibility 过滤"),
        owner_team: str | None = Query(default=None, description="按 owner_team 过滤"),
    ):
        """列出 Skill 元数据(供应链视图)。"""
        if skill_meta_repo is None:
            return []
        return skill_meta_repo.list_skills(status=status, visibility=visibility, owner_team=owner_team)

    @router.get("/skills/{name}")
    def get_skill_meta(name: str):
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        meta = skill_meta_repo.get_skill(name)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not registered")
        return meta

    @router.put("/skills/{name}")
    def upsert_skill_meta(name: str, req: SkillMetaRequest):
        """注册或更新 Skill 元数据(版本/可见性/状态/所有者)。"""
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        try:
            skill_meta_repo.upsert_skill(
                name=name,
                version=req.version,
                status=req.status,
                visibility=req.visibility,
                owner_team=req.owner_team,
                description=req.description,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if admin_audit is not None:
            admin_audit.add_event(
                "skill_meta_set", "skill", name,
                payload={
                    "version": req.version,
                    "status": req.status,
                    "visibility": req.visibility,
                    "owner_team": req.owner_team,
                },
            )
        return {"ok": True, "name": name}

    @router.post("/skills/{name}/revoke")
    def revoke_skill(name: str):
        """紧急撤销 Skill(发现恶意内容后立即停用)。"""
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        if not skill_meta_repo.revoke_skill(name):
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not registered")
        if admin_audit is not None:
            admin_audit.add_event(
                "skill_revoked", "skill", name,
                payload={"reason": "manual revoke via admin API"},
            )
        return {"ok": True, "name": name, "status": "revoked"}

    @router.delete("/skills/{name}")
    def delete_skill_meta(name: str):
        """删除 Skill 元数据(连同 ACL)。"""
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        if not skill_meta_repo.delete_skill(name):
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not registered")
        if admin_audit is not None:
            admin_audit.add_event("skill_meta_deleted", "skill", name)
        return {"ok": True}

    # ---- Skill ACL 管理(per-consumer 授权) ----

    @router.get("/skills/{name}/acls")
    def list_skill_acls(name: str):
        """列出指定 Skill 的 ACL(per-consumer 授权列表)。"""
        if skill_meta_repo is None:
            return []
        return skill_meta_repo.list_acls(skill_name=name)

    @router.put("/skills/{name}/acls/{team_name}")
    def grant_skill_access(name: str, team_name: str):
        """授予 team 对 protected skill 的访问权限。"""
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        skill_meta_repo.grant_access(name, team_name)
        if admin_audit is not None:
            admin_audit.add_event(
                "skill_acl_granted", "skill", name,
                payload={"team_name": team_name},
            )
        return {"ok": True, "skill": name, "team": team_name}

    @router.delete("/skills/{name}/acls/{team_name}")
    def revoke_skill_access(name: str, team_name: str):
        """撤销 team 对 skill 的访问权限。"""
        if skill_meta_repo is None:
            raise HTTPException(status_code=503, detail="Skill meta service not available")
        if not skill_meta_repo.revoke_access(name, team_name):
            raise HTTPException(
                status_code=404,
                detail=f"ACL not found: skill='{name}' team='{team_name}'",
            )
        if admin_audit is not None:
            admin_audit.add_event(
                "skill_acl_revoked", "skill", name,
                payload={"team_name": team_name},
            )
        return {"ok": True}

    return router
