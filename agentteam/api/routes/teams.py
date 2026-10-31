"""GET/POST/DELETE /api/teams 端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.api.store import TeamStore


def teams_router(store: TeamStore, memory_repo=None) -> APIRouter:
    router = APIRouter(prefix="/api/teams", tags=["teams"])

    @router.get("")
    def list_teams():
        return [team_to_dict(t) for t in store.list_all()]

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
        return {"name": team.name}

    @router.get("/{name}")
    def get_team(name: str):
        team = store.get(name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        return team_to_dict(team)

    @router.delete("/{name}")
    def delete_team(name: str):
        if not store.delete(name):
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
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
        return {"name": team.name}

    # —— 记忆层: 团队跨 run 记忆的查看与清理 ——

    @router.get("/{name}/memories")
    def list_memories(name: str, limit: int = 20):
        if store.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        if memory_repo is None:
            return []
        return memory_repo.list_for_team(name, limit=max(1, min(limit, 200)))

    @router.delete("/{name}/memories/{memory_id}")
    def delete_memory(name: str, memory_id: int):
        if memory_repo is None:
            raise HTTPException(status_code=404, detail="Memory layer not configured")
        if not memory_repo.delete_memory(name, memory_id):
            raise HTTPException(
                status_code=404, detail=f"Memory {memory_id} not found for team '{name}'"
            )
        return {"ok": True}

    return router
