"""GET/POST/DELETE /api/teams 端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.api.store import TeamStore
from agentteam.security.crypto import mask_secrets_in_dict
from agentteam.storage.admin_audit import AdminAuditRepo


def _team_to_masked_dict(team) -> dict:
    """序列化 Team 并对敏感字段(env/token/password)做脱敏。

    对标阿里云 AgentTeams "Agent 零接触明文":API 返回的 Team 配置中
    MCP server env、agent mcp_servers env 等敏感字段一律 mask 为 ***,
    防止前端/日志/网络抓包泄漏凭证。运行时由 TeamRepo.get 解密后注入子进程。
    """
    return mask_secrets_in_dict(team_to_dict(team))


def teams_router(store: TeamStore, admin_audit: AdminAuditRepo | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/teams", tags=["teams"])

    def _audit(event_type: str, team_name: str, payload: dict | None = None) -> None:
        if admin_audit is not None:
            admin_audit.add_event(event_type, "team", team_name, payload=payload)

    @router.get("")
    def list_teams():
        return [_team_to_masked_dict(t) for t in store.list_all()]

    @router.post("")
    def register_team(body: dict):
        try:
            team = team_from_dict(body)
        except (KeyError, TypeError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid team JSON: {e}")
        # BUG-01:用原子 register_if_absent 替代 get-then-register,
        # 避免并发 POST 同名 team 时两者都通过检查、互相覆盖。
        if not store.register_if_absent(team):
            raise HTTPException(
                status_code=400,
                detail=f"Team already exists: {team.name}",
            )
        _audit("team_created", team.name, {"description": team.description})
        return {"name": team.name}

    @router.get("/{name}")
    def get_team(name: str):
        team = store.get(name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        return _team_to_masked_dict(team)

    @router.delete("/{name}")
    def delete_team(name: str):
        if not store.delete(name):
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        _audit("team_deleted", name)
        return {"ok": True}

    @router.put("/{name}")
    def update_team(name: str, body: dict):
        if store.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        try:
            team = team_from_dict(body)
        except (KeyError, TypeError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid team JSON: {e}")
        if team.name != name:
            raise HTTPException(
                status_code=400,
                detail=f"Name in body ({team.name}) must match URL ({name})",
            )
        store.update(team)
        _audit("team_updated", team.name, {"description": team.description})
        return {"name": team.name}

    return router
