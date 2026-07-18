"""SP3 集成测试:create_app 启动后注册,重启后恢复。"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_team_persistence_across_restart(tmp_path: Path):
    """create_app 注册 team 后,新 app 同 DB 能取到。"""
    db_path = tmp_path / "test.db"

    # 第一次启动:注册 team
    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    team_payload = {
        "name": "persist_team",
        "description": "persistence test",
        "leader": {
            "name": "leader", "role": "主管",
            "system_prompt": "plan",
        },
        "workers": [
            {"name": "w1", "role": "编码", "description": "", "system_prompt": "code"},
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": ["python"],
    }
    resp = client1.post("/api/teams", json=team_payload)
    assert resp.status_code == 200

    # 第二次启动:同 DB,新 app —— team 应自动恢复
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/teams")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "persist_team" in names

    resp = client2.get("/api/teams/persist_team")
    assert resp.status_code == 200
    assert resp.json()["name"] == "persist_team"


def test_library_persistence_across_restart(tmp_path: Path):
    """create_app 注册 library agent 后,新 app 同 DB 能取到。"""
    db_path = tmp_path / "test.db"

    # 第一次启动:注册 library agent
    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    resp = client1.post("/api/library/agents", json={
        "name": "persist_coder",
        "role": "worker",
        "system_prompt": "code",
        "tools": ["read_file"],
        "max_iterations": 5,
    })
    assert resp.status_code == 200

    # 第二次启动:同 DB,新 app —— library agent 应自动恢复
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/library/agents")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert "persist_coder" in names


def test_team_delete_persists_across_restart(tmp_path: Path):
    """create_app 删除 team 后,新 app 同 DB 仍无此 team。"""
    db_path = tmp_path / "test.db"

    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    team_payload = {
        "name": "to_delete",
        "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    }
    client1.post("/api/teams", json=team_payload)
    resp = client1.delete("/api/teams/to_delete")
    assert resp.status_code == 200

    # 重启:to_delete 应不存在
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/teams/to_delete")
    assert resp.status_code == 404
