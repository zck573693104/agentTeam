from tests.api.conftest import (
    _wait_for_run,
    make_provider_with_plan,
    make_team_json,
)
from tests.conftest import FakeLLM, FakeModelProvider


def test_approve_resumes_interrupted_run(make_client):
    """中断的 run 被 approve 后应续跑至完成。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "approval test"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # approve
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "looks good"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # 等待完成
    status = _wait_for_run(client, run_id)
    assert status == "completed"


def test_reject_terminates_run(make_client):
    """拒绝后 run 不再继续（状态为 interrupted，不再有 pending）。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "reject test"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": False, "reason": "bad"}
    )
    assert resp.status_code == 200

    # 拒绝后 run 应最终结束（completed，因为拒绝后图走到 END）
    status = _wait_for_run(client, run_id)
    assert status == "completed"


def test_approve_non_interrupted_run_returns_400(make_client):
    """对非 interrupted 的 run approve 应返回 400。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())  # 无审批策略

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "x"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)  # 等待完成

    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 400


def test_approve_nonexistent_run_returns_404(make_client):
    client = make_client(FakeModelProvider({"qwen-max": FakeLLM()}))
    resp = client.post(
        "/api/runs/nonexistent/approve", json={"approved": True}
    )
    assert resp.status_code == 404


def test_double_approve_second_returns_400(make_client):
    """连续两次 approve 同一个 interrupted run：第一次成功（claim），第二次应 400。

    验证 try_claim 的原子条件更新防止并发双竞态。
    """
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "double approve"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # 第一次 approve — try_claim 成功，状态 interrupted → running
    resp1 = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "first"}
    )
    assert resp1.status_code == 200

    # 第二次 approve — try_claim 失败（状态已非 interrupted），应 400
    resp2 = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "second"}
    )
    assert resp2.status_code == 400
    assert "not interrupted" in resp2.json()["detail"]


def test_approve_after_graph_lost_marks_run_failed(tmp_path):
    """服务重启后 graph/config 丢失，approve 应将 run 标记为 failed 而非卡在 interrupted。"""
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

    # 手动创建组件以获取 run_manager 引用
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
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "restart sim"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # 模拟服务重启：清空 RunManager 内存状态
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()

    # approve 应返回 409，且 run 应标记为 failed（终态，不卡在 interrupted）
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 409

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"

    # 应有 error 事件记录失败原因
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert any(e["event_type"] == "error" for e in trace)
