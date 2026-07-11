import json
import sqlite3


def _seed_run(conn: sqlite3.Connection) -> str:
    from agentteam.storage.runs import RunRepo

    return RunRepo(conn).create_run(team_name="t", task="x")


def test_add_and_list_events(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    repo.add_event(run_id, event_type="run_start", actor="system", payload={"task": "x"})
    repo.add_event(run_id, event_type="worker_start", actor="coder", payload={"step": 0}, tokens=12)

    events = repo.list_events(run_id)
    assert len(events) == 2
    assert events[0]["event_type"] == "run_start"
    assert events[1]["tokens"] == 12
    assert json.loads(events[1]["payload"]) == {"step": 0}


def test_add_approval_and_decide(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    aid = repo.add_approval(run_id)
    assert repo.get_approval(aid)["status"] == "pending"

    repo.decide_approval(aid, decision="approved", decider="alice", reason="ok")
    ap = repo.get_approval(aid)
    assert ap["status"] == "approved"
    assert ap["decider"] == "alice"
    assert ap["decided_at"] is not None


def test_list_pending_approvals(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    a = repo.add_approval(run_id)
    repo.add_approval(run_id)  # 第二个保持 pending
    repo.decide_approval(a, "approved", "u")

    pending = repo.list_pending_approvals(run_id)
    assert len(pending) == 1
