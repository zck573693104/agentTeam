import sqlite3


def test_create_and_get_run(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="dev_team", task="写个 hello world")
    run = repo.get_run(run_id)
    assert run["team_name"] == "dev_team"
    assert run["task"] == "写个 hello world"
    assert run["status"] == "pending"
    assert run["total_tokens"] == 0


def test_update_status(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="t", task="x")
    repo.update_status(run_id, "running")
    assert repo.get_run(run_id)["status"] == "running"


def test_end_run_sets_ended_at_and_tokens(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="t", task="x")
    repo.end_run(run_id, status="completed", total_tokens=1234)
    run = repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None
    assert run["total_tokens"] == 1234


def test_list_runs(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    a = repo.create_run(team_name="t", task="1")
    b = repo.create_run(team_name="t", task="2")
    runs = repo.list_runs()
    assert len(runs) == 2
    assert {r["id"] for r in runs} == {a, b}


def test_get_missing_run_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    assert repo.get_run("nonexistent") is None
