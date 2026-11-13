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


# —— GET /api/teams/{name}/members/status 成员情况 ——

def _make_app_with_repos(tmp_path):
    """轻量 app + 真实 run/audit repo(临时库),用于成员情况聚合测试。"""
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentteam.storage.db import init_db
    from agentteam.storage.runs import RunRepo
    from agentteam.storage.audit import AuditRepo
    from agentteam.storage.teams import TeamRepo

    conn = init_db(str(tmp_path / "member_status.db"))
    lock = threading.Lock()
    run_repo = RunRepo(conn, lock=lock)
    audit_repo = AuditRepo(conn, lock=lock)
    team_store = TeamStore(repo=TeamRepo(conn, lock=lock))

    app = FastAPI()
    app.include_router(teams_router(
        team_store, None, run_repo=run_repo, audit_repo=audit_repo,
    ))
    return TestClient(app), run_repo, audit_repo


def test_member_status_team_not_found(tmp_path):
    """成员情况:团队不存在返回 404。"""
    client, _, _ = _make_app_with_repos(tmp_path)
    resp = client.get("/api/teams/nope/members/status")
    assert resp.status_code == 404


def test_member_status_without_activity(tmp_path):
    """成员情况:无运行记录时成员各项为 0/null,但配置成员仍列出。"""
    client, _, _ = _make_app_with_repos(tmp_path)
    client.post("/api/teams", json=_team_json())
    resp = client.get("/api/teams/dev/members/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["team"] == "dev"
    names = [m["name"] for m in body["members"]]
    # 旧 schema leader+workers 转换后:coder 是唯一 worker 叶子(leader 是 supervisor)
    assert "coder" in names
    coder = next(m for m in body["members"] if m["name"] == "coder")
    assert coder["role"] == "worker"
    assert coder["runs_participated"] == 0
    assert coder["starts"] == 0
    assert coder["failures"] == 0
    assert coder["last_active_at"] is None
    assert coder["last_run_id"] is None


def test_member_status_with_activity(tmp_path):
    """成员情况:聚合 worker_start/worker_end 事件(参与数/开工数/失败数/最近活跃)。"""
    import json
    client, run_repo, audit_repo = _make_app_with_repos(tmp_path)
    client.post("/api/teams", json=_team_json())

    # 两个 run:coder 在两个 run 都开工;run1 失败、run2 成功
    run1 = run_repo.create_run("dev", "task1")
    audit_repo.add_event(run1, "run_start", "system")
    audit_repo.add_event(run1, "worker_start", "coder")
    audit_repo.add_event(
        run1, "worker_end", "coder",
        {"answer_length": 0, "has_evidence": False, "failure": True},
    )
    run2 = run_repo.create_run("dev", "task2")
    audit_repo.add_event(run2, "worker_start", "coder")
    audit_repo.add_event(
        run2, "worker_end", "coder",
        {"answer_length": 120, "has_evidence": True, "failure": False},
    )

    resp = client.get("/api/teams/dev/members/status")
    assert resp.status_code == 200
    coder = next(
        m for m in resp.json()["members"] if m["name"] == "coder"
    )
    assert coder["runs_participated"] == 2
    assert coder["starts"] == 2
    assert coder["failures"] == 1
    assert coder["last_run_id"] == run2  # id DESC 扫描,最近活跃是 run2
    assert coder["last_active_at"]


def test_member_activity_by_team_max_events(tmp_path):
    """member_activity_by_team:max_events 限制扫描行数(取最新优先)。"""
    client, run_repo, audit_repo = _make_app_with_repos(tmp_path)
    client.post("/api/teams", json=_team_json())
    run_id = run_repo.create_run("dev", "task")
    for _ in range(5):
        audit_repo.add_event(run_id, "worker_start", "coder")
    rows = audit_repo.member_activity_by_team("dev", max_events=2)
    assert len(rows) == 1
    assert rows[0]["name"] == "coder"
    assert rows[0]["starts"] == 2  # 只扫到最新 2 条事件


def test_register_team_worker_role_optional():
    """worker 缺 role 字段时回退为成员名(兼容 Web 表单精简 payload)。"""
    client = TestClient(_make_app())
    payload = _team_json()
    del payload["workers"][0]["role"]
    resp = client.post("/api/teams", json=payload)
    assert resp.status_code == 200
    resp = client.get("/api/teams/dev")
    assert resp.status_code == 200
    # 转成 root 树后 role 固定为 worker(旧 schema 的 role 只是展示标签)
    assert resp.json()["root"]["children"][0]["role"] == "worker"
