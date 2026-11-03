"""SP12c: Trigger API 集成测试(规则 CRUD + 入站 webhook 端到端)。"""
import threading

from agentteam.storage.db import init_db
from agentteam.storage.triggers import TriggerRepo

from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _register_rule(client, name="gh_hook", **overrides):
    body = {
        "name": name,
        "team_name": "dev",
        "secret": "s3cret",
        "conditions": [{"field": "headers.x-kind", "op": "eq", "value": "request"}],
        "task_from": "payload.text",
        "slow_path": {"enabled": False, "prompt": ""},
        "enabled": True,
    }
    body.update(overrides)
    return client.post("/api/triggers", json=body)


def test_trigger_rule_crud(make_client):
    client = make_client()
    client.post("/api/teams", json=make_team_json(name="dev"))

    resp = _register_rule(client)
    assert resp.status_code == 200
    assert resp.json() == {"name": "gh_hook"}

    # 重复名 400;未知团队 422;非法条件 422
    assert _register_rule(client).status_code == 400
    assert _register_rule(client, team_name="ghost").status_code == 422
    bad = _register_rule(client, name="bad", conditions=[{"field": "a", "op": "huh", "value": 1}])
    assert bad.status_code == 422

    # 列表:secret 打码
    rows = client.get("/api/triggers").json()
    assert rows[0]["secret"] == "****"
    assert rows[0]["conditions"][0]["op"] == "eq"

    # PUT 更新 + DELETE
    upd = client.put("/api/triggers/gh_hook", json={
        "team_name": "dev", "enabled": False,
        "task_template": "处理 {payload.text}",
    })
    assert upd.status_code == 200
    got = client.get("/api/triggers/gh_hook").json()
    assert got["enabled"] is False and got["task_template"] == "处理 {payload.text}"
    assert client.delete("/api/triggers/gh_hook").json() == {"ok": True}
    assert client.get("/api/triggers/gh_hook").status_code == 404


def test_webhook_secret_and_match(make_client, tmp_path):
    from tests.api.conftest import make_provider_with_plan as _mp
    client = make_client(fake_provider=make_provider_with_plan(),
                         db_path=str(tmp_path / "t.db"))
    client.post("/api/teams", json=make_team_json(name="dev"))
    _register_rule(client)

    headers = {"X-Trigger-Secret": "s3cret", "x-kind": "request"}

    # secret 错 → 401
    resp = client.post("/api/triggers/webhook/gh_hook",
                       json={"text": "修复 bug"}, headers={"X-Trigger-Secret": "nope"})
    assert resp.status_code == 401

    # Fast Path 未命中 → matched=false
    resp = client.post("/api/triggers/webhook/gh_hook",
                       json={"text": "修复 bug"}, headers={**headers, "x-kind": "chat"})
    assert resp.status_code == 200
    assert resp.json()["matched"] is False

    # 命中 → run 创建,triggered_by_user 记 trigger:<名>
    resp = client.post("/api/triggers/webhook/gh_hook",
                       json={"text": "修复登录 bug"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True and body["via"] == "fast_path"
    run_id = body["run_id"]
    assert _wait_for_run(client, run_id) == "completed"

    # run_start 事件带 trigger 来源(REST trace 的 payload 是 JSON 字符串)
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    start = next(e for e in trace if e["event_type"] == "run_start")
    payload = start["payload"]
    if isinstance(payload, str):
        payload = __import__("json").loads(payload)
    assert payload["triggered_by_user"] == "trigger:gh_hook"

    # trigger_fired 审计事件
    assert any(e["event_type"] == "trigger_fired" for e in trace)


def test_webhook_unknown_rule_404(make_client):
    client = make_client()
    assert client.post("/api/triggers/webhook/nope", json={}).status_code == 404


def test_webhook_template_task(make_client, tmp_path):
    client = make_client(fake_provider=make_provider_with_plan(),
                         db_path=str(tmp_path / "t.db"))
    client.post("/api/teams", json=make_team_json(name="dev"))
    _register_rule(client, name="tpl", secret=None,
                   conditions=[], task_template="巡检 {payload.repo}")
    resp = client.post("/api/triggers/webhook/tpl", json={"repo": "core-db"})
    assert resp.status_code == 200
    assert resp.json()["matched"] is True
    task = client.get(f"/api/runs/{resp.json()['run_id']}").json()["task"]
    assert task == "巡检 core-db"


def test_triggers_survive_restart(make_client, tmp_path):
    """规则持久化:重启(create_app 重建)后仍可用。"""
    from agentteam.api.server import create_app
    from fastapi.testclient import TestClient
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider, FakeLLM

    db_path = str(tmp_path / "t.db")
    client = make_client(fake_provider=make_provider_with_plan(), db_path=db_path)
    client.post("/api/teams", json=make_team_json(name="dev"))
    _register_rule(client, name="persist", secret=None, conditions=[],
                   task_template="巡检 {payload.repo}")
    client.post("/api/triggers/webhook/persist", json={"repo": "a"})

    # 重建 app(模拟服务重启)
    app2 = create_app(db_path=db_path, model_provider=make_provider_with_plan(),
                      tool_registry=ToolRegistry())
    client2 = TestClient(app2)
    resp = client2.post("/api/triggers/webhook/persist", json={"repo": "b"})
    assert resp.status_code == 200
    assert resp.json()["matched"] is True
