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
