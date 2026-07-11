import pytest

from agentteam.domain.approval import ApprovalPolicy


def test_worker_level_policy():
    p = ApprovalPolicy(level="worker", targets=["coder"])
    assert p.level == "worker"
    assert p.targets == ["coder"]
    assert p.timeout_seconds is None


def test_step_level_policy_defaults():
    p = ApprovalPolicy(level="step")
    assert p.targets is None
    assert p.timeout_seconds is None


def test_tool_level_policy_with_timeout():
    p = ApprovalPolicy(level="tool", targets=["write_file"], timeout_seconds=300)
    assert p.level == "tool"
    assert p.timeout_seconds == 300


def test_approval_policy_is_frozen():
    p = ApprovalPolicy(level="tool", targets=["write_file"])
    with pytest.raises(Exception):
        p.level = "worker"  # type: ignore[misc]
