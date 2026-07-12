"""GET /api/dashboard 端点。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.api.routes.runs import run_to_dict
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo


def dashboard_router(run_repo: RunRepo, audit_repo: AuditRepo) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["dashboard"])

    @router.get("/dashboard")
    def get_dashboard():
        all_runs = run_repo.list_runs()
        by_status: dict[str, int] = {}
        by_team: dict[str, int] = {}
        total_tokens = 0
        for run in all_runs:
            s = run["status"]
            by_status[s] = by_status.get(s, 0) + 1
            t = run["team_name"]
            by_team[t] = by_team.get(t, 0) + 1
            total_tokens += run["total_tokens"] or 0

        recent = [run_to_dict(r) for r in all_runs[:10]]
        return {
            "total_runs": len(all_runs),
            "total_tokens": total_tokens,
            "by_status": by_status,
            "by_team": by_team,
            "recent_runs": recent,
        }

    return router
