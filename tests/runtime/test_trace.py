# tests/runtime/test_trace.py
"""TraceWriter 协议与实现的测试。"""
from __future__ import annotations

from agentteam.runtime.trace import FakeTraceWriter, SqliteTraceWriter
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo


def test_fake_trace_writer_collects_events():
    """FakeTraceWriter 按顺序收集事件到列表。"""
    tw = FakeTraceWriter()
    tw.emit("run1", "run_start", "system")
    tw.emit("run1", "leader_plan", "leader", {"steps": 3})
    tw.emit("run1", "worker_end", "w1", duration_ms=150, tokens=42)

    assert len(tw.events) == 3
    assert tw.events[0]["event_type"] == "run_start"
    assert tw.events[0]["run_id"] == "run1"
    assert tw.events[1]["payload"] == {"steps": 3}
    assert tw.events[2]["duration_ms"] == 150
    assert tw.events[2]["tokens"] == 42


def test_fake_trace_writer_starts_empty():
    """FakeTraceWriter 初始无事件。"""
    tw = FakeTraceWriter()
    assert tw.events == []


def test_sqlite_trace_writer_writes_to_db(tmp_db):
    """SqliteTraceWriter 将事件写入 run_events 表。"""
    run_repo = RunRepo(tmp_db)
    run_id = run_repo.create_run("team1", "task1")

    audit_repo = AuditRepo(tmp_db)
    tw = SqliteTraceWriter(audit_repo)
    tw.emit(run_id, "run_start", "system", {"key": "value"})

    events = audit_repo.list_events(run_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "run_start"
    assert events[0]["actor"] == "system"
    import json
    assert json.loads(events[0]["payload"]) == {"key": "value"}


def test_sqlite_trace_writer_multiple_events(tmp_db):
    """SqliteTraceWriter 按顺序写入多个事件。"""
    run_repo = RunRepo(tmp_db)
    run_id = run_repo.create_run("team1", "task1")

    audit_repo = AuditRepo(tmp_db)
    tw = SqliteTraceWriter(audit_repo)
    tw.emit(run_id, "run_start", "system")
    tw.emit(run_id, "leader_plan", "leader")
    tw.emit(run_id, "run_end", "system")

    events = audit_repo.list_events(run_id)
    assert len(events) == 3
    assert events[0]["event_type"] == "run_start"
    assert events[2]["event_type"] == "run_end"
