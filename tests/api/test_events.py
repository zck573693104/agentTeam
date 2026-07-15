import queue as queue_mod
import time

from agentteam.api.events import EventBus, BroadcastTraceWriter
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db


# ---- EventBus 测试 ----

def test_event_bus_subscribe_and_publish():
    bus = EventBus()
    q = bus.subscribe("run-1")
    bus.publish("run-1", {"event_type": "test", "data": 42})
    event = q.get(timeout=1.0)
    assert event["event_type"] == "test"
    assert event["data"] == 42


def test_event_bus_multiple_subscribers():
    bus = EventBus()
    q1 = bus.subscribe("run-1")
    q2 = bus.subscribe("run-1")
    bus.publish("run-1", {"event_type": "test"})
    assert q1.get(timeout=1.0)["event_type"] == "test"
    assert q2.get(timeout=1.0)["event_type"] == "test"


def test_event_bus_unsubscribe_stops_receiving():
    bus = EventBus()
    q = bus.subscribe("run-1")
    bus.unsubscribe("run-1", q)
    bus.publish("run-1", {"event_type": "test"})
    with __import__("pytest").raises(queue_mod.Empty):
        q.get(timeout=0.5)


def test_event_bus_publish_to_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish("run-1", {"event_type": "test"})  # 不抛异常


# ---- BroadcastTraceWriter 测试 ----

def test_broadcast_trace_writer_writes_sqlite_and_publishes_event_bus():
    conn = init_db(":memory:")
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    q = bus.subscribe("run-1")
    writer = BroadcastTraceWriter(audit_repo, bus)

    writer.emit("run-1", "worker_start", "coder", {"step": 1})

    # SQLite 有记录
    events = audit_repo.list_events("run-1")
    assert len(events) == 1
    assert events[0]["event_type"] == "worker_start"
    assert events[0]["actor"] == "coder"

    # EventBus 也收到
    event = q.get(timeout=1.0)
    assert event["event_type"] == "worker_start"
    assert event["id"] == events[0]["id"]  # SQLite 行 ID
    assert event["actor"] == "coder"
    conn.close()


def test_broadcast_trace_writer_publishes_to_multiple_subscribers():
    conn = init_db(":memory:")
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    q1 = bus.subscribe("run-1")
    q2 = bus.subscribe("run-1")
    writer = BroadcastTraceWriter(audit_repo, bus)

    writer.emit("run-1", "run_start", "system")

    assert q1.get(timeout=1.0)["event_type"] == "run_start"
    assert q2.get(timeout=1.0)["event_type"] == "run_start"
    conn.close()


def test_event_bus_bounded_queue_drops_oldest():
    """队列满后丢弃最旧事件，不崩溃。新事件仍可入队。"""
    bus = EventBus()
    q = bus.subscribe("run-1")
    # 发布超过 maxsize 个事件
    for i in range(1200):
        bus.publish("run-1", {"event_type": "test", "seq": i})
    # 队列不应超过 maxsize
    assert q.qsize() <= 1000
    # 最旧的事件应已被丢弃，最新的事件应在队列中
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert len(events) <= 1000
    # 最新事件保留
    assert events[-1]["seq"] == 1199
