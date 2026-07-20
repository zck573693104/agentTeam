"""P-A4 Token 配额:quotas 表读写 + check_quota 校验测试。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from agentteam.storage.quotas import QuotaRepo
from agentteam.storage.runs import RunRepo


def _seed_run(conn: sqlite3.Connection, team_name: str = "t1") -> str:
    return RunRepo(conn).create_run(team_name=team_name, task="x")


def test_upsert_and_get(tmp_db: sqlite3.Connection):
    repo = QuotaRepo(tmp_db)
    assert repo.get("t1") is None  # 未配置
    repo.upsert("t1", token_limit=1000, period_seconds=3600, description="test")
    q = repo.get("t1")
    assert q["team_name"] == "t1"
    assert q["token_limit"] == 1000
    assert q["period_seconds"] == 3600
    assert q["description"] == "test"
    assert q["created_at"] == q["updated_at"]


def test_upsert_is_idempotent_update(tmp_db: sqlite3.Connection):
    """upsert 同名 team 应更新而非插入新行。"""
    repo = QuotaRepo(tmp_db)
    repo.upsert("t1", token_limit=1000)
    repo.upsert("t1", token_limit=2000, description="updated")
    q = repo.get("t1")
    assert q["token_limit"] == 2000
    assert q["description"] == "updated"
    all_quotas = repo.list_all()
    assert len(all_quotas) == 1  # 没有重复


def test_list_all_orders_by_team_name(tmp_db: sqlite3.Connection):
    repo = QuotaRepo(tmp_db)
    repo.upsert("zeta", token_limit=100)
    repo.upsert("alpha", token_limit=200)
    repo.upsert("mid", token_limit=300)
    teams = [q["team_name"] for q in repo.list_all()]
    assert teams == ["alpha", "mid", "zeta"]


def test_delete(tmp_db: sqlite3.Connection):
    repo = QuotaRepo(tmp_db)
    repo.upsert("t1", token_limit=1000)
    assert repo.delete("t1") is True
    assert repo.get("t1") is None
    # 二次删除返回 False
    assert repo.delete("t1") is False


def test_check_quota_no_config_allows(tmp_db: sqlite3.Connection):
    """未配置 quota 的 team 默认放行(limit=0=不限)。"""
    repo = QuotaRepo(tmp_db)
    check = repo.check_quota("t1")
    assert check["allowed"] is True
    assert check["used"] == 0
    assert check["limit"] == 0
    assert check["period"] == 0
    assert check["team_name"] == "t1"


def test_check_quota_zero_limit_allows(tmp_db: sqlite3.Connection):
    """token_limit=0 显式表示不限。"""
    repo = QuotaRepo(tmp_db)
    repo.upsert("t1", token_limit=0)
    check = repo.check_quota("t1")
    assert check["allowed"] is True
    assert check["limit"] == 0


def test_check_quota_within_limit_allows(tmp_db: sqlite3.Connection):
    """已用 < limit 允许。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=1000, period_seconds=86400)
    # 插入一条已完成 run,用了 500 token
    run_id = run_repo.create_run("t1", "task")
    run_repo.end_run(run_id, "completed", total_tokens=500)
    check = repo.check_quota("t1")
    assert check["allowed"] is True
    assert check["used"] == 500
    assert check["limit"] == 1000


def test_check_quota_at_limit_blocks(tmp_db: sqlite3.Connection):
    """used >= limit 拒绝。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=1000, period_seconds=86400)
    run_id = run_repo.create_run("t1", "task")
    run_repo.end_run(run_id, "completed", total_tokens=1000)
    check = repo.check_quota("t1")
    assert check["allowed"] is False
    assert check["used"] == 1000


def test_check_quota_over_limit_blocks(tmp_db: sqlite3.Connection):
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=500, period_seconds=86400)
    run_id = run_repo.create_run("t1", "task")
    run_repo.end_run(run_id, "completed", total_tokens=999)
    check = repo.check_quota("t1")
    assert check["allowed"] is False


def test_check_quota_sums_multiple_runs(tmp_db: sqlite3.Connection):
    """多次 run 的 token 累加。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=1000, period_seconds=86400)
    for tokens in (300, 400, 200):
        rid = run_repo.create_run("t1", "x")
        run_repo.end_run(rid, "completed", total_tokens=tokens)
    check = repo.check_quota("t1")
    assert check["used"] == 900
    assert check["allowed"] is True


def test_check_quota_filters_by_team(tmp_db: sqlite3.Connection):
    """不同 team 的 token 互不影响。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=1000)
    repo.upsert("t2", token_limit=1000)
    r1 = run_repo.create_run("t1", "x")
    run_repo.end_run(r1, "completed", total_tokens=800)
    r2 = run_repo.create_run("t2", "x")
    run_repo.end_run(r2, "completed", total_tokens=500)
    assert repo.check_quota("t1")["used"] == 800
    assert repo.check_quota("t2")["used"] == 500


def test_check_quota_ignores_old_runs_outside_window(tmp_db: sqlite3.Connection):
    """周期窗口外的 run 不计入。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    # 1 小时窗口
    repo.upsert("t1", token_limit=1000, period_seconds=3600)

    # 手动塞一条 2 小时前的 run
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    run_id = run_repo.create_run("t1", "x")
    tmp_db.execute(
        "UPDATE runs SET status='completed', ended_at=?, total_tokens=999 WHERE id=?",
        (old_time, run_id),
    )
    tmp_db.commit()
    check = repo.check_quota("t1")
    # 旧 run 在窗口外,不计入
    assert check["used"] == 0
    assert check["allowed"] is True


def test_check_quota_ignores_running_runs(tmp_db: sqlite3.Connection):
    """未结束的 run(ended_at IS NULL)不计入。"""
    repo = QuotaRepo(tmp_db)
    run_repo = RunRepo(tmp_db)
    repo.upsert("t1", token_limit=1000, period_seconds=86400)
    # 创建但未 end_run
    run_repo.create_run("t1", "x")
    check = repo.check_quota("t1")
    assert check["used"] == 0
    assert check["allowed"] is True
