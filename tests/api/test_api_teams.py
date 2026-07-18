from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.routes.teams import teams_router
from agentteam.api.store import TeamStore


def _make_app():
    app = FastAPI()
    store = TeamStore()
    app.include_router(teams_router(store))
    return app


def _team_json(name="dev"):
    return {
        "name": name,
        "description": "研发小队",
        "leader": {
            "name": "leader",
            "role": "主管",
            "system_prompt": "你是主管",
            "model": {"provider": "qwen", "name": "qwen-max"},
            "approval_policy": None,
        },
        "workers": [
            {
                "name": "coder",
                "role": "代码工程师",
                "description": "写代码",
                "system_prompt": "你是代码工程师",
                "model": None,
                "tools": ["read_file"],
                "approval_policy": None,
                "max_iterations": 10,
            }
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
        "mcp_servers": [],
    }


def test_register_team():
    client = TestClient(_make_app())
    resp = client.post("/api/teams", json=_team_json())
    assert resp.status_code == 200
    assert resp.json() == {"name": "dev"}


def test_list_teams():
    app = _make_app()
    client = TestClient(app)
    client.post("/api/teams", json=_team_json("a"))
    client.post("/api/teams", json=_team_json("b"))
    resp = client.get("/api/teams")
    assert resp.status_code == 200
    names = sorted(t["name"] for t in resp.json())
    assert names == ["a", "b"]


def test_get_team():
    app = _make_app()
    client = TestClient(app)
    client.post("/api/teams", json=_team_json())
    resp = client.get("/api/teams/dev")
    assert resp.status_code == 200
    assert resp.json()["name"] == "dev"
    # 新 schema：to_dict 输出 root（Agent 树），原 worker 在 root.children 中
    assert resp.json()["root"]["children"][0]["name"] == "coder"


def test_get_team_not_found():
    client = TestClient(_make_app())
    resp = client.get("/api/teams/nope")
    assert resp.status_code == 404


def test_delete_team():
    app = _make_app()
    client = TestClient(app)
    client.post("/api/teams", json=_team_json())
    resp = client.delete("/api/teams/dev")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # 删除后再获取应 404
    assert client.get("/api/teams/dev").status_code == 404


def test_delete_team_not_found():
    client = TestClient(_make_app())
    resp = client.delete("/api/teams/nope")
    assert resp.status_code == 404


def test_update_existing_team_via_put():
    """PUT /api/teams/{name} 更新已存在 team,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    # 先创建
    team_payload = {
        "name": "dev", "description": "v1",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    }
    client.post("/api/teams", json=team_payload)

    # PUT 更新
    updated_payload = dict(team_payload)
    updated_payload["description"] = "v2"
    resp = client.put("/api/teams/dev", json=updated_payload)
    assert resp.status_code == 200
    assert resp.json()["name"] == "dev"

    # 验证更新生效
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "v2"


def test_update_missing_team_via_put_returns_404():
    """PUT /api/teams/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    resp = client.put("/api/teams/nonexistent", json={
        "name": "nonexistent", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    assert resp.status_code == 404


def test_update_team_name_mismatch_returns_400():
    """PUT /api/teams/{name} body.name 与 URL name 不匹配返回 400。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    # 先创建 dev
    client.post("/api/teams", json={
        "name": "dev", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    # PUT 时 body.name 与 URL 不匹配
    resp = client.put("/api/teams/dev", json={
        "name": "different_name", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    assert resp.status_code == 400
