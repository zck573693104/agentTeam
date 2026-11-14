"""P3 测试:gate 快筛 / Eval 三事实 / 观察模式 / webhook 接入 / SourceDaemon。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agentteam.v2.api.router import build_v2_router
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.eval_engine import EvalDecision
from agentteam.v2.kernel.gate import Gate
from agentteam.v2.kernel.router import RouterDecision
from agentteam.v2.sources.daemon import SourceDaemon
from agentteam.v2.sources.protocol import Trigger
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _make_client(make_v2, llm_by_model=None):
    v2 = make_v2(llm_by_model)
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api/v2")
    return TestClient(app), v2


# —— gate 单元 ——
def test_gate_whitelist():
    gate = Gate({"allowed_actors": ["alice"], "keywords": ["部署"]})
    assert gate.check("alice", "帮我部署服务") == (True, "passed")
    assert gate.check("mallory", "帮我部署服务")[0] is False
    assert gate.check("alice", "随便聊聊")[0] is False


def test_gate_star_and_regex():
    assert Gate({}).check("anyone", "anything") == (True, "passed")
    gate = Gate({"regex": r"issue[- ]?\d+"})
    assert gate.check("bob", "fix issue-42")[0] is True
    assert gate.check("bob", "hello")[0] is False


def test_webhook_observe_flow(make_v2):
    """观察模式默认:过筛+过研判 → would_accept(只记录不立项)。"""
    llm = FakeLLM()
    llm.set_structured_responses([EvalDecision(accept=True, reason="三事实成立")])
    client, v2 = _make_client(make_v2, {"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"mode": "observe"}})
    resp = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "alice", "thread_key": "chat-9",
        "text": "把登录页的报错修一下,验收:重现路径不再报错",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "would_accept"
    assert body.get("ticket_id") is None
    # 观察面板可见
    reviews = client.get("/api/v2/sources/observe/reviews").json()
    assert len(reviews) == 1 and reviews[0]["decision"] == "would_accept"


def test_webhook_observe_confirm_promotes(make_v2):
    """人工确认转正:would_accept → 立项(inferred Mandate)。"""
    llm = FakeLLM()
    llm.set_structured_responses([EvalDecision(accept=True, reason="ok")])
    llm.set_invoke_responses([AIMessage(content="done")])
    client, v2 = _make_client(make_v2, {"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"mode": "observe"}})
    body = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "alice", "thread_key": "chat-9", "text": "修登录报错",
    }).json()
    confirm = client.post(f"/api/v2/sources/observe/{body['evaluation_id']}/confirm",
                          json={"by": "boss"})
    assert confirm.status_code == 200
    tid = confirm.json()["ticket_id"]
    t = wait_ticket_terminal(v2, tid)
    assert t.status.value == "done"
    assert t.mandate.basis == "inferred"          # dsh: inferred 授权
    assert t.mandate.submitted_by == "source:ops"  # 身份分离:Source 独立身份
    # 转正后评价记录翻转为 accepted 且带 ticket
    reviews = client.get("/api/v2/sources/observe/reviews").json()
    assert reviews == []


def test_webhook_gate_rejected(make_v2):
    """gate 快筛拒绝:不进 LLM(无 structured 响应也安全)。"""
    client, v2 = _make_client(make_v2, {"qwen-max": FakeLLM()})
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"allowed_actors": ["alice"]}})
    resp = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "mallory", "thread_key": "c", "text": "hello",
    })
    assert resp.status_code == 200
    assert resp.json()["decision"] == "rejected"
    assert "白名单" in resp.json()["reason"]


def test_webhook_three_facts_rejected(make_v2):
    """Eval 拿不准倾向不行动:三事实不成立 → rejected。"""
    llm = FakeLLM()
    llm.set_structured_responses([EvalDecision(accept=False, reason="纯咨询")])
    client, _ = _make_client(make_v2, {"qwen-max": llm})
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"mode": "auto"}})
    resp = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "alice", "thread_key": "c", "text": "这个项目是干嘛的?",
    })
    body = resp.json()
    assert body["decision"] == "rejected"


def test_webhook_auto_mode_creates_ticket(make_v2):
    """auto 模式:过筛 + 过研判 → 直接立项(仍 inferred)。"""
    llm = FakeLLM()
    llm.set_structured_responses([
        EvalDecision(accept=True, reason="ok"),
        RouterDecision(action="dispatch", worker="coder", goal="修登录"),
    ])
    llm.set_invoke_responses([AIMessage(content="done")])
    client, v2 = _make_client(make_v2, {"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"mode": "auto"}})
    resp = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "alice", "thread_key": "c", "text": "修登录报错",
    })
    body = resp.json()
    assert body["decision"] == "accepted"
    t = wait_ticket_terminal(v2, body["ticket_id"])
    assert t.status.value == "done"
    assert t.mandate.basis == "inferred"


def test_webhook_secret_enforced(make_v2):
    llm = FakeLLM()
    llm.set_structured_responses([EvalDecision(accept=True, reason="ok")])
    client, _ = _make_client(make_v2, {"qwen-max": llm})
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "secret": "s3cret", "config": {}})
    # 无秘钥 → 401
    assert client.post("/api/v2/sources/ops/webhook", json={
        "actor": "a", "thread_key": "c", "text": "t",
    }).status_code == 401
    # 错秘钥 → 401
    assert client.post("/api/v2/sources/ops/webhook", headers={"X-Source-Secret": "bad"},
                       json={"actor": "a", "thread_key": "c", "text": "t"}).status_code == 401


def test_source_crud(make_v2):
    client, _ = _make_client(make_v2)
    assert client.post("/api/v2/sources", json={"name": "ops"}).status_code == 200
    assert client.post("/api/v2/sources", json={"name": "ops"}).status_code == 400
    assert client.post("/api/v2/sources", json={"name": "x", "kind": "bad"}).status_code == 422
    assert client.get("/api/v2/sources/ops").json()["kind"] == "webhook"
    assert client.put("/api/v2/sources/ops", json={"config": {"mode": "auto"}}).json()["config"]["mode"] == "auto"
    assert "secret_hash" not in client.get("/api/v2/sources/ops").json()
    assert client.delete("/api/v2/sources/ops").json()["ok"] is True
    assert client.get("/api/v2/sources/ops").status_code == 404


def test_source_daemon_poll_loop(make_v2):
    """SourceDaemon:轮询源产出 Trigger → 入站管线(用 fake poll source 验证循环)。"""
    llm = FakeLLM()
    llm.set_structured_responses([EvalDecision(accept=False, reason="测试")])
    v2 = make_v2({"qwen-max": llm})

    handled: list[tuple[str, Trigger]] = []

    class FakePollSource:
        name = "fake_poll"
        kind = "github"
        poll = lambda self: [  # noqa: E731
            Trigger(actor="gh-user", thread_key="issue-1", text="test trigger text")
        ]

    src = FakePollSource()
    daemon = SourceDaemon([src], lambda name, t: handled.append((name, t)), poll_interval=0.05)

    async def _run():
        await daemon.start()
        await __import__("asyncio").sleep(0.3)
        await daemon.stop()

    import asyncio
    asyncio.run(_run())
    assert len(handled) >= 1
    assert handled[0][0] == "fake_poll"
    assert handled[0][1].thread_key == "issue-1"
