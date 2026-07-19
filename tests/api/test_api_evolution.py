"""SP7b: Evolution API 测试。"""
import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.routes.evolution import evolution_router
from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.library import AgentLibrary


def _make_app():
    """构造测试 app,mock repo + 真实 AgentLibrary。"""
    evo_repo = MagicMock()
    lib = AgentLibrary()
    app = FastAPI()
    app.include_router(evolution_router(evo_repo, lib))
    return app, evo_repo, lib


def test_list_history_returns_records():
    """GET /api/agents/{name}/history 返回 history 列表。"""
    app, evo_repo, _ = _make_app()
    evo_repo.list_history.return_value = [
        {"id": 1, "agent_name": "coder", "version": 1, "dimension": "prompt"},
    ]
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["history"]) == 1
    assert body["history"][0]["dimension"] == "prompt"


def test_list_history_empty_returns_empty_list():
    """无 history → 返回空列表。"""
    app, evo_repo, _ = _make_app()
    evo_repo.list_history.return_value = []
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/history")
    assert resp.status_code == 200
    assert resp.json() == {"history": []}


def test_get_version_returns_snapshot():
    """GET /api/agents/{name}/versions/{v} 返回 version 快照。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "prompt", "before_value": "old", "after_value": "new"},
    ]
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/versions/2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert len(body["records"]) == 1


def test_get_version_unknown_returns_404():
    """未知 version → 404。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = []
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/versions/999")
    assert resp.status_code == 404


def test_rollback_applies_before_value_and_increments_version():
    """POST /api/agents/{name}/rollback?version=N 成功回滚 + version 递增。"""
    app, evo_repo, lib = _make_app()
    # 预置 agent(ApprovalPolicy 字段已修正:level/Targets 而非 mode/tools)
    lib.register(Agent(
        name="coder", role="worker",
        system_prompt="current", max_iterations=10,
        approval_policy=ApprovalPolicy(level="worker"),
        version=5,
    ))
    # mock version 2 的 snapshot:prompt + params(ApprovalPolicy 字段已修正)
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "prompt", "before_value": "old_prompt"},
        {"dimension": "params", "before_value": json.dumps({
            "max_iterations": 3,
            "approval_policy": {"level": "tool", "targets": ["x"]},
        })},
    ]
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["new_version"] == 6  # 5 + 1
    # Agent 已被回滚
    agent = lib.get("coder")
    assert agent.system_prompt == "old_prompt"
    assert agent.max_iterations == 3
    assert agent.version == 6
    # approval_policy 也被正确回滚(Issue 5:补强断言堵住测试盲区)
    assert agent.approval_policy is not None
    assert agent.approval_policy.level == "tool"
    assert agent.approval_policy.targets == ["x"]
    # rollback 记录已写入 history
    evo_repo.add_record.assert_called_once()
    call_kwargs = evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "rollback"
    assert call_kwargs.kwargs["success"] is True


def test_rollback_unknown_version_returns_404():
    """未知 version → 404,不修改 agent。"""
    app, evo_repo, lib = _make_app()
    lib.register(Agent(name="coder", role="worker", version=1))
    evo_repo.get_version_snapshot.return_value = []
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=999")
    assert resp.status_code == 404


def test_rollback_unknown_agent_returns_404():
    """未知 agent → 404。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = [{"dimension": "prompt", "before_value": "x"}]
    with TestClient(app) as client:
        resp = client.post("/api/agents/nonexistent/rollback?version=1")
    assert resp.status_code == 404


def test_rollback_does_not_revert_skill_gen_or_select():
    """rollback 不回滚 skill_gen / skill_select(已生成的文件保留)。"""
    app, evo_repo, lib = _make_app()
    lib.register(Agent(
        name="coder", role="worker",
        system_prompt="current", skills=["auto_x"], version=3,
    ))
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "skill_gen", "before_value": "", "after_value": "/skills/auto_x.md"},
        {"dimension": "skill_select", "before_value": "[]", "after_value": '["auto_x"]'},
    ]
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=2")
    assert resp.status_code == 200
    # skills 未被回滚(保留 auto_x)
    agent = lib.get("coder")
    assert agent.skills == ["auto_x"]


def test_create_app_includes_evolution_routes(tmp_path):
    """create_app 集成后,evolution endpoint 可访问。"""
    from agentteam.api.server import create_app
    from agentteam.models.provider import ModelProvider
    from agentteam.tools.registry import ToolRegistry

    app = create_app(
        db_path=str(tmp_path / "test.db"),
        model_provider=ModelProvider(),
        tool_registry=ToolRegistry(),
        skills_dir=tmp_path,
        web_dist=None,
    )
    with TestClient(app) as client:
        # history endpoint 可访问(返回空,因无 agent history)
        resp = client.get("/api/agents/nonexistent/history")
        assert resp.status_code == 200
        assert resp.json() == {"history": []}

