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
