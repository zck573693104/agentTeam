"""Library API 端点测试。"""
from fastapi.testclient import TestClient

from agentteam.api.server import create_app
from agentteam.domain.library import AgentLibrary


def test_register_and_list_agents():
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    # 注册一个 agent
    resp = client.post("/api/library/agents", json={
        "name": "coder", "role": "worker",
        "system_prompt": "code", "tools": ["read_file"], "max_iterations": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "coder"

    # 列表
    resp = client.get("/api/library/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert agents[0]["name"] == "coder"


def test_register_duplicate_agent_400():
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    client.post("/api/library/agents", json={"name": "x", "role": "worker"})
    resp = client.post("/api/library/agents", json={"name": "x", "role": "worker"})
    assert resp.status_code == 400
