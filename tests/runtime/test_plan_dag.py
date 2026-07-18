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


def test_leader_plan_dag_mode_initializes_completed_steps(fake_llm):
    """leader_plan dag 模式:初始化 completed_steps/skipped_steps 为空 set,不写 current_step。"""
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_plan_node

    plan = Plan(
        execution_mode="dag",
        steps=[
            PlanStep(worker="a", instruction="do A", id="step_a"),
            PlanStep(worker="b", instruction="do B", id="step_b",
                     depends_on=["step_a"]),
        ],
    )
    fake_llm.set_structured_responses([plan])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    result = node({"task": "dag task", "messages": []})

    # dag 模式字段
    assert result["execution_mode"] == "dag"
    assert result["completed_steps"] == set()
    assert result["skipped_steps"] == set()
    # dag 模式不写 current_step
    assert "current_step" not in result
    # plan 含 id/depends_on/condition 字段
    assert result["plan"][0]["id"] == "step_a"
    assert result["plan"][1]["depends_on"] == ["step_a"]


def test_leader_plan_sequential_mode_keeps_current_step(fake_llm):
    """leader_plan sequential 模式:沿用 current_step=0,不写 completed_steps。"""
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_plan_node

    plan = Plan(
        execution_mode="sequential",
        steps=[PlanStep(worker="w1", instruction="do x")],
    )
    fake_llm.set_structured_responses([plan])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    result = node({"task": "seq task", "messages": []})

    assert result["execution_mode"] == "sequential"
    assert result["current_step"] == 0
    # sequential 模式不写 completed_steps/skipped_steps
    assert "completed_steps" not in result
    assert "skipped_steps" not in result


def test_leader_plan_dag_mode_detects_cycle_raises(fake_llm):
    """leader_plan dag 模式:LLM 输出循环依赖 plan → 抛 ValueError。"""
    import pytest
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_plan_node

    plan = Plan(
        execution_mode="dag",
        steps=[
            PlanStep(worker="a", instruction="do A", id="step_a",
                     depends_on=["step_b"]),
            PlanStep(worker="b", instruction="do B", id="step_b",
                     depends_on=["step_a"]),
        ],
    )
    fake_llm.set_structured_responses([plan])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    with pytest.raises(ValueError, match="circular dependency|cycle"):
        node({"task": "bad dag", "messages": []})


def test_leader_review_dag_mode_does_not_advance_current_step(fake_llm):
    """leader_review dag 模式:不推进 current_step(completed_steps 已由 worker 通过 reducer 更新)。"""
    from langchain_core.messages import AIMessage
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_review_node

    fake_llm.set_invoke_responses([AIMessage(content="A done")])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_review_node(leader, fake_llm)

    state = {
        "task": "dag task",
        "messages": [],
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a",
             "depends_on": [], "status": "running"},
            {"worker": "b", "instruction": "do B", "id": "step_b",
             "depends_on": ["step_a"], "status": "pending"},
        ],
        "current_step": 0,
        "execution_mode": "dag",
        "completed_steps": {"step_a"},  # 已由 worker 通过 reducer 更新
        "skipped_steps": set(),
        "worker_outputs": {"a": "result A"},
        "audit_events": [],
    }
    result = node(state)

    # dag 模式:不写 current_step(不推进)
    assert "current_step" not in result
    # dag 模式:不写 completed_steps(已由 worker reducer 更新,避免覆盖)
    assert "completed_steps" not in result
    # 仍有点评消息
    assert len(result["messages"]) == 1
    assert len(result["audit_events"]) == 1


def test_leader_review_sequential_mode_advances_current_step(fake_llm):
    """leader_review sequential 模式:沿用 current_step += 1。"""
    from langchain_core.messages import AIMessage
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_review_node

    fake_llm.set_invoke_responses([AIMessage(content="good")])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_review_node(leader, fake_llm)

    state = {
        "task": "seq task",
        "messages": [],
        "plan": [
            {"worker": "coder", "instruction": "写代码", "status": "running"},
            {"worker": "tester", "instruction": "写测试", "status": "pending"},
        ],
        "current_step": 0,
        "execution_mode": "sequential",
        "worker_outputs": {"coder": "print('hi')"},
        "audit_events": [],
    }
    result = node(state)

    # sequential 模式:推进 current_step,标记 plan[0] done
    assert result["current_step"] == 1
    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "pending"


def test_init_worker_dag_mode_finds_ready_step(fake_llm):
    """init_worker dag 模式:从 plan 中找到本 worker 的 ready step,设置 current_step_id。"""
    from langchain_core.messages import HumanMessage, SystemMessage
    from agentteam.domain.worker import Worker
    from agentteam.runtime.nodes import make_init_worker

    worker = Worker(name="b", role="r", description="", system_prompt="test")
    node = make_init_worker(worker)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a", "depends_on": []},
            {"worker": "b", "instruction": "do B", "id": "step_b",
             "depends_on": ["step_a"]},
        ],
        "execution_mode": "dag",
        "completed_steps": {"step_a"},  # step_a 已完成,step_b ready
        "skipped_steps": set(),
        "run_id": "r1",
    }
    result = node(state)

    # 找到 step_b 作为 current_step_id
    assert result["current_step_id"] == "step_b"
    # react_messages 用 step_b 的 instruction
    assert len(result["react_messages"]) == 2
    assert isinstance(result["react_messages"][1], HumanMessage)
    assert result["react_messages"][1].content == "do B"


def test_init_worker_sequential_mode_uses_current_step(fake_llm):
    """init_worker sequential 模式:沿用 plan[current_step]。"""
    from langchain_core.messages import HumanMessage
    from agentteam.domain.worker import Worker
    from agentteam.runtime.nodes import make_init_worker

    worker = Worker(name="coder", role="r", description="", system_prompt="test")
    node = make_init_worker(worker)
    state = {
        "plan": [
            {"worker": "coder", "instruction": "写 hello", "status": "pending"},
        ],
        "current_step": 0,
        "execution_mode": "sequential",
    }
    result = node(state)
    # sequential 模式不设 current_step_id(或设为空)
    assert result.get("current_step_id", "") == ""
    assert result["react_messages"][1].content == "写 hello"


def test_finalize_dag_mode_returns_completed_steps(fake_llm):
    """finalize dag 模式:回传 completed_steps={current_step_id}(set_union reducer 合并)。"""
    from agentteam.domain.worker import Worker
    from agentteam.runtime.nodes import make_finalize

    worker = Worker(name="a", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "result A",
        "react_messages": [],
        "run_id": "r1",
        "execution_mode": "dag",
        "current_step_id": "step_a",
    }
    result = node(state)

    # dag 模式回传 completed_steps(set,经 set_union reducer 合并到父图)
    assert result["completed_steps"] == {"step_a"}
    # 仍有 worker_outputs 与 messages
    assert result["worker_outputs"] == {"a": "result A"}
    assert len(result["messages"]) == 1


def test_finalize_sequential_mode_no_completed_steps(fake_llm):
    """finalize sequential 模式:不回传 completed_steps。"""
    from agentteam.domain.worker import Worker
    from agentteam.runtime.nodes import make_finalize

    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "done",
        "react_messages": [],
        "run_id": "r1",
        "execution_mode": "sequential",
    }
    result = node(state)
    # sequential 模式不回传 completed_steps
    assert "completed_steps" not in result
    assert result["worker_outputs"] == {"w1": "done"}


def test_init_worker_dag_mode_raises_when_no_ready_step(fake_llm):
    """init_worker dag 模式:本 worker 无 ready step(不在 plan 或全部 completed) → 抛 ValueError。"""
    import pytest
    from agentteam.domain.worker import Worker
    from agentteam.runtime.nodes import make_init_worker

    worker = Worker(name="orphan", role="r", description="", system_prompt="test")
    node = make_init_worker(worker)
    state = {
        "plan": [
            {"worker": "a", "instruction": "do A", "id": "step_a", "depends_on": []},
        ],
        "execution_mode": "dag",
        "completed_steps": set(),
        "skipped_steps": set(),
        "run_id": "r1",
    }
    with pytest.raises(ValueError, match="no ready step"):
        node(state)


def test_leader_plan_dag_mode_detects_duplicate_ids_raises(fake_llm):
    """leader_plan dag 模式:LLM 对同一 worker 产多步且无显式 id → id 冲突抛 ValueError。"""
    import pytest
    from agentteam.domain.team import Leader
    from agentteam.runtime.nodes import make_leader_plan_node

    plan = Plan(
        execution_mode="dag",
        steps=[
            PlanStep(worker="a", instruction="do A first"),  # id 默认为 "a"
            PlanStep(worker="a", instruction="do A again"),  # id 又是 "a" → 冲突
        ],
    )
    fake_llm.set_structured_responses([plan])
    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    with pytest.raises(ValueError, match="duplicate step ids"):
        node({"task": "bad dag", "messages": []})


def test_plan_dag_parallel_execution_e2e():
    """E2E dag: A/B 无依赖并行, C 依赖 A+B。A/B 并行触发, C 在 A/B 完成后触发。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeLLM, FakeModelProvider

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    worker_a = Worker(
        name="a", role="r", description="", system_prompt="A",
        model=ModelRef("qwen", "worker-model"),
    )
    worker_b = Worker(
        name="b", role="r", description="", system_prompt="B",
        model=ModelRef("qwen", "worker-model"),
    )
    worker_c = Worker(
        name="c", role="r", description="", system_prompt="C",
        model=ModelRef("qwen", "worker-model"),
    )
    team = Team(
        name="dag_team", description="dag test",
        leader=leader, workers=[worker_a, worker_b, worker_c],
        default_model=ModelRef("qwen", "qwen-max"),
    )

    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(
        execution_mode="dag",
        steps=[
            PlanStep(worker="a", instruction="do A", id="step_a"),
            PlanStep(worker="b", instruction="do B", id="step_b"),
            PlanStep(worker="c", instruction="do C", id="step_c",
                     depends_on=["step_a", "step_b"]),
        ],
    )])
    # 2 次 review(A+B 完成后, C 完成后)
    leader_llm.set_invoke_responses([
        AIMessage(content="A/B done"),
        AIMessage(content="C done, all complete"),
    ])

    # worker LLM: A/B/C 各 1 次直接答案
    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([
        AIMessage(content="result A"),
        AIMessage(content="result B"),
        AIMessage(content="result C"),
    ])

    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "do ABC in dag"},
        config={"configurable": {"thread_id": "dag-e2e-1"}},
    )

    # dag 模式
    assert result["execution_mode"] == "dag"
    # 3 步全部完成
    assert "step_a" in result["completed_steps"]
    assert "step_b" in result["completed_steps"]
    assert "step_c" in result["completed_steps"]
    # 3 个 worker 都有产出
    assert result["worker_outputs"]["a"] == "result A"
    assert result["worker_outputs"]["b"] == "result B"
    assert result["worker_outputs"]["c"] == "result C"


def test_plan_dag_condition_skip_e2e():
    """E2E dag: B 的 condition=False 被跳过, C(依赖 B)仍执行(skipped 视为满足)。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeLLM, FakeModelProvider

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    worker_a = Worker(
        name="a", role="r", description="", system_prompt="A",
        model=ModelRef("qwen", "worker-model"),
    )
    worker_b = Worker(
        name="b", role="r", description="", system_prompt="B",
        model=ModelRef("qwen", "worker-model"),
    )
    worker_c = Worker(
        name="c", role="r", description="", system_prompt="C",
        model=ModelRef("qwen", "worker-model"),
    )
    team = Team(
        name="dag_cond_team", description="dag condition test",
        leader=leader, workers=[worker_a, worker_b, worker_c],
        default_model=ModelRef("qwen", "qwen-max"),
    )

    leader_llm = FakeLLM()
    # B 的 condition 永远 False
    leader_llm.set_structured_responses([Plan(
        execution_mode="dag",
        steps=[
            PlanStep(worker="a", instruction="do A", id="step_a"),
            PlanStep(worker="b", instruction="do B", id="step_b",
                     condition="False"),
            PlanStep(worker="c", instruction="do C", id="step_c",
                     depends_on=["step_b"]),
        ],
    )])
    # 2 次 review(A 完成后, C 完成后)
    leader_llm.set_invoke_responses([
        AIMessage(content="A done"),
        AIMessage(content="C done"),
    ])

    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([
        AIMessage(content="result A"),
        AIMessage(content="result C"),
    ])

    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "dag with condition"},
        config={"configurable": {"thread_id": "dag-cond-1"}},
    )

    assert result["execution_mode"] == "dag"
    # A 和 C 完成
    assert "step_a" in result["completed_steps"]
    assert "step_c" in result["completed_steps"]
    # B 被跳过
    assert "step_b" in result["skipped_steps"]
    # B 未执行(worker_outputs 无 b)
    assert "b" not in result.get("worker_outputs", {})
    # A 和 C 有产出
    assert result["worker_outputs"]["a"] == "result A"
    assert result["worker_outputs"]["c"] == "result C"


def test_plan_sequential_backward_compat_e2e():
    """E2E sequential: 默认模式行为与旧版完全一致(current_step 线性推进)。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeLLM, FakeModelProvider

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    coder = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师", model=ModelRef("qwen", "worker-model"),
    )
    tester = Worker(
        name="tester", role="测试员", description="写测试",
        system_prompt="你是测试员", model=ModelRef("qwen", "worker-model"),
    )
    team = Team(
        name="seq_team", description="seq backward compat",
        leader=leader, workers=[coder, tester],
        default_model=ModelRef("qwen", "qwen-max"),
    )

    leader_llm = FakeLLM()
    # 默认 execution_mode="sequential"(不显式设置)
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="写 hello world"),
        PlanStep(worker="tester", instruction="写测试"),
    ])])
    leader_llm.set_invoke_responses([
        AIMessage(content="coder 干得不错"),
        AIMessage(content="tester 测试到位,全部完成"),
    ])

    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([
        AIMessage(content="print('hello world')"),
        AIMessage(content="assert True"),
    ])

    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "开发 hello world"},
        config={"configurable": {"thread_id": "seq-e2e-1"}},
    )

    # execution_mode 默认 sequential(直接索引,缺失字段会抛 KeyError 暴露回归)
    assert result["execution_mode"] == "sequential"
    # current_step 推进到末尾(旧版行为)
    assert result["current_step"] == 2
    # 计划 2 步全部 done(旧版行为)
    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "done"
    # 两个 worker 都有产出
    assert result["worker_outputs"]["coder"] == "print('hello world')"
    assert result["worker_outputs"]["tester"] == "assert True"
    # completed_steps 在 sequential 模式下不被写入(或为空)
    assert not result.get("completed_steps", set())
