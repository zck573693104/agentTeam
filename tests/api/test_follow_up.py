"""SP12d 绿色通道(追问直通 worker)测试。

覆盖:
- RunManager inbox:入队/消费 FIFO、per-worker 隔离、leader 目标键、
  未知 run 空 pop、follow_up_delivered 审计事件
- HTTP 端点:404/409/422 校验(归属约束:必须显式指定 worker,禁止广播)
- langgraph 消费:agent_step 在 LLM 调用前注入 [人工追问] 消息并持久化
- harness 消费:_drain_follow_ups 三步投递(interrupt → prompt_async →
  重锚 turn_id)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.api.routes.runs import runs_router
from agentteam.harness.engine import HarnessRunner
from agentteam.runtime.nodes import make_agent_step
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.domain.worker import Worker


@pytest.fixture
def db(tmp_path):
    conn = init_db(tmp_path / "followup.db")
    yield conn
    conn.close()


@pytest.fixture
def rm(db):
    """RunManager + 真实存储;预置 run 行 r1(run_events 外键需要)。"""
    from agentteam.storage.utils import utcnow_iso

    db.execute(
        "INSERT INTO runs (id, team_name, task, status, created_at, updated_at) "
        "VALUES ('r1', 't', 'task', 'running', ?, ?)",
        (utcnow_iso(), utcnow_iso()),
    )
    db.commit()
    return RunManager(RunRepo(db), AuditRepo(db), EventBus())


class _FakeRunRepo:
    def __init__(self, rows: dict):
        self._rows = rows

    def get_run(self, run_id):
        return self._rows.get(run_id)


def _app(run_row: dict | None, run_manager: RunManager) -> TestClient:
    app = FastAPI()
    rows = {run_row["id"]: run_row} if run_row else {}
    app.include_router(runs_router(
        run_manager=run_manager, team_store=None, model_provider=None,
        tool_registry=None,
        run_repo=_FakeRunRepo(rows),
        audit_repo=None, event_bus=None,
    ))
    return TestClient(app)


_RUNNING = {"id": "r1", "status": "running"}
_COMPLETED = {"id": "r1", "status": "completed"}


# ================= RunManager inbox =================


def test_deliver_and_pop_fifo(rm):
    assert rm.deliver_follow_up("r1", "worker", "w1", "第一条") == 1
    assert rm.deliver_follow_up("r1", "worker", "w1", "第二条") == 2
    assert rm.pop_follow_ups("r1", "w1") == ["第一条", "第二条"]
    assert rm.pop_follow_ups("r1", "w1") == []  # 取走即空


def test_pop_is_per_worker(rm):
    rm.deliver_follow_up("r1", "worker", "w1", "给 w1")
    rm.deliver_follow_up("r1", "worker", "w2", "给 w2")
    assert rm.pop_follow_ups("r1", "w1") == ["给 w1"]
    assert rm.pop_follow_ups("r1", "w2") == ["给 w2"]
    assert rm.pop_follow_ups("r1", "w3") == []  # 未指定的 worker 收不到


def test_leader_target_keyed_separately(rm):
    rm.deliver_follow_up("r1", "leader", None, "问领导")
    # leader 目标不进任何 worker 的 inbox
    assert rm.pop_follow_ups("r1", "w1") == []
    # 仅可通过 __leader__ 键取(当前无消费方,留作 attention 语义)
    assert rm.pop_follow_ups("r1", "__leader__") == ["问领导"]


def test_pop_unknown_run_returns_empty(rm):
    assert rm.pop_follow_ups("ghost", "w1") == []


def test_deliver_emits_audit_event(rm, db):
    rm.deliver_follow_up("r1", "worker", "w1", "进度如何?")
    audit = AuditRepo(db)
    events = [dict(e) for e in audit.list_events("r1")]
    delivered = [e for e in events if e["event_type"] == "follow_up_delivered"]
    assert len(delivered) == 1
    payload = __import__("json").loads(delivered[0]["payload"])
    assert payload["target"] == "worker"
    assert payload["worker"] == "w1"
    assert payload["queue_depth"] == 1


# ================= HTTP 端点 =================


def test_endpoint_delivers_to_worker(rm):
    client = _app(dict(_RUNNING), rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "worker", "worker_name": "w1",
                             "text": "用 A 还是 B?"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "target": "worker", "queue_depth": 1}
    assert rm.pop_follow_ups("r1", "w1") == ["用 A 还是 B?"]


def test_endpoint_unknown_run_404(rm):
    client = _app(None, rm)
    resp = client.post("/api/runs/ghost/messages",
                       json={"target": "leader", "text": "x"})
    assert resp.status_code == 404


def test_endpoint_terminal_run_409(rm):
    """终态 run 不接受投递:不自动复活,提示重新提交。"""
    client = _app(dict(_COMPLETED), rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "leader", "text": "x"})
    assert resp.status_code == 409, resp.text
    assert "new run" in resp.json()["detail"]


def test_endpoint_rejects_broadcast(rm):
    """归属约束:target=worker 必须显式指定 worker_name(禁止广播)。"""
    client = _app(dict(_RUNNING), rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "worker", "text": "x"})
    assert resp.status_code == 422
    assert "no broadcasting" in resp.json()["detail"]


def test_endpoint_rejects_bad_target(rm):
    client = _app(dict(_RUNNING), rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "all", "text": "x"})
    assert resp.status_code == 422


def test_endpoint_rejects_leader_with_worker_name(rm):
    client = _app(dict(_RUNNING), rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "leader", "worker_name": "w1",
                             "text": "x"})
    assert resp.status_code == 422


def test_endpoint_accepts_interrupted_run(rm):
    client = _app({"id": "r1", "status": "interrupted"}, rm)
    resp = client.post("/api/runs/r1/messages",
                       json={"target": "worker", "worker_name": "w1",
                             "text": "补充信息"})
    assert resp.status_code == 200


# ================= langgraph 消费 =================


class _SpyLLM:
    """记录 invoke 收到的消息,返回固定 AIMessage。"""

    def __init__(self):
        self.captured = None

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.captured = list(messages)
        return AIMessage(content="done")


class _StubRM:
    """RunManager 的最小桩:取消恒 False,追问可编程。"""

    def __init__(self, follow_ups: dict[str, list[str]] | None = None):
        self._follow_ups = follow_ups or {}

    def is_cancelled(self, run_id):
        return False

    def pop_follow_ups(self, run_id, worker_name):
        return self._follow_ups.pop(worker_name, [])


def test_agent_step_consumes_follow_up_before_llm():
    """agent_step 把追问包成 [人工追问] HumanMessage,先于 AI 回合入列。"""
    spy = _SpyLLM()
    worker = Worker(name="w1", role="r", description="", system_prompt="s")
    node = make_agent_step(
        worker, spy, [], run_manager=_StubRM({"w1": ["用 A 还是 B?"]}))
    state = {"run_id": "r1", "react_messages": [HumanMessage(content="do x")]}
    result = node(state)

    # LLM 本轮就看到了追问(直达,无需等下一回合)
    contents = [m.content for m in spy.captured]
    assert "[人工追问] 用 A 还是 B?" in contents
    assert contents.index("[人工追问] 用 A 还是 B?") > \
        contents.index("do x")  # 追问在原始任务之后

    # 追问消息持久化进 react_messages 通道(先追问后回应,历史完整)
    returned = result["react_messages"]
    assert isinstance(returned[0], HumanMessage)
    assert returned[0].content == "[人工追问] 用 A 还是 B?"
    assert isinstance(returned[1], AIMessage)


def test_agent_step_without_follow_up_no_extra_message():
    spy = _SpyLLM()
    worker = Worker(name="w1", role="r", description="", system_prompt="s")
    node = make_agent_step(worker, spy, [], run_manager=_StubRM())
    result = node({"run_id": "r1", "react_messages": []})
    assert [type(m).__name__ for m in result["react_messages"]] == ["AIMessage"]


# ================= harness 消费 =================


class _FakeOCClient:
    def __init__(self):
        self.interrupted: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    def interrupt_session(self, session_id):
        self.interrupted.append(session_id)

    def prompt_async(self, session_id, text, delivery=None, **kw):
        self.prompts.append((session_id, text))
        return {"id": "m-new-turn"}

    def turn_message_id(self, resp):
        return resp["id"]


def _runner(client, rm_stub, trace=None):
    return HarnessRunner(
        client=client, run_id="r1", team=None, task="t",
        trace_writer=trace, audit_repo=None, run_manager=rm_stub,
    )


class _TraceSpy:
    def __init__(self):
        self.events: list[tuple] = []

    def emit(self, run_id, event_type, actor, payload=None, **kwargs):
        self.events.append((run_id, event_type, actor, payload))


def test_harness_drain_delivers_follow_up():
    """轮询间隙把追问投到在飞会话:interrupt → prompt_async → 重锚 turn_id。"""
    client = _FakeOCClient()
    stub = _StubRM({"w1": ["补充:验收标准改成 contains"]})
    trace = _TraceSpy()
    runner = _runner(client, stub, trace)
    info = {"worker": "w1", "session_id": "s1", "turn_id": "t-old"}
    runner._drain_follow_ups({"__seq__": info})

    assert client.interrupted == ["s1"]
    assert len(client.prompts) == 1
    assert client.prompts[0][0] == "s1"
    assert "[人工追问]" in client.prompts[0][1]
    assert info["turn_id"] == "m-new-turn"  # 重锚,引擎轮询衔接新回合
    delivered = [e for e in trace.events if e[1] == "follow_up_delivered"]
    assert len(delivered) == 1
    assert delivered[0][2] == "user"


def test_harness_drain_skips_empty_inbox():
    client = _FakeOCClient()
    runner = _runner(client, _StubRM())
    runner._drain_follow_ups(
        {"__seq__": {"worker": "w1", "session_id": "s1", "turn_id": "t"}})
    assert client.interrupted == [] and client.prompts == []
