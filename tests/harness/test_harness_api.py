"""API 层集成测试：engine="opencode" 的 Team 走完整 runs 流程（fake server）。

覆盖：注册→提交→SSE 事件流→完成；step/tool 审批 interrupt→approve→续跑；
取消；engine 字段在 Team API 的往返。
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from tests.harness.fake_opencode import FakeOpenCodeServer, Script


@pytest.fixture
def oc_api(tmp_path, monkeypatch):
    """带 fake opencode 后端的完整 app + client。

    返回 (TestClient, FakeOpenCodeServer, Script)。
    """
    created = {}

    def _make(script: Script | None = None, harness_enabled=True):
        script = script or Script()
        server = FakeOpenCodeServer(script)
        base_url = server.start()
        monkeypatch.setenv("AGENTTEAM_OPENCODE_URL", base_url)
        monkeypatch.setenv("AGENTTEAM_OC_MODEL", "opencode/test-model")
        monkeypatch.delenv("AGENTTEAM_HARNESS_DISABLED", raising=False)
        from agentteam.api.server import create_app
        from agentteam.tools.registry import ToolRegistry
        from tests.conftest import FakeLLM, FakeModelProvider
        # langgraph 路径的假 LLM（对照测试用；opencode 路径不经过它）
        llm = FakeLLM()
        from langchain_core.messages import AIMessage
        # P0 后 leader_review 也走结构化输出（ReviewVerdict）：一个 run 会依次
        # 消费 1 个 Plan + N 个 ReviewVerdict，按调用顺序备足
        llm.set_invoke_responses([AIMessage(content="done"), AIMessage(content="ok")] * 8)
        from agentteam.runtime.nodes import Plan, PlanStep, ReviewVerdict
        llm.set_structured_responses(
            [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
            + [ReviewVerdict(passed=True, reason="ok")] * 8
        )
        provider = FakeModelProvider({"qwen-max": llm})
        app = create_app(
            db_path=str(tmp_path / "h.db"),
            model_provider=provider,
            tool_registry=ToolRegistry(),
            web_dist=None,
            harness_enabled=harness_enabled,
        )
        client = TestClient(app)
        created["client"] = client
        created["server"] = server
        return client, server, script

    yield _make
    if "client" in created:
        created["server"].stop()


def register_oc_team(client, name="oc_dev", with_leader_approval=False,
                     worker_policy=None):
    team = {
        "name": name,
        "description": "套壳团队",
        "engine": "opencode",
        "root": {
            "name": "leader", "role": "supervisor",
            "system_prompt": "你是主管",
            "approval_policy": {"level": "step"} if with_leader_approval else None,
            "children": [
                {"name": "w1", "role": "worker", "system_prompt": "执行者",
                 "tools": [], "approval_policy": worker_policy},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
        "mcp_servers": [],
    }
    resp = client.post("/api/teams", json=team)
    assert resp.status_code == 200, resp.text
    return team


def wait_run(client, run_id, timeout=15.0):
    for _ in range(int(timeout * 10)):
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("completed", "failed", "interrupted", "cancelled"):
            return r
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} not settled: {r}")


def test_team_engine_roundtrip(oc_api):
    client, _, _ = oc_api()
    register_oc_team(client)
    got = client.get("/api/teams/oc_dev").json()
    assert got["engine"] == "opencode"


def test_full_run_completes(oc_api):
    client, server, script = oc_api()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    register_oc_team(client)
    resp = client.post("/api/runs", json={"team_name": "oc_dev", "task": "任务"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    run = wait_run(client, run_id)
    assert run["status"] == "completed"
    assert run["total_tokens"] > 0
    trace = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/trace").json()]
    # RunManager + 引擎完整词表
    assert trace[0] == "run_start"
    for t in ("leader_plan", "worker_start", "worker_end", "leader_review", "run_end"):
        assert t in trace


def test_step_approval_flow_via_api(oc_api):
    client, server, script = oc_api()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    register_oc_team(client, with_leader_approval=True)
    run_id = client.post(
        "/api/runs", json={"team_name": "oc_dev", "task": "任务"}
    ).json()["run_id"]
    run = wait_run(client, run_id)
    assert run["status"] == "interrupted"
    # 批准 → 续跑完成
    resp = client.post(f"/api/runs/{run_id}/approve",
                       json={"approved": True, "reason": "同意"})
    assert resp.status_code == 200
    run = wait_run(client, run_id)
    assert run["status"] == "completed"
    approvals = client.get(f"/api/runs/{run_id}/approvals").json()
    assert approvals[0]["status"] == "approved"
    assert approvals[0]["decider"] == "api-user"


def test_tool_approval_flow_via_api(oc_api):
    client, server, script = oc_api()
    script.plan = {"steps": [{"worker": "w1", "instruction": "写"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [
        {"type": "tool", "name": "write_file", "ask": True, "result": "ok"},
        {"type": "final", "text": "写完"},
    ]
    register_oc_team(
        client, worker_policy={"level": "tool", "targets": ["write_file"]}
    )
    run_id = client.post(
        "/api/runs", json={"team_name": "oc_dev", "task": "任务"}
    ).json()["run_id"]
    assert wait_run(client, run_id)["status"] == "interrupted"
    client.post(f"/api/runs/{run_id}/approve", json={"approved": True})
    run = wait_run(client, run_id)
    assert run["status"] == "completed"
    trace = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/trace").json()]
    assert "tool_call" in trace


def test_cancel_via_api(oc_api):
    client, server, script = oc_api()
    script.plan = {"steps": [{"worker": "w1", "instruction": "慢"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "sleep", "seconds": 30}]
    register_oc_team(client)
    run_id = client.post(
        "/api/runs", json={"team_name": "oc_dev", "task": "任务"}
    ).json()["run_id"]
    # 等 run 进入 running（worker 会话 sleep 中）
    for _ in range(50):
        if client.get(f"/api/runs/{run_id}").json()["status"] == "running":
            break
        time.sleep(0.1)
    resp = client.post(f"/api/runs/{run_id}/cancel")
    assert resp.status_code == 200
    run = wait_run(client, run_id)
    assert run["status"] == "cancelled"


def test_sse_stream_delivers_events(oc_api):
    client, server, script = oc_api()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    register_oc_team(client)
    run_id = client.post(
        "/api/runs", json={"team_name": "oc_dev", "task": "任务"}
    ).json()["run_id"]
    wait_run(client, run_id)
    # 完成后连接 SSE：应回放全部历史并以 run_end 关流
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
            if not line:
                continue
    types = [e.get("event_type") for e in events]
    assert types[-1] == "run_end"
    assert "worker_end" in types


def test_harness_disabled_returns_503(oc_api):
    client, _, _ = oc_api(harness_enabled=False)
    register_oc_team(client)
    resp = client.post("/api/runs", json={"team_name": "oc_dev", "task": "任务"})
    assert resp.status_code == 503


def test_langgraph_default_untouched(oc_api):
    """不带 engine 字段的团队仍走 langgraph 引擎（不触 opencode）。"""
    client, server, _ = oc_api()
    from tests.api.conftest import make_team_json
    resp = client.post("/api/teams", json=make_team_json(name="lg_team"))
    assert resp.status_code == 200
    run_id = client.post(
        "/api/runs", json={"team_name": "lg_team", "task": "任务"}
    ).json()["run_id"]
    r = wait_run(client, run_id)
    assert r["status"] == "completed"
    # fake opencode server 未被使用（没有会话创建）
    assert server.sessions == {}


def test_example_opencode_team_e2e(oc_api):
    """examples/opencode_dev_team.py 的团队定义可注册、可经套壳引擎跑完。"""
    from examples.opencode_dev_team import OPENCODE_DEV_TEAM
    client, server, script = oc_api()
    team = json.loads(json.dumps(OPENCODE_DEV_TEAM))
    team["name"] = "oc_example_e2e"  # 防重名
    resp = client.post("/api/teams", json=team)
    assert resp.status_code == 200, resp.text
    # 剧本：主管拆 1 步给 coder（skill code_review 存在于 skills/ 目录）
    script.plan = {"steps": [{"worker": "coder", "instruction": "实现 hello"}],
                   "execution_mode": "sequential"}
    script.worker_steps["coder"] = [{"type": "final", "text": "done"}]
    run_id = client.post(
        "/api/runs", json={"team_name": "oc_example_e2e", "task": "做一个小功能"}
    ).json()["run_id"]
    run = wait_run(client, run_id)
    # step 级审批在先：interrupted → 批准 → 完成（skills 注入不破坏编译期校验）
    assert run["status"] == "interrupted"
    client.post(f"/api/runs/{run_id}/approve", json={"approved": True})
    run = wait_run(client, run_id)
    assert run["status"] == "completed"
    types = [e["event_type"] for e in
             client.get(f"/api/runs/{run_id}/trace").json()]
    assert "worker_end" in types and "leader_review" in types
