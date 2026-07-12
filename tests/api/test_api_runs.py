import time

from langchain_core.messages import AIMessage

from agentteam.runtime.nodes import Plan, PlanStep
from tests.api.conftest import make_team_json
from tests.conftest import FakeLLM, FakeModelProvider


def _wait_for_run(client, run_id, timeout=10.0):
    """轮询 GET /api/runs/{id} 直到状态非 running/pending。"""
    for _ in range(int(timeout * 10)):
        resp = client.get(f"/api/runs/{run_id}")
        if resp.json()["status"] in ("completed", "failed", "interrupted"):
            return resp.json()["status"]
        time.sleep(0.1)
    return None


def _make_provider_with_plan():
    llm = FakeLLM()
    llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    llm.set_invoke_responses([AIMessage(content="done"), AIMessage(content="ok")])
    return FakeModelProvider({"qwen-max": llm})


def test_submit_run(make_client):
    provider = _make_provider_with_plan()
    client = make_client(provider)

    # 注册团队
    client.post("/api/teams", json=make_team_json())

    # 提交任务
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "test task"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    # 等待完成
    status = _wait_for_run(client, run_id)
    assert status == "completed"


def test_submit_run_team_not_found(make_client):
    from tests.conftest import FakeLLM, FakeModelProvider

    client = make_client(FakeModelProvider({"qwen-max": FakeLLM()}))
    resp = client.post("/api/runs", json={"team_name": "nope", "task": "x"})
    assert resp.status_code == 404


def test_list_runs(make_client):
    provider = _make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    client.post("/api/runs", json={"team_name": "dev", "task": "task1"})
    client.post("/api/runs", json={"team_name": "dev", "task": "task2"})

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_run_status(make_client):
    provider = _make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "x"})
    run_id = resp.json()["run_id"]

    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id


def test_get_run_not_found(make_client):
    from tests.conftest import FakeLLM, FakeModelProvider

    client = make_client(FakeModelProvider({"qwen-max": FakeLLM()}))
    resp = client.get("/api/runs/nonexistent")
    assert resp.status_code == 404


def test_get_run_trace(make_client):
    provider = _make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "x"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    resp = client.get(f"/api/runs/{run_id}/trace")
    assert resp.status_code == 200
    events = resp.json()
    # 至少有 run_start 和 run_end
    event_types = [e["event_type"] for e in events]
    assert "run_start" in event_types
    assert "run_end" in event_types


def test_get_run_approvals(make_client):
    provider = _make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "x"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    resp = client.get(f"/api/runs/{run_id}/approvals")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_sse_replay_after_run_completes(make_client):
    """run 完成后连 SSE，应收到全部历史事件后关闭。"""
    provider = _make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "sse test"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    # 连 SSE
    resp = client.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    text = resp.text
    # 应包含 run_start 和 run_end 事件
    assert "run_start" in text
    assert "run_end" in text


def test_sse_for_interrupted_run(make_client):
    """有 step 审批的 run 中断后连 SSE，应收到 run_interrupted 事件。"""
    from agentteam.runtime.nodes import Plan, PlanStep

    llm = FakeLLM()
    llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    provider = FakeModelProvider({"qwen-max": llm})

    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "approval test"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    # 连 SSE
    resp = client.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    text = resp.text
    assert "run_interrupted" in text
