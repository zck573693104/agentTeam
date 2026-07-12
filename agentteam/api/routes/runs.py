"""GET/POST /api/runs 端点 + trace + approvals 列表。

SSE stream 和 approve 端点在后续 Task 中添加。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentteam.api.events import BroadcastTraceWriter, EventBus
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.models.provider import ModelProvider
from agentteam.runtime.graph import TeamCompiler
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry


class CreateRunRequest(BaseModel):
    team_name: str
    task: str


def run_to_dict(row) -> dict:
    """将 runs 表行转为 API 响应 dict：id → run_id（与 POST 响应一致）。

    公开供 dashboard 等其他路由复用，避免 id→run_id 重映射逻辑分散。
    """
    d = dict(row)
    d["run_id"] = d.pop("id")
    return d


def runs_router(
    run_manager: RunManager,
    team_store: TeamStore,
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    run_repo: RunRepo,
    audit_repo: AuditRepo,
    event_bus: EventBus,
    checkpointer=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.post("")
    def create_run(req: CreateRunRequest):
        team = team_store.get(req.team_name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{req.team_name}' not found")

        run_id = run_repo.create_run(team.name, req.task)

        trace_writer = BroadcastTraceWriter(audit_repo, event_bus)
        compiler = TeamCompiler(model_provider, tool_registry)
        try:
            graph = compiler.compile(
                team,
                checkpointer=checkpointer,
                trace_writer=trace_writer,
                audit_repo=audit_repo,
            )
        except Exception as e:
            run_repo.end_run(run_id, "failed")
            raise HTTPException(status_code=400, detail=f"Compile failed: {e}")

        config = {"configurable": {"thread_id": run_id}}
        run_manager.start_run(run_id, graph, config, req.task)
        return {"run_id": run_id}

    @router.get("")
    def list_runs():
        rows = run_repo.list_runs()
        return [run_to_dict(r) for r in rows]

    @router.get("/{run_id}")
    def get_run(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return run_to_dict(run)

    @router.get("/{run_id}/trace")
    def get_trace(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        rows = audit_repo.list_events(run_id)
        return [dict(r) for r in rows]

    @router.get("/{run_id}/approvals")
    def list_approvals(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        rows = audit_repo.list_approvals(run_id)
        return [dict(r) for r in rows]

    return router
