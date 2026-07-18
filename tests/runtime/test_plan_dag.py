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


def test_make_route_from_plan_dag_returns_ready_steps():
    """dag 路由: A/B 无依赖 ready, C 依赖 A+B 不 ready。返回 [A_target, B_target]。"""
    from agentteam.runtime.graph import make_route_from_plan_dag
    child_targets = {"a": "worker_a", "b": "worker_b", "c": "worker_c"}
    route = make_route_from_plan_dag(child_targets)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a", "depends_on": []},
            {"worker": "b", "instruction": "do B", "id": "step_b", "depends_on": []},
            {"worker": "c", "instruction": "do C", "id": "step_c",
             "depends_on": ["step_a", "step_b"]},
        ],
        "completed_steps": set(),
        "skipped_steps": set(),
    }
    result = route(state)
    # A/B ready(无依赖),C 不 ready(依赖未满足)
    assert set(result) == {"worker_a", "worker_b"}
    assert "worker_c" not in result


def test_make_route_from_plan_dag_completes_and_ends():
    """dag 路由: 所有 step 已完成 → 返回 [END]。"""
    from langgraph.graph import END
    from agentteam.runtime.graph import make_route_from_plan_dag
    child_targets = {"a": "worker_a"}
    route = make_route_from_plan_dag(child_targets)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a", "depends_on": []},
        ],
        "completed_steps": {"step_a"},
        "skipped_steps": set(),
    }
    result = route(state)
    assert result == [END]


def test_make_route_from_plan_dag_skips_condition_false():
    """dag 路由: condition 求值 False 的 step 被加入 skipped,不返回。"""
    from agentteam.runtime.graph import make_route_from_plan_dag
    child_targets = {"a": "worker_a", "b": "worker_b"}
    route = make_route_from_plan_dag(child_targets)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a",
             "depends_on": [], "condition": "False"},
            {"worker": "b", "instruction": "do B", "id": "step_b", "depends_on": []},
        ],
        "completed_steps": set(),
        "skipped_steps": set(),
    }
    result = route(state)
    # B ready,A 被 condition 跳过
    assert result == ["worker_b"]
    # A 被加入 skipped_steps(in-place mutation)
    assert "step_a" in state["skipped_steps"]


def test_make_route_from_plan_dag_condition_true_executes():
    """dag 路由: condition 求值 True 的 step 正常返回。"""
    from agentteam.runtime.graph import make_route_from_plan_dag
    child_targets = {"a": "worker_a"}
    route = make_route_from_plan_dag(child_targets)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a",
             "depends_on": [], "condition": "len(completed_steps) >= 0"},
        ],
        "completed_steps": set(),
        "skipped_steps": set(),
    }
    result = route(state)
    assert result == ["worker_a"]


def test_make_route_from_plan_dag_dep_on_skipped_treated_satisfied():
    """dag 路由: 依赖已 skipped 的 step 视为依赖满足(skipped 等价 completed)。"""
    from agentteam.runtime.graph import make_route_from_plan_dag
    child_targets = {"b": "worker_b"}
    route = make_route_from_plan_dag(child_targets)
    state = {
        "plan": [
            {"worker": "b", "instruction": "do B", "id": "step_b",
             "depends_on": ["step_a"]},
        ],
        "completed_steps": set(),
        "skipped_steps": {"step_a"},
    }
    result = route(state)
    assert result == ["worker_b"]


def test_detect_dag_cycle_returns_true_for_circular_deps():
    """循环依赖检测: A→B→A 返回 True。"""
    from agentteam.runtime.graph import _detect_dag_cycle
    plan = [
        {"worker": "a", "id": "step_a", "depends_on": ["step_b"]},
        {"worker": "b", "id": "step_b", "depends_on": ["step_a"]},
    ]
    assert _detect_dag_cycle(plan) is True


def test_detect_dag_cycle_returns_false_for_acyclic():
    """无环 DAG: A→B→C 返回 False。"""
    from agentteam.runtime.graph import _detect_dag_cycle
    plan = [
        {"worker": "a", "id": "step_a", "depends_on": []},
        {"worker": "b", "id": "step_b", "depends_on": ["step_a"]},
        {"worker": "c", "id": "step_c", "depends_on": ["step_b"]},
    ]
    assert _detect_dag_cycle(plan) is False


def test_detect_dag_cycle_returns_false_for_diamond():
    """菱形依赖 A→B,A→C,B→D,C→D 无环。"""
    from agentteam.runtime.graph import _detect_dag_cycle
    plan = [
        {"worker": "a", "id": "step_a", "depends_on": []},
        {"worker": "b", "id": "step_b", "depends_on": ["step_a"]},
        {"worker": "c", "id": "step_c", "depends_on": ["step_a"]},
        {"worker": "d", "id": "step_d", "depends_on": ["step_b", "step_c"]},
    ]
    assert _detect_dag_cycle(plan) is False


def test_eval_condition_returns_false_on_exception():
    """_eval_condition 异常时返回 False(宁可跳过)。"""
    from agentteam.runtime.graph import _eval_condition
    state = {"worker_outputs": {}}
    assert _eval_condition("undefined_var > 0", state) is False
    assert _eval_condition("1 / 0 > 0", state) is False
    assert _eval_condition("import os", state) is False


def test_eval_condition_reads_state_fields():
    """_eval_condition 可读 state 中的 worker_outputs/completed_steps 等字段。"""
    from agentteam.runtime.graph import _eval_condition
    state = {"worker_outputs": {"a": "x", "b": "y"}, "completed_steps": {"step_a"}}
    assert _eval_condition("len(worker_outputs) >= 2", state) is True
    assert _eval_condition("len(worker_outputs) >= 3", state) is False
    assert _eval_condition("'step_a' in completed_steps", state) is True
