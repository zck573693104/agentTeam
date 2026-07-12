from tests.api.conftest import (
    _wait_for_run,
    make_provider_with_plan,
    make_team_json,
)


def test_dashboard_empty(make_client):
    """无 run 时 dashboard 应返回零值与空集合。"""
    client = make_client()
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 0
    assert data["total_tokens"] == 0
    assert data["by_status"] == {}
    assert data["by_team"] == {}
    assert data["recent_runs"] == []


def test_dashboard_with_runs(make_client):
    """有 run 完成后 dashboard 应聚合状态/团队计数与最近 run 列表。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "task1"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "completed"

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_runs"] == 1
    assert "completed" in data["by_status"]
    assert data["by_status"]["completed"] == 1
    assert "dev" in data["by_team"]
    assert data["by_team"]["dev"] == 1
    assert len(data["recent_runs"]) == 1
    # recent_runs 使用 run_id 字段（与 POST 响应一致），而非 id
    assert data["recent_runs"][0]["run_id"] == run_id
