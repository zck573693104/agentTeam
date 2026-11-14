"""v2 存储层测试:schema 幂等 / CAS 状态机 / transcript 往返。"""
from __future__ import annotations

import threading

from agentteam.v2.domain.events import SessionStatus, TicketStatus
from agentteam.v2.domain.mandate import Mandate, policy_version_for_pool
from agentteam.v2.domain.ticket import Ticket
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.storage.db import init_db
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo
from agentteam.v2.storage.workers import WorkerRepo


def test_init_db_idempotent(tmp_path):
    db = str(tmp_path / "a.db")
    conn1 = init_db(db)
    v1 = conn1.execute("PRAGMA user_version").fetchone()[0]
    conn1.close()
    conn2 = init_db(db)
    v2 = conn2.execute("PRAGMA user_version").fetchone()[0]
    conn2.close()
    assert v1 == v2 == 2  # v2 迁移: workers.mcp_json


def test_worker_repo_roundtrip(tmp_path):
    conn = init_db(str(tmp_path / "a.db"))
    repo = WorkerRepo(conn)
    w = WorkerDef(name="coder", system_prompt="写代码", model={"provider": "qwen", "name": "qwen-max"},
                  tools=["search_web"], skills=["dev_delivery"], approval_targets=["write_file"],
                  max_iterations=12)
    repo.upsert(w)
    got = repo.get("coder")
    assert got.name == "coder"
    assert got.model == {"provider": "qwen", "name": "qwen-max"}
    assert got.approval_targets == ["write_file"]
    # upsert 更新
    w2 = WorkerDef(name="coder", system_prompt="改代码")
    repo.upsert(w2)
    assert repo.get("coder").system_prompt == "改代码"
    assert repo.get("coder").tools == []
    assert repo.delete("coder")
    assert repo.get("coder") is None
    conn.close()


def test_ticket_cas_status(tmp_path):
    conn = init_db(str(tmp_path / "a.db"))
    repo = TicketRepo(conn)
    t = Ticket(id="t1", title="x", task="y", status=TicketStatus.PENDING,
               mandate=Mandate(basis="explicit", policy_version="abc"))
    repo.create(t)
    # 错误的期望状态 → False
    assert repo.cas_status("t1", TicketStatus.RUNNING, TicketStatus.RUNNING) is False
    # pending → running
    assert repo.cas_status("t1", TicketStatus.PENDING, TicketStatus.RUNNING) is True
    # running → done(终态,写 ended_at + result)
    assert repo.cas_status("t1", TicketStatus.RUNNING, TicketStatus.DONE, result="ok") is True
    got = repo.get("t1")
    assert got.status == TicketStatus.DONE
    assert got.result == "ok"
    assert got.ended_at is not None
    assert got.mandate.policy_version == "abc"
    conn.close()


def test_messages_roundtrip(tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    conn = init_db(str(tmp_path / "a.db"))
    repo = MessageRepo(conn)
    repo.append("s1", "user", "任务")
    ai = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"path": "a"}, "id": "c1"}])
    from agentteam.v2.kernel.session import _persist_message
    _persist_message(repo, "s1", ai)
    _persist_message(repo, "s1", ToolMessage(content="Wrote 1", tool_call_id="c1"))
    rows = repo.list_for_session("s1")
    assert [r["role"] for r in rows] == ["user", "assistant", "tool"]
    assert rows[1]["tool_calls"][0]["name"] == "write_file"
    assert rows[2]["tool_call_id"] == "c1"
    conn.close()


def test_session_repo(tmp_path):
    conn = init_db(str(tmp_path / "a.db"))
    repo = SessionRepo(conn)
    repo.create("s1", "t1", "coder", "ws/t1", 10, {"provider": "qwen", "name": "qwen-max"})
    assert repo.get("s1")["status"] == "running"
    assert repo.cas_status("s1", SessionStatus.RUNNING, SessionStatus.DONE)
    assert repo.list_running() == []
    repo.create("s2", "t1", "coder", "ws/t1", 10, None)
    assert len(repo.list_for_ticket("t1")) == 2
    conn.close()


def test_policy_version_changes_with_pool():
    a = [{"name": "coder", "system_prompt": "x"}]
    b = [{"name": "coder", "system_prompt": "y"}]
    assert policy_version_for_pool(a) != policy_version_for_pool(b)
    assert policy_version_for_pool(a) == policy_version_for_pool(list(a))


def test_event_repo_replay(tmp_path):
    from agentteam.v2.storage.events import EventRepo
    conn = init_db(str(tmp_path / "a.db"))
    repo = EventRepo(conn)
    e1 = repo.insert("t1", "ticket_created", payload={"a": 1})
    e2 = repo.insert("t1", "session_started", actor="coder", session_id="s1")
    repo.insert("t2", "ticket_created")
    assert repo.latest_event_id("t1") == e2["id"]
    after = repo.list_after("t1", after_id=e1["id"])
    assert [e["type"] for e in after] == ["session_started"]
    assert after[0]["payload"] == {}
    conn.close()
