# tests/runtime/test_approval.py
"""审批门节点的测试。"""
from __future__ import annotations

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.trace import FakeTraceWriter


# --- Step Gate: No-op cases (testable without graph) ---


def test_step_gate_no_policy_returns_empty():
    """无策略时 step_gate 是 no-op。"""
    gate = make_step_gate(None)
    result = gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
    assert result == {}


def test_step_gate_wrong_level_returns_empty():
    """策略 level 不是 step 时 no-op。"""
    policy = ApprovalPolicy(level="worker")
    gate = make_step_gate(policy)
    result = gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
    assert result == {}


def test_step_gate_no_more_steps_returns_empty():
    """无更多步骤时 step_gate no-op。"""
    policy = ApprovalPolicy(level="step")
    gate = make_step_gate(policy)
    result = gate({"run_id": "r1", "current_step": 3, "plan": [{"worker": "w1"}]})
    assert result == {}


# --- Worker Gate: No-op cases ---


def test_worker_gate_no_policy_returns_empty():
    """无策略时 worker_gate 是 no-op。"""
    gate = make_worker_gate("w1", None)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_wrong_level_returns_empty():
    """策略 level 不是 worker 时 no-op。"""
    policy = ApprovalPolicy(level="step")
    gate = make_worker_gate("w1", policy)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_target_not_in_list_returns_empty():
    """worker 不在 targets 列表中时 no-op。"""
    policy = ApprovalPolicy(level="worker", targets=["other_worker"])
    gate = make_worker_gate("w1", policy)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_target_in_list_proceeds():
    """worker 在 targets 列表中时不 no-op（会尝试 interrupt）。
    在图外调用 interrupt 会抛 RuntimeError，验证它确实进入了审批逻辑。"""
    policy = ApprovalPolicy(level="worker", targets=["w1"])
    gate = make_worker_gate("w1", policy)
    try:
        gate({"run_id": "r1"})
        assert False, "应该抛出 RuntimeError（interrupt 在图外调用）"
    except RuntimeError:
        pass  # 预期行为：说明 interrupt 被调用了


# --- Step Gate: enters interrupt ---


def test_step_gate_enters_interrupt():
    """有 step 策略且有步骤时，step_gate 调用 interrupt。"""
    policy = ApprovalPolicy(level="step")
    gate = make_step_gate(policy)
    try:
        gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
        assert False, "应该抛出 RuntimeError（interrupt 在图外调用）"
    except RuntimeError:
        pass  # 预期行为
