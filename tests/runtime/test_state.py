from agentteam.runtime.state import TeamState, Step, merge_dicts


def test_merge_dicts_disjoint():
    assert merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}


def test_merge_dicts_right_wins():
    assert merge_dicts({"a": "1"}, {"a": "2"}) == {"a": "2"}


def test_merge_dicts_empty():
    assert merge_dicts({}, {"a": "1"}) == {"a": "1"}
    assert merge_dicts({"a": "1"}, {}) == {"a": "1"}


def test_step_typeddict_accepts_fields():
    step: Step = {"worker": "coder", "instruction": "写代码", "status": "pending"}
    assert step["worker"] == "coder"
    assert step["status"] == "pending"


def test_team_state_typeddict_accepts_fields():
    state: TeamState = {
        "messages": [],
        "task": "开发 hello world",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
    }
    assert state["task"] == "开发 hello world"
    assert state["current_step"] == 0
