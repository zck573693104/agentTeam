"""P-A3 管理操作审计:admin_events 表读写测试。"""
from __future__ import annotations

import json
import sqlite3

from agentteam.storage.admin_audit import AdminAuditRepo


def test_add_event_returns_id(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    eid = repo.add_event("team_created", "team", "t1", actor="alice", payload={"k": "v"})
    assert isinstance(eid, int)
    assert eid > 0


def test_add_event_default_actor(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    eid = repo.add_event("team_created", "team", "t1")
    events = repo.list_events()
    assert len(events) == 1
    assert events[0]["actor"] == "api-user"
    assert events[0]["id"] == eid


def test_add_event_payload_serialized_as_json(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("quota_set", "quota", "t1", payload={"limit": 1000, "nested": {"a": 1}})
    events = repo.list_events()
    payload = json.loads(events[0]["payload"])
    assert payload == {"limit": 1000, "nested": {"a": 1}}


def test_add_event_none_payload(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_deleted", "team", "t1", payload=None)
    events = repo.list_events()
    assert json.loads(events[0]["payload"]) == {}


def test_list_events_ordered_desc(tmp_db: sqlite3.Connection):
    """list_events 按时间倒序返回(最新在前)。"""
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_created", "team", "t1")
    repo.add_event("team_updated", "team", "t1")
    repo.add_event("team_deleted", "team", "t1")
    events = repo.list_events()
    assert len(events) == 3
    # 最新插入的在前
    assert events[0]["event_type"] == "team_deleted"
    assert events[2]["event_type"] == "team_created"


def test_list_events_filter_by_resource(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_created", "team", "t1")
    repo.add_event("library_agent_created", "library_agent", "a1")
    repo.add_event("quota_set", "quota", "t1")
    team_events = repo.list_events(resource="team")
    assert len(team_events) == 1
    assert team_events[0]["resource"] == "team"
    quota_events = repo.list_events(resource="quota")
    assert len(quota_events) == 1
    assert quota_events[0]["resource"] == "quota"


def test_list_events_filter_by_actor(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_created", "team", "t1", actor="alice")
    repo.add_event("team_updated", "team", "t1", actor="bob")
    alice_events = repo.list_events(actor="alice")
    assert len(alice_events) == 1
    assert alice_events[0]["actor"] == "alice"


def test_list_events_filter_by_resource_and_actor(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_created", "team", "t1", actor="alice")
    repo.add_event("team_updated", "team", "t1", actor="bob")
    repo.add_event("library_agent_created", "library_agent", "a1", actor="alice")
    filtered = repo.list_events(resource="team", actor="alice")
    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "team_created"


def test_list_events_pagination(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    for i in range(5):
        repo.add_event("team_created", "team", f"t{i}")
    page1 = repo.list_events(limit=2, offset=0)
    page2 = repo.list_events(limit=2, offset=2)
    page3 = repo.list_events(limit=2, offset=4)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # 不重复
    ids = {e["id"] for e in page1 + page2 + page3}
    assert len(ids) == 5


def test_count_events_total(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    assert repo.count_events() == 0
    repo.add_event("team_created", "team", "t1")
    repo.add_event("quota_set", "quota", "t1")
    assert repo.count_events() == 2


def test_count_events_filtered(tmp_db: sqlite3.Connection):
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("team_created", "team", "t1", actor="alice")
    repo.add_event("team_updated", "team", "t1", actor="bob")
    repo.add_event("quota_set", "quota", "t1", actor="alice")
    assert repo.count_events(resource="team") == 2
    assert repo.count_events(actor="alice") == 2
    assert repo.count_events(resource="team", actor="alice") == 1


def test_resource_id_optional(tmp_db: sqlite3.Connection):
    """resource_id 可为 None(如 cache_reloaded 这类系统级事件)。"""
    repo = AdminAuditRepo(tmp_db)
    repo.add_event("cache_reloaded", "system", resource_id=None)
    events = repo.list_events()
    assert events[0]["resource_id"] is None
