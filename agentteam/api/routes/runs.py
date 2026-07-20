"""GET/POST /api/runs 端点 + trace + approvals 列表 + SSE stream + approve。"""
from __future__ import annotations

import asyncio
import json
import queue as queue_mod
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agentteam.api.events import BroadcastTraceWriter, EventBus
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelProvider
from agentteam.storage.admin_audit import AdminAuditRepo
from agentteam.storage.audit import AuditRepo
from agentteam.storage.quotas import QuotaRepo
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agentteam.runtime.graph import TeamCompiler


class CreateRunRequest(BaseModel):
    team_name: str = Field(max_length=128, description="团队名")
    task: str = Field(max_length=16384, description="任务描述(上限 16KB 防止 prompt 滥用)")


class ApproveRequest(BaseModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=2048, description="审批理由")


def run_to_dict(row) -> dict:
    """将 runs 表行转为 API 响应 dict：id → run_id（与 POST 响应一致）。

    公开供 dashboard 等其他路由复用，避免 id→run_id 重映射逻辑分散。
    """
    d = dict(row)
    d["run_id"] = d.pop("id")
    return d


def _build_compiler(
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    library: AgentLibrary,
    team_store: TeamStore,
    skill_loader=None,
) -> TeamCompiler:
    """构造 TeamCompiler 并注册所有已知 Team(供 TeamRef 解析)。

    抽成模块级 helper 供 approve_run 的 lazy recompile 路径调用,
    与 create_run 中的编译逻辑保持一致,避免循环依赖
    (RunManager 不直接依赖 ModelProvider/ToolRegistry)。
    """
    import agentteam.runtime.graph as _graph  # 局部导入,避免 api→runtime 顶层循环
    compiler = _graph.TeamCompiler(
        model_provider, tool_registry, library=library, skill_loader=skill_loader,
    )
    for t in team_store.list_all():
        compiler.register_team(t)
    return compiler


def runs_router(
    run_manager: RunManager,
    team_store: TeamStore,
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    run_repo: RunRepo,
    audit_repo: AuditRepo,
    event_bus: EventBus,
    checkpointer=None,
    agent_library: AgentLibrary | None = None,
    skill_loader=None,
    quota_repo: QuotaRepo | None = None,
    admin_audit_repo: AdminAuditRepo | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])
    lib = agent_library or AgentLibrary()

    @router.post("")
    def create_run(req: CreateRunRequest):
        team = team_store.get(req.team_name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{req.team_name}' not found")

        # P-A4 Token 配额校验(对标阿里云 AgentTeams "成本可控"):
        # 启动 run 前检查当前周期已用 token,超额返回 429。
        # 无配额配置或 token_limit=0 视为不限,放行。
        if quota_repo is not None:
            check = quota_repo.check_quota(team.name)
            if not check["allowed"]:
                if admin_audit_repo is not None:
                    admin_audit_repo.add_event(
                        "run_rejected_by_quota", "team", team.name,
                        payload={
                            "used": check["used"],
                            "limit": check["limit"],
                            "period": check["period"],
                            "task": req.task[:200],
                        },
                    )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Token quota exceeded for team '{team.name}': "
                        f"used {check['used']} / limit {check['limit']} "
                        f"(period {check['period']}s)"
                    ),
                )

        run_id = run_repo.create_run(team.name, req.task)

        trace_writer = BroadcastTraceWriter(audit_repo, event_bus)
        import agentteam.runtime.graph as _graph  # 局部导入,避免 api→runtime 顶层循环
        compiler = _graph.TeamCompiler(
            model_provider, tool_registry, library=lib,
            run_manager=run_manager, skill_loader=skill_loader,
        )
        # 注册所有已知 Team 到 compiler._team_registry，使 TeamRef 可解析
        for t in team_store.list_all():
            compiler.register_team(t)
        try:
            graph = compiler.compile(
                team,
                checkpointer=checkpointer,
                trace_writer=trace_writer,
                audit_repo=audit_repo,
            )
        except Exception as e:
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e)}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e)},
                },
            )
            raise HTTPException(status_code=400, detail=f"Compile failed: {e}")

        config = {"configurable": {"thread_id": run_id}}
        run_manager.start_run(run_id, graph, config, req.task)
        return {"run_id": run_id}

    @router.get("")
    def list_runs(
        limit: int | None = Query(default=50, ge=1, le=500, description="每页数量"),
        offset: int = Query(default=0, ge=0, description="偏移量"),
    ):
        rows = run_repo.list_runs(limit=limit, offset=offset)
        return [run_to_dict(r) for r in rows]

    @router.get("/{run_id}")
    def get_run(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return run_to_dict(run)

    @router.get("/{run_id}/trace")
    def get_trace(
        run_id: str,
        limit: int | None = Query(default=None, ge=1, le=5000, description="事件数量上限"),
        offset: int = Query(default=0, ge=0, description="偏移量"),
    ):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        rows = audit_repo.list_events(run_id, limit=limit, offset=offset)
        return [dict(r) for r in rows]

    @router.get("/{run_id}/approvals")
    def list_approvals(
        run_id: str,
        limit: int | None = Query(default=None, ge=1, le=500, description="审批记录数量上限"),
        offset: int = Query(default=0, ge=0, description="偏移量"),
    ):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        rows = audit_repo.list_approvals(run_id, limit=limit, offset=offset)
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
                # 用游标增量读取替代全表 list_events + Python 端去重
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
                    # 增量补发:仅读 id > last_id 的事件(走 idx_run_events_run_id_id 索引),
                    # 替代原全表 list_events 重读 + Python 端过滤
                    for row in audit_repo.list_events_after(run_id, last_id):
                        event_data = dict(row)
                        eid = event_data.get("id", 0)
                        if eid > last_id:
                            last_id = eid
                            yield {
                                "event": row["event_type"],
                                "data": json.dumps(event_data, default=str, ensure_ascii=False),
                            }
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
                if run_status and run_status["status"] in ("completed", "failed", "cancelled"):
                    # 同样增量补发 id > last_id 的新事件
                    for row in audit_repo.list_events_after(run_id, last_id):
                        event_data = dict(row)
                        eid = event_data.get("id", 0)
                        if eid > last_id:
                            last_id = eid
                            yield {
                                "event": row["event_type"],
                                "data": json.dumps(event_data, default=str, ensure_ascii=False),
                            }
                    return

                # 4. 直播模式：从 Queue 读事件
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        # BUG-08 修复：timeout 缩短到 0.5s。
                        # 原值 2.0s 会让客户端断开后最坏延迟 2s 才检测到，
                        # 期间 threadpool 线程被阻塞，>40 个并发 SSE 客户端会耗尽 threadpool。
                        # 0.5s 平衡了 disconnect 检测频率与 idle 时的 CPU 开销。
                        event = await loop.run_in_executor(
                            None, lambda: q.get(timeout=0.5)
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
                    # run_end / error / run_cancelled 后关闭（run_interrupted 不关闭——等审批续跑）
                    if event.get("event_type") in ("run_end", "error", "run_cancelled"):
                        break
            finally:
                event_bus.unsubscribe(run_id, q)

        return EventSourceResponse(event_generator())

    @router.post("/{run_id}/approve")
    def approve_run(run_id: str, req: ApproveRequest):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        # 原子地 claim：仅当状态仍为 interrupted 时才置为 running，
        # 避免两个并发 approve 请求都通过 check-then-act 竞态。
        if not run_repo.try_claim(run_id, "interrupted", "running"):
            current = run_repo.get_run(run_id)
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' is not interrupted (status={current['status']})",
            )

        try:
            if run_manager.has_graph(run_id):
                # fast path: graph 仍在内存(正常流程),直接 resume
                run_manager.resume_run(run_id, req.approved, req.reason)
            else:
                # lazy recompile (P0): 服务重启后 _graphs/_configs 丢失,
                # 从 team_store 取 Team 重新 compile,再 resume。
                # SqliteSaver checkpoint 已持久化 interrupt 状态,
                # 新 graph 持有原 saver 即可从 checkpoint 续跑。
                team = team_store.get(run["team_name"])
                if team is None:
                    raise ValueError(
                        f"Team '{run['team_name']}' not found (needed for recompile)"
                    )
                run_manager.recompile_and_resume(
                    run_id, team,
                    compiler_factory=lambda: _build_compiler(
                        model_provider, tool_registry, lib, team_store,
                        skill_loader=skill_loader,
                    ),
                    approved=req.approved, reason=req.reason,
                )
        except Exception as e:
            # BUG-10 修复(沿用):catch Exception 确保任何 resume/recompile 异常
            # 都回滚状态为 failed,避免 try_claim 已置 running 后无线程执行导致卡死。
            # ValueError(team 不存在等预期错误)返回 409;
            # 其他异常(锁竞争、compile 失败等)返回 500。
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e)}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e)},
                },
            )
            status_code = 409 if isinstance(e, ValueError) else 500
            raise HTTPException(status_code=status_code, detail=str(e))
        return {"ok": True}

    @router.post("/{run_id}/cancel")
    def cancel_run(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if run["status"] not in ("running", "interrupted"):
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' cannot be cancelled in status: {run['status']}",
            )
        if not run_manager.cancel_run(run_id):
            # cancel_run 返回 False:run 不在可取消状态(并发竞态:已被其他请求取消/结束)
            # 重新读取状态便于诊断(409 detail 含当前 status,与 approve_run 风格一致)
            current = run_repo.get_run(run_id)
            current_status = current["status"] if current else "unknown"
            raise HTTPException(
                status_code=409,
                detail=f"Run '{run_id}' not active or already cancelled (current status: {current_status})",
            )
        return {"ok": True}

    return router
