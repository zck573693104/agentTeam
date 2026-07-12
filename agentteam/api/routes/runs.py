"""GET/POST /api/runs 端点 + trace + approvals 列表 + SSE stream + approve。"""
from __future__ import annotations

import asyncio
import json
import queue as queue_mod

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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


class ApproveRequest(BaseModel):
    approved: bool
    reason: str | None = None


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

    @router.get("/{run_id}/stream")
    async def stream_run(run_id: str, request: Request):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        async def event_generator():
            loop = asyncio.get_running_loop()

            # 1. 先订阅 EventBus（防丢）
            q = event_bus.subscribe(run_id)
            try:
                # 2. 回放 SQLite 历史事件
                history = audit_repo.list_events(run_id)
                last_id = 0
                for row in history:
                    event_data = dict(row)
                    eid = event_data.get("id", 0)
                    last_id = max(last_id, eid)
                    yield {
                        "event": row["event_type"],
                        "data": json.dumps(event_data, default=str, ensure_ascii=False),
                    }

                # 3. 检查 run 当前状态
                run_status = run_repo.get_run(run_id)
                if run_status and run_status["status"] == "interrupted":
                    # run 已中断。run_interrupted 是纯控制信号（只推 EventBus 不写 SQLite），
                    # 若客户端在中断后才连接，需在此补发，否则客户端不知道要弹审批框。
                    yield {
                        "event": "run_interrupted",
                        "data": json.dumps(
                            {"event_type": "run_interrupted", "run_id": run_id},
                            default=str,
                            ensure_ascii=False,
                        ),
                    }
                    return
                if run_status and run_status["status"] in ("completed", "failed"):
                    return

                # 4. 直播模式：从 Queue 读事件
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await loop.run_in_executor(
                            None, lambda: q.get(timeout=2.0)
                        )
                    except queue_mod.Empty:
                        continue
                    # 去重：仅对有 id 的 SQLite 事件去重；run_interrupted 等控制信号无 id，直接放行
                    if "id" in event and event["id"] <= last_id:
                        continue
                    yield {
                        "event": event.get("event_type", "message"),
                        "data": json.dumps(event, default=str, ensure_ascii=False),
                    }
                    # run_end / error 后关闭（run_interrupted 不关闭——等审批续跑）
                    if event.get("event_type") in ("run_end", "error"):
                        break
            finally:
                event_bus.unsubscribe(run_id, q)

        return EventSourceResponse(event_generator())

    @router.post("/{run_id}/approve")
    def approve_run(run_id: str, req: ApproveRequest):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if run["status"] != "interrupted":
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' is not interrupted (status={run['status']})",
            )

        try:
            run_manager.resume_run(run_id, req.approved, req.reason)
        except ValueError as e:
            # run 在 DB 中为 interrupted 但内存中 graph/config 已丢失（如服务重启后）
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True}

    return router
