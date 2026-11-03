"""SP12c: TriggerRepo CRUD + v15 迁移幂等。"""
import threading

import pytest

from agentteam.domain.trigger import TriggerRule
from agentteam.storage.db import init_db, run_migrations
from agentteam.storage.triggers import TriggerRepo


def _rule(name="hook1", team="dev", **kw) -> TriggerRule:
    base = dict(
        name=name, team_name=team, source="webhook", secret="s3cret",
        conditions=[{"field": "payload.text", "op": "contains", "value": "bug"}],
        task_from="payload.text",
        slow_path={"enabled": False, "prompt": ""},
        enabled=True,
    )
    base.update(kw)
    return TriggerRule(**base)


@pytest.fixture
def repo(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    yield TriggerRepo(conn, lock=threading.Lock())
    conn.close()


def test_add_get_rule_roundtrip(repo):
    repo.add_rule(_rule())
    got = repo.get_by_name("hook1")
    assert got is not None
    assert got.team_name == "dev"
    assert got.secret == "s3cret"
    assert got.conditions[0]["op"] == "contains"
    assert got.task_from == "payload.text"
    assert got.enabled is True


def test_add_duplicate_name_raises(repo):
    repo.add_rule(_rule())
    with pytest.raises(ValueError, match="already exists"):
        repo.add_rule(_rule())


def test_list_scoped_by_team_and_ordered(repo):
    repo.add_rule(_rule("a", "dev"))
    repo.add_rule(_rule("b", "dev"))
    repo.add_rule(_rule("c", "other"))
    assert [r.name for r in repo.list_rules("dev")] == ["b", "a"]
    assert len(repo.list_rules()) == 3


def test_update_and_delete(repo):
    repo.add_rule(_rule())
    assert repo.update_rule(_rule(enabled=False, secret=None)) is True
    got = repo.get_by_name("hook1")
    assert got.enabled is False and got.secret is None
    assert repo.delete_rule("hook1") is True
    assert repo.get_by_name("hook1") is None
    assert repo.delete_rule("hook1") is False


def test_corrupt_rules_json_skipped_in_list(repo):
    repo.add_rule(_rule("good"))
    repo._execute(
        "INSERT INTO trigger_rules (name, team_name, source, rules) "
        "VALUES ('bad', 'dev', 'webhook', '{broken')",
    )
    names = [r.name for r in repo.list_rules("dev")]
    assert names == ["good"]
    with pytest.raises(ValueError, match="corrupt"):
        repo.get_by_name("bad")


def test_migration_v15_idempotent(tmp_path):
    path = str(tmp_path / "mig.db")
    conn1 = init_db(path)
    assert conn1.execute("PRAGMA user_version").fetchone()[0] >= 15
    conn1.close()
    conn2 = init_db(path)
    assert run_migrations(conn2) == 0
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(trigger_rules)")}
    assert {"id", "name", "team_name", "source", "secret", "rules"} <= cols
    conn2.close()
