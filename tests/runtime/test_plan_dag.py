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
