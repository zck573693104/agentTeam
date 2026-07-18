"""GET/POST/DELETE /api/teams 端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.api.store import TeamStore


def teams_router(store: TeamStore) -> APIRouter:
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
        if store.get(team.name) is not None:
            raise HTTPException(
                status_code=400,
                detail=f"Team already exists: {team.name}",
            )
        store.register(team)
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

    return router
