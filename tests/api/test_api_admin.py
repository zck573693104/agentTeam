"""SP4 admin reload 端点测试。"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_reload_returns_counts(tmp_path: Path):
    """POST /api/admin/reload 返回重载数量。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 先注册一个 team 和 library agent
    client.post("/api/teams", json={
        "name": "dev", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})

    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["teams_reloaded"] == 1
    assert data["agents_reloaded"] == 1


def test_reload_picks_up_external_db_changes(tmp_path: Path):
    """外部直接修改 DB 后,reload 使内存刷新。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 先注册 dev team
    client.post("/api/teams", json={
        "name": "dev", "description": "original",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })

    # 外部直接通过 repo 修改 DB(模拟 DBA 操作)
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    external_team = Team(
        name="dev", description="externally_updated",
        root=Agent(name="leader", role="supervisor", system_prompt="x",
                   children=[Agent(name="w1", role="worker")]),
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    repo.upsert(external_team)
    conn.close()

    # 内存仍是旧值
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "original"

    # reload
    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200

    # 内存刷新
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "externally_updated"


def test_reload_empty_db_returns_zero(tmp_path: Path):
    """空 DB reload 返回 0。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200
    assert resp.json()["teams_reloaded"] == 0
    assert resp.json()["agents_reloaded"] == 0
