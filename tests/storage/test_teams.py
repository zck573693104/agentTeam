import sqlite3

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


def _make_team(name="dev") -> Team:
    return Team(
        name=name,
        description="test team",
        root=Agent(
            name="lead", role="supervisor",
            system_prompt="plan",
            children=[Agent(name="w1", role="worker", tools=["read_file"])],
        ),
        default_model=ModelRef(provider="qwen", name="qwen-max"),
        skills=["python"],
    )


def test_team_repo_upsert_and_get(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    team = _make_team("dev")
    repo.upsert(team)

    got = repo.get("dev")
    assert got is not None
    assert got.name == "dev"
    assert got.description == "test team"
    assert got.root.name == "lead"
    assert got.root.role == "supervisor"
    assert got.root.children[0].name == "w1"
    assert got.root.children[0].tools == ["read_file"]
    assert got.default_model.provider == "qwen"
    assert got.default_model.name == "qwen-max"
    assert got.skills == ["python"]


def test_team_repo_get_missing_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    assert repo.get("nonexistent") is None
