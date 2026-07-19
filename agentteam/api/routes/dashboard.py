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
        # SQL 聚合替代 Python 端遍历(原实现 list_runs() 全表返回再循环统计,
        # 大数据量下内存与 CPU 双重浪费)。三条聚合查询均走索引:
        # - by_status 走 idx_runs_status
        # - by_team 走 idx_runs_team_name
        # - total_tokens 走全表扫描但单次聚合,远优于 N 次行读
        by_status = run_repo.aggregate_by_status()
        by_team = run_repo.aggregate_by_team()
        total_tokens = run_repo.sum_total_tokens()
        total_runs = sum(by_status.values())

        # recent_runs 只需最近 10 条,用 LIMIT 10 避免全表加载
        recent_rows = run_repo.list_runs(limit=10)
        recent = [run_to_dict(r) for r in recent_rows]
        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "by_status": by_status,
            "by_team": by_team,
            "recent_runs": recent,
        }

    return router
