"""记忆层: MemoryRepo CRUD + run_memories/approvals 迁移。"""
import threading

import pytest

from agentteam.storage.db import init_db, run_migrations
from agentteam.storage.memory import MemoryRepo


@pytest.fixture
def repo(tmp_path):
    conn = init_db(str(tmp_path / "mem.db"))
    yield MemoryRepo(conn, lock=threading.Lock())
    conn.close()


def test_add_and_list_memory(repo):
    mid = repo.add_memory(
        team_name="dev", run_id="r1", outcome="completed",
        summary="完成 X,产出 f.py",
        meta={"key_points": ["坑:需要先装依赖"], "files": ["f.py"]},
        tokens=42,
    )
    assert mid > 0
    rows = repo.list_for_team("dev")
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "r1"
    assert row["outcome"] == "completed"
    assert row["summary"] == "完成 X,产出 f.py"
    assert row["meta"]["files"] == ["f.py"]
    assert row["tokens"] == 42


def test_list_for_team_scoped_and_ordered(repo):
    repo.add_memory("dev", "r1", "completed", "旧记忆")
    repo.add_memory("dev", "r2", "failed", "新记忆")
    repo.add_memory("other", "r3", "completed", "别队的")
    rows = repo.list_for_team("dev")
    assert [r["run_id"] for r in rows] == ["r2", "r1"]  # 新→旧,且只看本队


def test_list_for_team_limit(repo):
    for i in range(5):
        repo.add_memory("dev", f"r{i}", "completed", f"m{i}")
    assert len(repo.list_for_team("dev", limit=2)) == 2


def test_delete_memory_scoped_by_team(repo):
    mid = repo.add_memory("dev", "r1", "completed", "m")
    assert repo.delete_memory("other", mid) is False  # 队不匹配拒删
    assert repo.delete_memory("dev", mid) is True
    assert repo.list_for_team("dev") == []


def test_meta_corrupt_json_degrades(repo):
    repo._execute(
        "INSERT INTO run_memories (team_name, run_id, outcome, summary, meta) "
        "VALUES ('dev', 'r1', 'completed', 's', '{broken')",
    )
    row = repo.list_for_team("dev")[0]
    assert row["meta"] == "{broken"  # 解析失败原样返回,不抛


def test_migration_v12_idempotent(tmp_path):
    """同一 db 路径重复 init_db(迁移重跑)无副作用。"""
    path = str(tmp_path / "mig.db")
    conn1 = init_db(path)
    v1 = conn1.execute("PRAGMA user_version").fetchone()[0]
    assert v1 >= 13
    conn1.close()
    conn2 = init_db(path)
    assert run_migrations(conn2) == 0  # 全部已应用
    # 表与列都在
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(run_memories)")}
    assert {"id", "team_name", "run_id", "outcome", "summary", "meta", "tokens"} <= cols
    conn2.close()
