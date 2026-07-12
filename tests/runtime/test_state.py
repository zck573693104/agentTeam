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


def test_state_supports_run_id():
    """TeamState 包含 run_id 字段。"""
    state: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": None,
    }
    assert state["run_id"] == "run-001"


def test_state_supports_pending_approval():
    """TeamState 包含 pending_approval 字段，可为 None 或 dict。"""
    state_none: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": None,
    }
    assert state_none["pending_approval"] is None

    state_rejected: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": {"gate": "step", "approved": False},
    }
    assert state_rejected["pending_approval"]["approved"] is False


def test_is_rejected_helper():
    """is_rejected 正确判断审批拒绝状态。"""
    from agentteam.runtime.state import is_rejected

    assert is_rejected({"pending_approval": None}) is False
    assert is_rejected({"pending_approval": {"approved": True}}) is False
    assert is_rejected({"pending_approval": {"approved": False}}) is True
    assert is_rejected({}) is False


def test_worker_state_typeddict_accepts_fields():
    """WorkerState 包含共享字段和 worker 内部字段。"""
    from agentteam.runtime.state import WorkerState

    state: WorkerState = {
        "messages": [],
        "plan": [],
        "current_step": 0,
        "run_id": "run-1",
        "pending_approval": None,
        "audit_events": [],
        "worker_outputs": {},
        "react_messages": [],
        "tool_calls": [],
        "iteration": 0,
        "final_answer": "",
    }
    assert state["iteration"] == 0
    assert state["final_answer"] == ""
    assert state["tool_calls"] == []
    assert state["react_messages"] == []
