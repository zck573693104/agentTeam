"""R2 测试:dashboard 聚合 / GitHub 入站源(轮询+去重)。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.sources.poll import GitHubSource
from agentteam.v2.sources.protocol import Trigger
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _issue(number, updated, title="issue", body="body"):
    return {
        "number": number, "title": title, "body": body, "state": "open",
        "updated_at": updated, "created_at": updated,
        "user": {"login": "alice"}, "labels": [], "html_url": f"http://x/{number}",
    }


def test_dashboard_aggregation(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    wait_ticket_terminal(v2, resp["ticket_id"])
    # dashboard 端点逻辑(直接调 route 函数级组件:此处对 repo 方法断言)
    by_status = v2.ticket_repo.count_by_status()
    assert by_status.get("done") == 1
    assert v2.event_repo.recent_count(1.0) > 0
    assert isinstance(v2.outbox_repo.counts(), dict)


def test_dashboard_endpoint(make_v2):
    client = None
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agentteam.v2.api.router import build_v2_router
    v2 = make_v2({"qwen-max": FakeLLM()})
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api")
    client = TestClient(app)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tickets"]["total"] == 0
    assert body["workers"]["total"] == 0
    assert "events_recent_1h" in body


def test_github_poll_and_dedupe(make_v2):
    v2 = make_v2({"qwen-max": FakeLLM()})
    # fake github API
    issues = [
        _issue(1, "2026-11-13T10:00:00Z", "修复登录", "重现路径如下"),
        _issue(2, "2026-11-13T11:00:00Z", "加导出功能", "csv"),
    ]
    comments = [{"id": 99, "body": "同意,优先做", "created_at": "2026-11-13T11:30:00Z",
                 "user": {"login": "bob"}, "html_url": "http://x/99"}]
    src = GitHubSource("gh", {"repos": ["acme/app"]}, None)
    with patch.object(src, "_get", side_effect=[
        MagicMock(status_code=200, json=lambda: issues),
        MagicMock(status_code=200, json=lambda: comments),
        MagicMock(status_code=200, json=lambda: []),
        MagicMock(status_code=200, json=lambda: []),
        MagicMock(status_code=200, json=lambda: []),
        MagicMock(status_code=200, json=lambda: []),
    ]):
        first = src.poll()
        assert len(first) == 3  # issue1 + issue1 评论 + issue2
        second = src.poll()  # 游标推进后无新事件
        assert second == []
    # external_id 去重:重放同事件不重复落库
    thread = v2.thread_repo.get_or_create("gh", "acme/app#1")
    raw = {"external_id": "acme/app#1:issue:2026-11-13T10:00:00Z"}
    t1 = v2.thread_repo.append_trigger(thread["id"], "alice", "修复登录", raw=raw)
    t2 = v2.thread_repo.append_trigger(thread["id"], "alice", "修复登录", raw=raw)
    assert t1["id"] == t2["id"]


def test_github_inbound_creates_ticket_via_auto(make_v2):
    """github poll → trigger → gate → eval(auto) → 立项(inferred)。"""
    from agentteam.v2.kernel.eval_engine import EvalDecision
    from agentteam.v2.kernel.router import RouterDecision
    llm = FakeLLM()
    llm.set_structured_responses([
        EvalDecision(accept=True, reason="ok"),
        RouterDecision(action="dispatch", worker="coder", goal="修登录"),
    ])
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    v2.source_repo.create("gh", "webhook", {"mode": "auto"})
    thread = v2.thread_repo.get_or_create("gh", "acme/app#1")
    trig = v2.thread_repo.append_trigger(
        thread["id"], "alice", "修复登录报错",
        raw={"external_id": "acme/app#1:issue:x"},
    )
    trigger = Trigger(
        actor="alice", thread_key="acme/app#1", text="修复登录报错",
        raw={"external_id": "acme/app#1:issue:x2"},
    )
    result = v2.inbound_engine.handle("gh", trigger)
    assert result["decision"] == "accepted"
    t = wait_ticket_terminal(v2, result["ticket_id"])
    assert t.mandate.basis == "inferred"
    assert t.mandate.submitted_by == "source:gh"
