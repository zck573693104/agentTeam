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
from agentteam.domain.mandate import default_mandate
from agentteam.models.provider import ModelProvider
from agentteam.storage.audit import AuditRepo
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


class FollowUpMessageRequest(BaseModel):
    """SP12d 绿色通道:人对运行中/等待中 worker 的追问。"""
    target: str = Field(description="投递目标:worker | leader(必选其一,禁止广播)")
    worker_name: str | None = Field(
        default=None, max_length=128,
        description="target=worker 时必填:追问送达的 worker 名",
    )
    text: str = Field(
        min_length=1, max_length=4000,
        description="追问文本(上限 4KB,与 Trigger 任务文本同限)",
    )


def run_to_dict(row) -> dict:
    """将 runs 表行转为 API 响应 dict：id → run_id（与 POST 响应一致）。

    公开供 dashboard 等其他路由复用，避免 id→run_id 重映射逻辑分散。
    SP12 Mandate:mandate_json 解析为 mandate 对象(dict);NULL/损坏 → None。
    """
    d = dict(row)
    d["run_id"] = d.pop("id")
    raw = d.pop("mandate_json", None)
    if raw:
        try:
            d["mandate"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            d["mandate"] = None
    else:
        d["mandate"] = None
    return d


class RunSubmitError(ValueError):
    """run 提交的业务性失败（配额/底座不可用/编译失败）。

    status_code 直接映射 HTTP 状态；HTTP 路由转 HTTPException，
    Trigger 引擎等非 HTTP 调用方捕获后自行决定处置。
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def submit_run(
    *,
    team,
    task: str,
    triggered_by_user: str | None,
    team_store: TeamStore,
    run_repo: RunRepo,
    audit_repo: AuditRepo,
    event_bus: EventBus,
    run_manager: RunManager,
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    checkpointer,
    agent_library: AgentLibrary,
    skill_loader=None,
    harness_factory=None,
    quota_repo=None,
    admin_audit_repo=None,
) -> str:
    """提交一个 run（create_run 的核心逻辑，供 HTTP 路由与 Trigger 引擎复用）。

    SP12 对标 DeepSeek Super-agent 事件驱动：入站 Trigger 命中规则后
    走同一条提交流程（配额/审计/引擎选择），triggered_by_user 记
    "trigger:<规则名>" 以区分人工触发。
    业务失败统一抛 RunSubmitError（配额 429 / harness 不可用 502-503 /
    编译失败 400）；调用前 team 必须已存在且非 None。
    """
    # P-A4 Token 配额校验(对标阿里云 AgentTeams "成本可控"):
    # 启动 run 前检查当前周期已用 token,超额返回 429。
    # 无配额配置或 token_limit=0 视为不限,放行。
    if quota_repo is not None:
        check = quota_repo.check_quota(team.name)
        if not check["allowed"]:
            if admin_audit_repo is not None:
                admin_audit_repo.add_event(
                    "run_rejected_by_quota", "team", team.name,
                    actor=triggered_by_user or "api-user",
                    payload={
                        "used": check["used"],
                        "limit": check["limit"],
                        "period": check["period"],
                        "task": task[:200],
                    },
                )
            raise RunSubmitError(
                429,
                (
                    f"Token quota exceeded for team '{team.name}': "
                    f"used {check['used']} / limit {check['limit']} "
                    f"(period {check['period']}s)"
                ),
            )

    run_id = run_repo.create_run(
        team.name, task, triggered_by_user=triggered_by_user,
        mandate=default_mandate(team, task, triggered_by_user),
    )

    # P-B2 审计:记录触发者(HTTP 用户 或 trigger:<规则名>)
    if admin_audit_repo is not None and triggered_by_user:
        admin_audit_repo.add_event(
            "run_triggered", "run", run_id,
            actor=triggered_by_user,
            payload={"team_name": team.name, "task": task[:200]},
        )
    trace_writer = BroadcastTraceWriter(audit_repo, event_bus)
    config = {"configurable": {"thread_id": run_id}}

    # SP8: harness（opencode 套壳）引擎路径
    if _team_engine(team) == "opencode":
        if harness_factory is None:
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system",
                {"error": "harness engine not configured (opencode disabled)"},
            )
            event_bus.publish(run_id, {
                "id": eid, "event_type": "error", "run_id": run_id,
                "payload": {"error": "harness engine not configured"},
            })
            raise RunSubmitError(503, "harness engine not configured (opencode disabled)")
        try:
            from agentteam.harness.runner import ensure_backend
            compat = ensure_backend(
                harness_factory.client, team, harness_factory._default_model
            )
        except Exception as e:
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e)}
            )
            event_bus.publish(run_id, {
                "id": eid, "event_type": "error", "run_id": run_id,
                "payload": {"error": str(e)},
            })
            raise RunSubmitError(502, f"opencode backend unavailable: {e}")
        # 版本兼容性 warn 记入 run 审计（error 已在 ensure_backend 内抛出）
        if compat.get("level") == "warn":
            eid = audit_repo.add_event(
                run_id, "backend_warning", "system",
                {"version": compat.get("version"), "message": compat.get("message")},
            )
            event_bus.publish(run_id, {
                "id": eid, "event_type": "backend_warning", "run_id": run_id,
                "payload": {"version": compat.get("version"),
                            "message": compat.get("message")},
            })
        # 注册所有已知 Team（TeamRef 解析 parity）
        harness_factory.set_teams(team_store.list_all())
        runner = harness_factory.create(
            run_id, team, task, trace_writer, audit_repo,
            run_manager=run_manager,
        )
        run_manager.start_run(run_id, runner, config, task)
        return run_id

    import agentteam.runtime.graph as _graph  # 局部导入,避免 api→runtime 顶层循环
    compiler = _graph.TeamCompiler(
        model_provider, tool_registry, library=agent_library,
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
        raise RunSubmitError(400, f"Compile failed: {e}")

    run_manager.start_run(run_id, graph, config, task)
    return run_id


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


def _team_engine(team) -> str:
    """Team 执行引擎解析：Team.engine 显式声明优先，否则取环境默认。"""
    import os
    return getattr(team, "engine", None) or os.environ.get(
        "AGENTTEAM_DEFAULT_ENGINE", "langgraph"
    )


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
    harness_factory=None,
    harness_state_store=None,
    quota_repo: QuotaRepo | None = None,
    admin_audit_repo: AdminAuditRepo | None = None,
    pep_repo=None,
    delivery_repo=None,
    notify_outbox_repo=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])
    lib = agent_library or AgentLibrary()

    @router.post("")
    def create_run(req: CreateRunRequest, request: Request):
        team = team_store.get(req.team_name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{req.team_name}' not found")

        # P-B2 WAT 双身份:从 request.state.user 提取触发用户(对标阿里云 AgentTeams
        # "WAT 双身份机制确保操作可追溯")。未启用鉴权时 user 为 None,向后兼容。
        user = getattr(request.state, "user", None)
        triggered_by_user = user["username"] if user else None

        try:
            run_id = submit_run(
                team=team, task=req.task, triggered_by_user=triggered_by_user,
                team_store=team_store,
                run_repo=run_repo, audit_repo=audit_repo, event_bus=event_bus,
                run_manager=run_manager,
                model_provider=model_provider, tool_registry=tool_registry,
                checkpointer=checkpointer, agent_library=lib,
                skill_loader=skill_loader, harness_factory=harness_factory,
                quota_repo=quota_repo, admin_audit_repo=admin_audit_repo,
            )
        except RunSubmitError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)
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
        chain: str | None = Query(
            default=None,
            description="按链类型过滤:call(调用链)/tool(工具链)/decision(决策链)",
        ),
    ):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        # P-B3:支持按 chain 过滤(对标阿里云 AgentTeams 三链检索)
        if chain is not None:
            if chain not in ("call", "tool", "decision"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid chain '{chain}', must be one of: call/tool/decision",
                )
            rows = audit_repo.list_events_by_chain(run_id, chain)
        else:
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

    @router.get("/{run_id}/pending-approval")
    def get_pending_approval(run_id: str):
        """待审批预览(审批 diff 预览):中断 run 的 park gate + 结构化预览。

        - run 非 interrupted → {"pending": false}
        - harness 引擎有 park 快照 → gate(含截断后的完整入参) + preview
          (write_file → unified diff / 新内容全文,bash → 命令,见
          harness/approval_preview.py)
        - 无快照(langgraph interrupt 等场景) → pending=true 但 gate/preview 为
          null,前端回退到现有横幅
        """
        from agentteam.harness.approval_preview import build_approval_preview

        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if run["status"] != "interrupted":
            return {"pending": False}
        gate = None
        if harness_state_store is not None:
            snapshot = harness_state_store.load(run_id)
            if snapshot:
                gate = (snapshot.get("park") or {}).get("gate")
        return {
            "pending": True,
            "gate": gate,
            "preview": build_approval_preview(gate),
        }

    @router.get("/{run_id}/notifications")
    def list_notifications(run_id: str):
        """IM 通知回执(SP12b outbox):该 run 的通知发送状态。

        每行含 event/status(pending|sent|dropped)/attempts/last_error/时间;
        dropped = 连续失败达上限(保留 last_error 供排查),pending 会在
        OutboxWorker 重试或服务重启后续发。
        """
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if notify_outbox_repo is None:
            return []
        rows = notify_outbox_repo.list_for_run(run_id)
        return [
            {
                "id": r["id"],
                "event": r["event"],
                "status": r["status"],
                "attempts": r["attempts"],
                "last_error": r["last_error"],
                "created_at": r["created_at"],
                "sent_at": r["sent_at"],
            }
            for r in rows
        ]

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
    def approve_run(run_id: str, req: ApproveRequest, request: Request):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        # P-B2 WAT 双身份:审批决策者也记入 audit
        user = getattr(request.state, "user", None)
        decider = user["username"] if user else "api-user"

        # 原子地 claim：仅当状态仍为 interrupted 时才置为 running，
        # 避免两个并发 approve 请求都通过 check-then-act 竞态。
        if not run_repo.try_claim(run_id, "interrupted", "running"):
            current = run_repo.get_run(run_id)
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' is not interrupted (status={current['status']})",
            )

        # SP8: 引擎分支需要 Team（engine 字段 + harness rehydrate 都要）
        team = team_store.get(run["team_name"])
        try:
            if run_manager.has_graph(run_id):
                # fast path: graph 仍在内存(正常流程),直接 resume
                run_manager.resume_run(run_id, req.approved, req.reason, decider=decider)
            elif _team_engine(team) == "opencode":
                # harness 引擎重启恢复：重建 runner，invoke 时从
                # run_engine_state 表恢复编排快照（与 SqliteSaver 对等）
                if harness_factory is None:
                    raise ValueError("harness engine not configured")
                harness_factory.set_teams(team_store.list_all())
                runner = harness_factory.rehydrate(
                    run_id, team, run["task"],
                    BroadcastTraceWriter(audit_repo, event_bus), audit_repo,
                    run_manager=run_manager,
                )
                run_manager.rehydrate_and_resume(
                    run_id, runner, req.approved, req.reason, decider=decider
                )
            else:
                # lazy recompile (P0): 服务重启后 _graphs/_configs 丢失,
                # 从 team_store 取 Team 重新 compile,再 resume。
                # SqliteSaver checkpoint 已持久化 interrupt 状态,
                # 新 graph 持有原 saver 即可从 checkpoint 续跑。
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
                    approved=req.approved, reason=req.reason, decider=decider,
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

    @router.post("/{run_id}/retry-step/{step_id}")
    def retry_step(run_id: str, step_id: str, request: Request):
        """Graph Engineering P1 局部重跑:dag 模式失败后只重跑指定 step。

        文章原文:"并行分支失败时,保留已验证工件,只重跑受影响的一支"。
        实现:从 checkpoint 移除该 step 的 completed/worker_outputs,触发 dag 路由重跑。

        前置条件:
        - run 状态为 failed/interrupted(整 run 失败或部分 step 卡住)
        - run 必须有内存态 graph(否则需先 recompile)
        - run 必须是 dag 模式(sequential 模式无 step 级重跑概念)
        - step_id 必须存在于 plan 中

        状态流转:failed/interrupted → running(dag 续跑)。
        """
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        # 仅 failed / interrupted 允许 retry(防止在 running 时并发触发)
        if run["status"] not in ("failed", "interrupted"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Run '{run_id}' cannot retry-step in status: {run['status']}. "
                    f"Only failed/interrupted runs can retry."
                ),
            )

        # P-B2 WAT 双身份:retry 决策者记入 audit
        user = getattr(request.state, "user", None)
        decider = user["username"] if user else "api-user"

        # 原子地 claim:仅当状态仍为 failed/interrupted 时才置 running,
        # 避免并发 retry/approve 竞态覆盖。
        original_status = run["status"]
        if not run_repo.try_claim(run_id, original_status, "running"):
            current = run_repo.get_run(run_id)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run '{run_id}' state changed (current: "
                    f"{current['status'] if current else 'unknown'}). Retry aborted."
                ),
            )

        try:
            run_manager.retry_step(run_id, step_id, decider=decider)
        except ValueError as e:
            # 校验失败(graph 不在内存 / 非 dag 模式 / step_id 不存在):
            # 回滚到原 status,避免误伤仍可继续的 run。
            run_repo.try_claim(run_id, "running", original_status)
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e), "retry_step": step_id}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e), "retry_step": step_id},
                },
            )
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            # 其他异常(checkpoint 损坏等)→ 标 failed
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e), "retry_step": step_id}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e), "retry_step": step_id},
                },
            )
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "step_id": step_id}

    @router.post("/{run_id}/messages")
    def deliver_follow_up(run_id: str, req: FollowUpMessageRequest):
        """SP12d 绿色通道:人对运行中/等待中 worker 的追问直通(不过 leader)。

        - 消息只入不回:worker 的回答仍走正常汇报链(→ leader → 出口);
        - 归属约束:必须显式指定 worker(或 leader),禁止广播到全部 worker;
        - leader 目标只产生 follow_up_delivered 事件(leader 仅可据此 stop,
          不可代答/重新派工——通信拓扑红线);
        - 终态 run 不接受投递(不自动复活,提示重新提交)。
        """
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if run["status"] not in ("running", "interrupted"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Run '{run_id}' is {run['status']}; follow-up messages "
                    "are only accepted while running/interrupted "
                    "(submit a new run instead of resuming a finished one)"
                ),
            )
        target = (req.target or "").strip().lower()
        if target not in ("worker", "leader"):
            raise HTTPException(
                status_code=422, detail="target must be 'worker' or 'leader'",
            )
        if target == "worker" and not (req.worker_name or "").strip():
            raise HTTPException(
                status_code=422,
                detail="worker_name is required when target='worker' "
                       "(no broadcasting)",
            )
        if target == "leader" and req.worker_name:
            raise HTTPException(
                status_code=422,
                detail="worker_name must be empty when target='leader'",
            )
        depth = run_manager.deliver_follow_up(
            run_id, target, (req.worker_name or "").strip() or None, req.text,
        )
        return {"ok": True, "target": target, "queue_depth": depth}

    return router
