"""SP6-P0 Run 可恢复性测试。

覆盖:
- has_graph: 判断 run_id 是否有内存态 graph(Task 1)
- recompile_and_resume: lazy recompile + resume(Task 2)
- approve_run lazy recompile 路径(Task 3)
- approve_run fast path 不变(Task 4)
"""
import threading
from unittest.mock import MagicMock, patch

import pytest
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
    """手动创建 app 并暴露 run_manager,便于模拟重启/注入 mock。

    与 test_api_approvals_robustness.py 中的同名 helper 同构,
    但 RunManager 注入了 checkpointer(P0 新增),使 lazy recompile 可用。
    """
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    saver.setup()
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=saver)
    provider = make_provider_with_plan()
    tr = ToolRegistry()

    app = FastAPI()
    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, provider, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    return app, run_manager, run_repo, audit_repo, event_bus, conn


# ===== Task 1: has_graph =====


def test_has_graph_returns_true_after_start_run(tmp_path):
    """start_run 后 has_graph(run_id) 应返回 True(graph 已注入内存)。"""
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)

    run_id = run_repo.create_run("test_team", "test task")

    # patch _run_in_background 为 no-op,避免后台线程 cleanup _graphs
    # (start_run 同步注入 _graphs[run_id],后台线程异步可能 cleanup)
    with patch.object(run_manager, "_run_in_background", lambda *a, **k: None):
        run_manager.start_run(
            run_id, MagicMock(name="graph"),
            {"configurable": {"thread_id": run_id}}, "test task",
        )
        assert run_manager.has_graph(run_id) is True

    # 等后台 no-op 线程结束,避免泄漏
    run_manager.wait(run_id, timeout=5)
    conn.close()


def test_has_graph_returns_false_for_unknown_run(tmp_path):
    """未启动的 run_id,has_graph 返回 False。"""
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)

    assert run_manager.has_graph("nonexistent-run-id") is False
    conn.close()


# ===== Task 2: recompile_and_resume =====


def test_recompile_and_resume_constructs_graph_and_resumes(tmp_path):
    """recompile_and_resume 调用 compiler_factory 构造 graph,注入 _graphs/_configs,然后调 resume_run。"""
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    my_saver = MagicMock(name="saver")
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=my_saver)

    run_id = run_repo.create_run("t", "task")
    team = Team(name="t", description="d", root=MagicMock(), default_model=ModelRef("qwen", "qwen-max"))

    fake_graph = MagicMock(name="graph")
    mock_compiler = MagicMock(name="compiler")
    mock_compiler.compile.return_value = fake_graph
    compiler_factory = MagicMock(return_value=mock_compiler)

    # mock resume_run 避免实际启线程(单元测试聚焦 recompile 逻辑)
    with patch.object(run_manager, "resume_run") as mock_resume:
        run_manager.recompile_and_resume(
            run_id, team, compiler_factory, approved=True, reason="ok",
        )

    # compiler_factory 被调用一次
    compiler_factory.assert_called_once()
    # compile 被调用,graph 注入内存
    mock_compiler.compile.assert_called_once()
    assert run_manager._graphs[run_id] is fake_graph
    assert run_manager._configs[run_id] == {"configurable": {"thread_id": run_id}}
    # resume_run 被调用,参数透传
    mock_resume.assert_called_once_with(run_id, True, "ok")
    conn.close()


def test_recompile_uses_correct_checkpointer(tmp_path):
    """recompile_and_resume 调用 compiler.compile 时传入 self._saver 作为 checkpointer。

    这是 lazy recompile 能从 checkpoint 续跑的关键 — 新 graph 必须持有原 saver。
    """
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    my_saver = MagicMock(name="my_saver")
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=my_saver)

    run_id = run_repo.create_run("t", "task")
    team = Team(name="t", description="d", root=MagicMock(), default_model=ModelRef("qwen", "qwen-max"))

    mock_compiler = MagicMock(name="compiler")
    mock_compiler.compile.return_value = MagicMock(name="graph")
    compiler_factory = MagicMock(return_value=mock_compiler)

    with patch.object(run_manager, "resume_run"):
        run_manager.recompile_and_resume(
            run_id, team, compiler_factory, approved=True,
        )

    # 验证 compile 调用时 checkpointer == my_saver(不是 None,不是新 saver)
    _, kwargs = mock_compiler.compile.call_args
    assert kwargs.get("checkpointer") is my_saver, (
        f"compile 应传入 self._saver 作为 checkpointer,实际: {kwargs.get('checkpointer')}"
    )
    conn.close()


# ===== Task 3: approve_run lazy recompile 路径 =====


def test_approve_after_restart_recompiles_and_resumes(tmp_path):
    """模拟服务重启(清空 _graphs/_configs/_threads),approve 应 lazy recompile + resume,run 最终完成。

    场景:
    1. 启动 run → interrupted(step 审批)
    2. 模拟重启:清空 RunManager 内存(_graphs/_configs/_threads)
    3. approve → 触发 lazy recompile(从 team_store 取 Team + 重新 compile + resume)
    4. SqliteSaver checkpoint 持久化 interrupt 状态,新 graph 能从 checkpoint 续跑
    5. run 最终 completed
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "restart recompile"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted", f"setup 失败:run 未到 interrupted(实际 {status})"

    # 模拟服务重启:清空 RunManager 内存状态
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()
    assert not run_manager.has_graph(run_id), "重启后 has_graph 应为 False"

    # approve 应触发 lazy recompile + resume,返回 200
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "ok"}
    )
    assert resp.status_code == 200, f"lazy recompile 后 approve 应成功,实际: {resp.status_code} {resp.text}"

    # run 应最终完成(checkpoint 续跑成功)
    status = _wait_for_run(client, run_id, timeout=15.0)
    assert status == "completed", (
        f"lazy recompile 后 run 应完成,实际: {status}"
    )

    conn.close()


def test_approve_after_restart_team_deleted_returns_409(tmp_path):
    """重启后 team 也被删除,approve 应返回 409 + run 标 failed(ValueError 路径)。"""
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "team deleted"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # 模拟重启 + team 被删
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()
    del_resp = client.delete("/api/teams/dev")
    assert del_resp.status_code == 200

    # approve:team 不存在 → ValueError → 409 + run failed
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 409
    assert "not found" in resp.json()["detail"].lower()

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed", (
        f"team 不存在时 run 应标 failed,实际: {run['status']}"
    )

    # 应有 error 事件
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert any(e["event_type"] == "error" for e in trace)

    conn.close()
