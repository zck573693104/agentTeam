"""SP6-P1 Plan DAG 测试。"""
from agentteam.runtime.nodes import Plan, PlanStep


def test_plan_step_new_fields_default_values():
    """PlanStep 新字段默认值符合向后兼容(旧代码不传新字段仍可构造)。"""
    step = PlanStep(worker="coder", instruction="写代码")
    assert step.id == ""
    assert step.depends_on == []
    assert step.condition is None
    # 旧字段仍存在
    assert step.worker == "coder"
    assert step.instruction == "写代码"


def test_plan_execution_mode_defaults_sequential():
    """Plan.execution_mode 默认 'sequential'(向后兼容)。"""
    plan = Plan(steps=[PlanStep(worker="w1", instruction="do x")])
    assert plan.execution_mode == "sequential"


def test_plan_step_with_depends_on_and_condition():
    """构造带 depends_on + condition 的 PlanStep。"""
    step = PlanStep(
        worker="reviewer",
        instruction="审查代码",
        id="step_review",
        depends_on=["step_code", "step_test"],
        condition="len(worker_outputs) >= 2",
    )
    assert step.id == "step_review"
    assert step.depends_on == ["step_code", "step_test"]
    assert step.condition == "len(worker_outputs) >= 2"


def test_set_union_reducer_merges_sets():
    """set_union reducer 合并两个 set(left ∪ right)。"""
    from agentteam.runtime.state import set_union
    left = {"a", "b"}
    right = {"b", "c"}
    result = set_union(left, right)
    assert result == {"a", "b", "c"}


def test_set_union_reducer_with_empty():
    """set_union 与空 set 合并返回原集合副本。"""
    from agentteam.runtime.state import set_union
    assert set_union({"a"}, set()) == {"a"}
    assert set_union(set(), {"b"}) == {"b"}
    assert set_union(set(), set()) == set()


def test_team_state_has_completed_and_skipped_steps_fields():
    """TeamState TypedDict 包含 completed_steps/skipped_steps/execution_mode 字段。"""
    from agentteam.runtime.state import TeamState
    # TypedDict 的 __annotations__ 包含所有声明的字段
    annotations = TeamState.__annotations__
    assert "completed_steps" in annotations
    assert "skipped_steps" in annotations
    assert "execution_mode" in annotations


def test_worker_state_has_current_step_id_field():
    """WorkerState 包含 current_step_id 字段(dag 模式用)。"""
    from agentteam.runtime.state import WorkerState
    annotations = WorkerState.__annotations__
    assert "current_step_id" in annotations
