"""v2 API 测试:workers CRUD / requests / tickets / events(REST 通道)。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agentteam.v2.api.router import build_v2_router
from agentteam.v2.domain.worker import WorkerDef
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _make_client(make_v2, llm_by_model=None):
    v2 = make_v2(llm_by_model)
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api/v2")
    return TestClient(app), v2


def test_health(make_v2):
    client, v2 = _make_client(make_v2)
    resp = client.get("/api/v2/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_workers_crud(make_v2):
    client, _ = _make_client(make_v2)
    # create
    resp = client.post("/api/v2/workers", json={"name": "coder", "system_prompt": "写代码"})
    assert resp.status_code == 200
    # duplicate
    resp = client.post("/api/v2/workers", json={"name": "coder"})
    assert resp.status_code == 400
    # missing field
    resp = client.post("/api/v2/workers", json={"system_prompt": "x"})
    assert resp.status_code == 422
    # read
    assert client.get("/api/v2/workers/coder").json()["name"] == "coder"
    assert client.get("/api/v2/workers/nope").status_code == 404
    # update
    resp = client.put("/api/v2/workers/coder", json={"name": "coder", "max_iterations": 5})
    assert resp.status_code == 200
    assert client.get("/api/v2/workers/coder").json()["max_iterations"] == 5
    # name mismatch
    resp = client.put("/api/v2/workers/coder", json={"name": "other"})
    assert resp.status_code == 400
    # delete
    assert client.delete("/api/v2/workers/coder").json()["ok"] is True
    assert client.get("/api/v2/workers/coder").status_code == 404


def test_request_and_ticket_flow(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="完成了")])
    client, v2 = _make_client(make_v2, {"qwen-max": llm})
    client.post("/api/v2/workers", json={"name": "coder"})
    resp = client.post("/api/v2/requests", json={"text": "干活", "worker_name": "coder"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "dispatch"
    ticket_id = body["ticket_id"]
    t = wait_ticket_terminal(v2, ticket_id)
    # 看板过滤
    running = client.get("/api/v2/tickets", params={"status": t.status.value}).json()
    assert any(x["id"] == ticket_id for x in running)
    # 详情带 sessions
    detail = client.get(f"/api/v2/tickets/{ticket_id}").json()
    assert detail["status"] == "done"
    assert len(detail["sessions"]) == 1
    # transcript
    msgs = client.get(f"/api/v2/tickets/{ticket_id}/messages").json()
    assert msgs[-1]["content"] == "完成了"
    # 事件 REST 通道
    events = client.get(f"/api/v2/tickets/{ticket_id}/events").json()
    assert events[0]["type"] == "ticket_created"
    assert any(e["type"] == "ticket_status_changed" for e in events)


def test_request_validation(make_v2):
    client, _ = _make_client(make_v2)
    assert client.post("/api/v2/requests", json={"text": ""}).status_code == 422
    resp = client.get("/api/v2/tickets", params={"status": "bogus"})
    assert resp.status_code == 422
    assert client.get("/api/v2/tickets/nope").status_code == 404
    assert client.get("/api/v2/tickets/nope/events").status_code == 404


def test_cancel_pending_only(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    client, v2 = _make_client(make_v2, {"qwen-max": llm})
    client.post("/api/v2/workers", json={"name": "coder"})
    resp = client.post("/api/v2/requests", json={"text": "x", "worker_name": "coder"})
    ticket_id = resp.json()["ticket_id"]
    # done 后取消 → 409
    wait_ticket_terminal(v2, ticket_id)
    assert client.post(f"/api/v2/tickets/{ticket_id}/cancel").status_code == 409
    # 404
    assert client.post("/api/v2/tickets/nope/cancel").status_code == 404
