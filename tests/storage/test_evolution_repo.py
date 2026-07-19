"""SP7b EvolutionRepo 单元测试。"""
import sqlite3
import threading

import pytest

from agentteam.storage.evolution import EvolutionRepo


def _make_repo(conn: sqlite3.Connection) -> EvolutionRepo:
    """用现有连接构造 EvolutionRepo(独立 lock,适合单线程单元测试)。"""
    return EvolutionRepo(conn, lock=threading.Lock())


def test_add_record_returns_id(tmp_db):
    """add_record 返回新插入的 id(>=1)。"""
    repo = _make_repo(tmp_db)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="old", after_value="new", diff="...",
        reason="test", run_id="r1", success=True,
    )
    assert eid >= 1


def test_add_record_failed_dimension_stores_error(tmp_db):
    """success=False 时,error/success/reason 字段被正确持久化。"""
    repo = _make_repo(tmp_db)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="", after_value="", diff="",
        reason="LLM error", run_id="r1", success=False, error="timeout",
    )
    history = repo.list_history("coder")
    assert len(history) == 1
    rec = history[0]
    assert rec["id"] == eid
    assert rec["success"] == 0  # SQLite BOOLEAN 存为 int
    assert rec["error"] == "timeout"
    assert rec["reason"] == "LLM error"


def test_list_history_returns_records_for_agent(tmp_db):
    """list_history 返回该 agent 的所有记录。"""
    repo = _make_repo(tmp_db)
    repo.add_record("coder", 1, "prompt", "a", "b", "", "r1", "r1", True)
    repo.add_record("coder", 2, "params", "{}", "{}", "", "r2", "r2", True)
    repo.add_record("reviewer", 1, "prompt", "x", "y", "", "r3", "r3", True)
    history = repo.list_history("coder")
    assert len(history) == 2
    assert all(h["agent_name"] == "coder" for h in history)


def test_list_history_ordered_by_timestamp_desc(tmp_db):
    """list_history 按 timestamp 倒序(最新在前),而非 id 倒序。

    反序插入:先插 timestamp 较晚的 version=2,再插 timestamp 较早的 version=1。
    - id 顺序:version=2 (id=1) → version=1 (id=2)
    - timestamp 顺序:version=2 (Jan 2) → version=1 (Jan 1)
    若实现误用 ORDER BY id DESC,会返回 version=1 在前(id=2 DESC);
    正确实现 ORDER BY timestamp DESC 返回 version=2 在前。
    """
    tmp_db.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 2, "prompt", 1, "2026-01-02 10:00:00"),
    )
    tmp_db.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 1, "prompt", 1, "2026-01-01 10:00:00"),
    )
    tmp_db.commit()
    repo = _make_repo(tmp_db)
    history = repo.list_history("a")
    assert len(history) == 2
    assert history[0]["version"] == 2  # timestamp 晚的在前(非 id 大的在前)
    assert history[1]["version"] == 1


def test_list_history_respects_limit(tmp_db):
    """list_history(limit=N) 最多返回 N 条。"""
    repo = _make_repo(tmp_db)
    for i in range(5):
        repo.add_record("a", i + 1, "prompt", "", "", "", "", "r", True)
    assert len(repo.list_history("a", limit=3)) == 3
    assert len(repo.list_history("a", limit=10)) == 5


def test_list_history_unknown_agent_returns_empty(tmp_db):
    """未知 agent:list_history 返回空 list,不抛异常。"""
    repo = _make_repo(tmp_db)
    assert repo.list_history("nonexistent") == []
