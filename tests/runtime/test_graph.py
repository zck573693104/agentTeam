from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler, route_from_plan, route_from_review
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _make_team():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    coder = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师", model=ModelRef("qwen", "worker-model"),
    )
    tester = Worker(
        name="tester", role="测试员", description="写测试",
        system_prompt="你是测试员", model=ModelRef("qwen", "worker-model"),
    )
    return Team(
        name="dev", description="开发小队", leader=leader,
        workers=[coder, tester], default_model=ModelRef("qwen", "qwen-max"),
    )


def test_route_from_plan_returns_first_worker():
    state = {"plan": [{"worker": "coder", "instruction": "x", "status": "pending"}]}
    assert route_from_plan(state) == "worker_coder"


def test_route_from_plan_empty_plan_ends():
    assert route_from_plan({"plan": []}) == END


def test_route_from_review_continues():
    state = {
        "plan": [
            {"worker": "coder", "instruction": "x", "status": "done"},
            {"worker": "tester", "instruction": "y", "status": "pending"},
        ],
        "current_step": 1,
    }
    assert route_from_review(state) == "worker_tester"


def test_route_from_review_ends_when_done():
    state = {
        "plan": [{"worker": "coder", "instruction": "x", "status": "done"}],
        "current_step": 1,
    }
    assert route_from_review(state) == END


def test_team_compiler_produces_runnable_graph():
    team = _make_team()
    leader_llm = FakeLLM()
    worker_llm = FakeLLM()
    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team)

    # 编译产物应有 nodes 属性
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    assert "worker_coder" in node_names
    assert "worker_tester" in node_names


def test_end_to_end_run_two_steps():
    """Leader 拆 2 步 → coder 执行 → review → tester 执行 → review → 结束。"""
    team = _make_team()

    # Leader LLM：1 次结构化输出（拆计划）+ 2 次 invoke（点评）
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="写 hello world"),
        PlanStep(worker="tester", instruction="写测试"),
    ])])
    leader_llm.set_invoke_responses([
        AIMessage(content="coder 干得不错"),
        AIMessage(content="tester 测试到位，全部完成"),
    ])

    # Worker LLM：coder 1 次 + tester 1 次（都直接给最终答案，不调工具）
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
        config={"configurable": {"thread_id": "t1"}},
    )

    # 计划 2 步全部完成
    assert len(result["plan"]) == 2
    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "done"

    # 两个 worker 都有产出
    assert result["worker_outputs"]["coder"] == "print('hello world')"
    assert result["worker_outputs"]["tester"] == "assert True"

    # current_step 推进到末尾
    assert result["current_step"] == 2

    # 消息历史包含 leader_plan + coder + review + tester + review
    assert len(result["messages"]) >= 5

    # audit_events 累积：leader_plan + worker_end×2 + leader_review×2 = 5
    assert len(result["audit_events"]) == 5


def test_end_to_end_empty_plan_ends_immediately():
    """Leader 拆出空计划 → 立即结束。"""
    team = _make_team()

    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[])])

    provider = FakeModelProvider({"leader-model": leader_llm, "worker-model": FakeLLM()})
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "啥也不用做"},
        config={"configurable": {"thread_id": "t2"}},
    )

    assert result["plan"] == []
    assert result["worker_outputs"] == {}


def test_compile_with_step_policy_adds_step_gate(fake_llm):
    """有 step 级策略时，编译出的图包含 step_gate 节点。"""
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test", approval_policy=ApprovalPolicy(level="step")),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "step_gate" in node_names


def test_compile_without_policy_has_no_gates(fake_llm):
    """无策略时，编译出的图不含任何 gate 节点（与 M2 一致）。"""
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "step_gate" not in node_names
    assert not any(n.startswith("worker_gate") for n in node_names)


def test_compile_with_worker_policy_adds_worker_gate(fake_llm):
    """有 worker 级策略时，编译出的图包含 worker_gate 节点。"""
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[
            Worker(
                name="w1",
                role="r",
                description="",
                system_prompt="test",
                approval_policy=ApprovalPolicy(level="worker"),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "worker_gate_w1" in node_names


def test_compile_accepts_trace_writer_and_audit_repo(fake_llm, fake_trace_writer, tmp_db):
    """compile 接受 trace_writer 和 audit_repo 参数。"""
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.storage.audit import AuditRepo
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    audit_repo = AuditRepo(tmp_db)
    graph = compiler.compile(
        team, trace_writer=fake_trace_writer, audit_repo=audit_repo
    )
    assert graph is not None
