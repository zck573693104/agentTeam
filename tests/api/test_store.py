import pytest

from agentteam.api.store import TeamStore
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def _make_team(name="dev") -> Team:
    return Team(
        name=name,
        description="test",
        leader=Leader(system_prompt="x"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="x")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )


def test_register_and_get():
    store = TeamStore()
    team = _make_team()
    store.register(team)
    assert store.get("dev") is team


def test_get_nonexistent_returns_none():
    store = TeamStore()
    assert store.get("nope") is None


def test_list_all_returns_all_registered():
    store = TeamStore()
    store.register(_make_team("a"))
    store.register(_make_team("b"))
    names = sorted(t.name for t in store.list_all())
    assert names == ["a", "b"]


def test_delete_removes_team():
    store = TeamStore()
    store.register(_make_team("dev"))
    assert store.delete("dev") is True
    assert store.get("dev") is None


def test_delete_nonexistent_returns_false():
    store = TeamStore()
    assert store.delete("nope") is False


def test_register_overwrites_existing():
    store = TeamStore()
    store.register(_make_team("dev"))
    team2 = _make_team("dev")
    team2.description = "updated"
    store.register(team2)
    assert store.get("dev").description == "updated"
