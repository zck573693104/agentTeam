"""Agent / TeamRef dataclass 单元测试。"""
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


def test_team_ref_basic():
    ref = TeamRef(name="dev_subteam")
    assert ref.name == "dev_subteam"
    assert ref.alias is None


def test_team_ref_with_alias():
    ref = TeamRef(name="dev_subteam", alias="qa")
    assert ref.alias == "qa"


def test_agent_worker_defaults():
    a = Agent(name="coder", role="worker")
    assert a.name == "coder"
    assert a.role == "worker"
    assert a.system_prompt == ""
    assert a.model is None
    assert a.children == []
    assert a.approval_policy is None
    assert a.tools == []
    assert a.max_iterations == 10
    assert a.ref is None


def test_agent_supervisor_with_children():
    child = Agent(name="w1", role="worker", tools=["read_file"])
    parent = Agent(
        name="lead", role="supervisor",
        system_prompt="你是主管",
        children=[child],
    )
    assert parent.role == "supervisor"
    assert len(parent.children) == 1
    assert parent.children[0].name == "w1"


def test_agent_with_team_ref_child():
    ref = TeamRef(name="sub_team", alias="qa")
    parent = Agent(name="lead", role="supervisor", children=[ref])
    assert isinstance(parent.children[0], TeamRef)
    assert parent.children[0].alias == "qa"


def test_agent_with_ref_and_overrides():
    a = Agent(
        name="eng", role="worker",
        ref="library:code_engineer",
        system_prompt="override prompt",
        max_iterations=5,
    )
    assert a.ref == "library:code_engineer"
    assert a.system_prompt == "override prompt"
    assert a.max_iterations == 5


def test_agent_supervisor_with_approval_policy():
    ap = ApprovalPolicy(level="step")
    a = Agent(name="lead", role="supervisor", children=[
        Agent(name="w", role="worker")
    ], approval_policy=ap)
    assert a.approval_policy is ap


def test_agent_with_model():
    m = ModelRef("qwen", "qwen-max")
    a = Agent(name="w", role="worker", model=m)
    assert a.model is m
