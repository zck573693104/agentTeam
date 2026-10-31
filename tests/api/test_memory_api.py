"""记忆层 + 审批预览的 API 集成测试。"""
import threading
from unittest.mock import MagicMock

from agentteam.harness.runner import HarnessStateStore
from agentteam.storage.db import init_db
from agentteam.storage.memory import MemoryRepo
from agentteam.storage.runs import RunRepo

from tests.api.conftest import (
    _wait_for_run,
    make_provider_with_plan,
    make_team_json,
)


# ---------- 团队记忆路由 ----------

def test_team_memories_routes(make_client, tmp_path):
    client = make_client(db_path=str(tmp_path / "m.db"))
    assert client.post("/api/teams", json=make_team_json(name="dev")).status_code == 200
    assert client.get("/api/teams/dev/memories").json() == []

    # 直接落一条记忆(蒸馏路径由 runtime 测试覆盖)
    conn2 = init_db(str(tmp_path / "m.db"))
    MemoryRepo(conn2).add_memory(
        "dev", "r1", "completed", "m1", {"files": ["a.py"]},
    )
    rows = client.get("/api/teams/dev/memories").json()
    assert len(rows) == 1
    assert rows[0]["summary"] == "m1"
    assert rows[0]["meta"]["files"] == ["a.py"]

    # 未知团队 404;删除校验 team 归属
    assert client.get("/api/teams/ghost/memories").status_code == 404
    mid = rows[0]["id"]
    assert client.delete(f"/api/teams/dev/memories/{mid}").json() == {"ok": True}
    assert client.delete(f"/api/teams/dev/memories/{mid}").status_code == 404
    conn2.close()


# ---------- 待审批预览端点 ----------

def _submit_run(client, tmp_path, name: str) -> str:
    """注册团队 + 提交一个能跑完的 run(预置 Plan 的 fake provider)。"""
    client.post("/api/teams", json=make_team_json(name=name))
    run_id = client.post(
        "/api/runs", json={"team_name": name, "task": "任务"}
    ).json()["run_id"]
    assert _wait_for_run(client, run_id) == "completed"
    return run_id


def test_pending_approval_not_interrupted(make_client, tmp_path):
    client = make_client(
        fake_provider=make_provider_with_plan(), db_path=str(tmp_path / "p.db")
    )
    run_id = _submit_run(client, tmp_path, "dev")
    resp = client.get(f"/api/runs/{run_id}/pending-approval")
    assert resp.status_code == 200
    assert resp.json() == {"pending": False}


def test_pending_approval_with_park_snapshot(make_client, tmp_path):
    client = make_client(
        fake_provider=make_provider_with_plan(), db_path=str(tmp_path / "p.db")
    )
    run_id = _submit_run(client, tmp_path, "dev")

    # 模拟 harness park:改状态 + 写快照(第二条连接,与生产同构)
    conn2 = init_db(str(tmp_path / "p.db"))
    RunRepo(conn2).update_status(run_id, "interrupted")
    HarnessStateStore(conn2).save(run_id, {
        "park": {"gate": {
            "gate": "tool", "worker": "w1", "permission": "bash",
            "args": '{"command": "ls"}', "input": {"command": "ls -la"},
        }},
    })
    resp = client.get(f"/api/runs/{run_id}/pending-approval")
    body = resp.json()
    assert body["pending"] is True
    assert body["gate"]["permission"] == "bash"
    assert body["preview"]["kind"] == "command"
    assert body["preview"]["command"] == "ls -la"
    conn2.close()


def test_pending_approval_interrupted_without_snapshot(make_client, tmp_path):
    """langgraph interrupt(无 park 快照)→ pending=true 但 gate/preview 为 null。"""
    client = make_client(
        fake_provider=make_provider_with_plan(), db_path=str(tmp_path / "p.db")
    )
    run_id = _submit_run(client, tmp_path, "dev")
    conn2 = init_db(str(tmp_path / "p.db"))
    RunRepo(conn2).update_status(run_id, "interrupted")
    resp = client.get(f"/api/runs/{run_id}/pending-approval")
    body = resp.json()
    assert body == {"pending": True, "gate": None, "preview": None}
    conn2.close()


def test_pending_approval_unknown_run_404(make_client, tmp_path):
    client = make_client(db_path=str(tmp_path / "p.db"))
    assert client.get("/api/runs/nonexistent/pending-approval").status_code == 404


# ---------- RunManager 记忆触发(镜像 test_evolution_trigger) ----------

def _make_run_manager(memory_engine=None):
    from agentteam.api.run_manager import RunManager

    repo = MagicMock()
    repo.end_run_if_status.return_value = True
    return RunManager(
        run_repo=repo,
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
        memory_engine=memory_engine,
    )


def test_run_manager_accepts_memory_engine_param():
    rm = _make_run_manager()
    assert rm._memory is None


def test_completed_run_triggers_memory_engine():
    import threading as _t

    mock_mem = MagicMock()
    triggered = _t.Event()
    mock_mem.trigger.side_effect = lambda *_: triggered.set()
    rm = _make_run_manager(memory_engine=mock_mem)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []
    mock_state.values = {"total_tokens": 10}
    mock_graph.get_state.return_value = mock_state
    rm._handle_invoke_result("r1", mock_graph, {})
    assert triggered.wait(timeout=2.0), "memory.trigger was not called within 2s"
    mock_mem.trigger.assert_called_once_with("r1")


def test_failed_run_triggers_memory_engine():
    import threading as _t

    mock_mem = MagicMock()
    triggered = _t.Event()
    mock_mem.trigger.side_effect = lambda *_: triggered.set()
    rm = _make_run_manager(memory_engine=mock_mem)
    rm._handle_error("r1", RuntimeError("bug"))
    assert triggered.wait(timeout=2.0)
    mock_mem.trigger.assert_called_once_with("r1")


def test_interrupted_run_does_not_trigger_memory_engine():
    mock_mem = MagicMock()
    rm = _make_run_manager(memory_engine=mock_mem)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = ["some_node"]  # interrupted
    mock_graph.get_state.return_value = mock_state
    rm._handle_invoke_result("r1", mock_graph, {})
    mock_mem.trigger.assert_not_called()


def test_build_memory_context_empty_without_engine():
    rm = _make_run_manager()
    assert rm._build_memory_context("r1") == ""
