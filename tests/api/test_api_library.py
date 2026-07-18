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


def test_update_existing_agent_via_put():
    """PUT /api/library/agents/{name} 更新已存在 agent,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    # 先创建
    client.post("/api/library/agents", json={
        "name": "coder", "role": "worker",
        "system_prompt": "v1", "tools": [], "max_iterations": 10,
    })
    # PUT 更新
    resp = client.put("/api/library/agents/coder", json={
        "name": "coder", "role": "worker",
        "system_prompt": "v2", "tools": ["read_file"], "max_iterations": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "coder"

    # 验证更新生效
    agents = client.get("/api/library/agents").json()
    coder = [a for a in agents if a["name"] == "coder"][0]
    assert coder["system_prompt"] == "v2"
    assert coder["tools"] == ["read_file"]
    assert coder["max_iterations"] == 5


def test_update_missing_agent_via_put_returns_404():
    """PUT /api/library/agents/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    resp = client.put("/api/library/agents/nonexistent", json={
        "name": "nonexistent", "role": "worker",
    })
    assert resp.status_code == 404


def test_update_agent_name_mismatch_returns_400():
    """PUT /api/library/agents/{name} body.name 与 URL 不匹配返回 400。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})
    resp = client.put("/api/library/agents/coder", json={
        "name": "different", "role": "worker",
    })
    assert resp.status_code == 400


def test_delete_existing_agent():
    """DELETE /api/library/agents/{name} 删除已存在 agent,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})
    resp = client.delete("/api/library/agents/coder")
    assert resp.status_code == 200
    # 验证已删除
    agents = client.get("/api/library/agents").json()
    assert all(a["name"] != "coder" for a in agents)


def test_delete_missing_agent_returns_404():
    """DELETE /api/library/agents/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    resp = client.delete("/api/library/agents/nonexistent")
    assert resp.status_code == 404
