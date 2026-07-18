"""BUG-10: approve_run 对非 ValueError 异常不处理，run 卡死。

原实现：
    try:
        run_manager.resume_run(...)
    except ValueError as e:
        run_repo.end_run(run_id, "failed")
        ...
        raise HTTPException(status_code=409, ...)

若 resume_run 内部抛非 ValueError（如 sqlite3.OperationalError 锁竞争、
RuntimeError），except ValueError 不捕获，异常传播到 FastAPI 返回 500。
但此时 try_claim 已把状态置为 running，无后台线程执行，run 永久卡死
（用户无法重新 approve，try_claim 因状态非 interrupted 失败）。

修复后：catch Exception，确保任何 resume_run 异常都回滚状态为 failed。
"""
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _build_app_with_run_manager(tmp_path):
    """手动创建 app 并暴露 run_manager，便于 monkey-patch resume_run。"""
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    provider = make_provider_with_plan()
    tr = ToolRegistry()
    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    saver.setup()

    app = FastAPI()
    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, provider, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    return app, run_manager, run_repo, audit_repo, event_bus, conn


def _create_interrupted_run(client):
    """注册带 step 审批的 team 并启动 run，等到 interrupted。"""
    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "robust test"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted", f"setup 失败：run 未到 interrupted（实际 {status}）"
    return run_id


def test_approve_run_non_value_error_rolls_back_to_failed(tmp_path):
    """resume_run 抛 RuntimeError 时，run 应标记 failed 而非卡在 running。

    场景：try_claim 已把状态置 running，但 resume_run 内部抛非 ValueError
    （模拟 SQLite 锁竞争 → RuntimeError）。原实现 except ValueError 不捕获，
    异常 500 返回，状态卡 running 永久死锁。修复后应回滚为 failed。
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)
    run_id = _create_interrupted_run(client)

    # Monkey-patch：让 resume_run 抛 RuntimeError（非 ValueError）
    original = run_manager.resume_run

    def boom(*args, **kwargs):
        raise RuntimeError("sqlite3.OperationalError: database is locked")

    run_manager.resume_run = boom  # type: ignore[assignment]

    try:
        resp = client.post(
            f"/api/runs/{run_id}/approve", json={"approved": True}
        )
    finally:
        run_manager.resume_run = original  # type: ignore[assignment]

    # 应返回错误（500 或 409 均可接受，关键是回滚状态）
    assert resp.status_code in (400, 409, 500, 503), (
        f"应返回错误码，实际: {resp.status_code}"
    )

    # 核心断言：run 必须为 failed（而非卡在 running）
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed", (
        f"非 ValueError 异常应回滚状态为 failed，实际: {run['status']}"
    )

    # 应有 error 事件记录失败原因
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    error_events = [e for e in trace if e["event_type"] == "error"]
    assert error_events, "应有 error 事件记录失败原因"
    assert "database is locked" in error_events[-1].get("payload", "{}") or \
           "database is locked" in str(error_events[-1])

    conn.close()


def test_approve_run_value_error_still_returns_409(tmp_path):
    """回归保障：ValueError（team 不存在导致 recompile 失败）仍按原逻辑返回 409 + 标记 failed。

    P0 后,清空 _graphs 单独不再触发 ValueError —— lazy recompile 会从 team_store
    取 Team 重新 compile。要触发 ValueError,需同时让 team 也不存在(team_store.get
    返回 None → approve_run 显式 raise ValueError)。这模拟"重启后 team 被删除"场景。
    修复时不能破坏 ValueError 的现有行为(409 + failed + error 事件)。
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)
    run_id = _create_interrupted_run(client)

    # 模拟 graph/config 丢失(RunManager 内存被清空)+ team 被删除
    # (P0 后清空 _graphs 单独触发 lazy recompile,需 team 也不存在才会抛 ValueError)
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()
    del_resp = client.delete("/api/teams/dev")
    assert del_resp.status_code == 200

    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 409

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"

    conn.close()
