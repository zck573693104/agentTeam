from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def test_worker_defaults():
    w = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
    )
    assert w.model is None
    assert w.tools == []
    assert w.approval_policy is None
    assert w.max_iterations == 10


def test_worker_with_all_fields():
    w = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        model=ModelRef("qwen", "qwen-max"),
        tools=["read_file", "write_file"],
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        max_iterations=5,
    )
    assert w.model == ModelRef("qwen", "qwen-max")
    assert w.tools == ["read_file", "write_file"]
    assert w.approval_policy.level == "tool"
    assert w.max_iterations == 5


def test_worker_to_agent_basic():
    w = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师",
    )
    a = w.to_agent()
    assert a.name == "coder"
    assert a.role == "worker"
    assert a.system_prompt == "你是代码工程师"
    assert a.tools == []
    assert a.max_iterations == 10
    assert a.children == []
    assert a.ref is None


def test_worker_to_agent_preserves_all_fields():
    m = ModelRef("qwen", "qwen-max")
    ap = ApprovalPolicy(level="tool", targets=["write_file"])
    w = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师", model=m,
        tools=["read_file", "write_file"],
        approval_policy=ap, max_iterations=5,
    )
    a = w.to_agent()
    assert a.model is m
    assert a.tools == ["read_file", "write_file"]
    assert a.approval_policy is ap
    assert a.max_iterations == 5
