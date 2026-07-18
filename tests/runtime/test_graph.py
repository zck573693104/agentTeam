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


def _make_initial_state(task="test task", run_id="run-e2e"):
    """构建 E2E 测试用的初始状态。"""
    return {
        "messages": [],
        "task": task,
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": run_id,
        "pending_approval": None,
    }


def test_e2e_step_approval_interrupt_resume(fake_llm, fake_trace_writer):
    """E2E：step 级审批 → interrupt → resume → 完成。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="task done"), AIMessage(content="good job")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-step"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 step_gate 处 interrupt
    result = graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在 step_gate 处暂停"

    # Resume：批准
    result = graph.invoke(
        Command(resume={"approved": True, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证 worker 产出
    values = state.values
    assert "w1" in values.get("worker_outputs", {})

    # 验证轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "leader_plan" in event_types
    assert "approval_requested" in event_types
    assert "approval_decided" in event_types
    assert "worker_start" in event_types
    assert "worker_end" in event_types


def test_e2e_worker_approval_interrupt_resume(fake_llm, fake_trace_writer):
    """E2E：worker 级审批 → interrupt → resume → 完成。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="task done"), AIMessage(content="good job")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                approval_policy=ApprovalPolicy(level="worker"),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-worker"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 worker_gate 处 interrupt
    result = graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在 worker_gate 处暂停"

    # Resume：批准
    result = graph.invoke(
        Command(resume={"approved": True, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    values = state.values
    assert "w1" in values.get("worker_outputs", {})


def test_e2e_step_approval_rejection_terminates(fake_llm, fake_trace_writer):
    """E2E：step 级审批被拒绝 → 图终止。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-reject"}}
    initial = _make_initial_state()

    # 第一次 invoke：interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next

    # Resume：拒绝
    graph.invoke(
        Command(resume={"approved": False, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已终止"

    # worker 不应该执行
    values = state.values
    assert "w1" not in values.get("worker_outputs", {})


def test_e2e_no_policy_runs_without_interrupt(fake_llm, fake_trace_writer):
    """E2E：无审批策略时，图直接运行完成（无 interrupt）。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-nopolicy"}}
    result = graph.invoke(_make_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该直接完成"
    assert "w1" in state.values.get("worker_outputs", {})

    # 无审批事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "approval_requested" not in event_types


def test_e2e_tool_approval_interrupt_resume(fake_llm, fake_trace_writer, tmp_path):
    """E2E：Worker 调用需审批的工具 → interrupt → resume approved → 完成。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    # 一个需要审批的工具
    target = tmp_path / "secret.txt"
    def write_secret(content: str) -> str:
        target.write_text(content, encoding="utf-8")
        return "written"

    dangerous_tool = StructuredTool.from_function(
        name="write_secret", description="写秘密文件", func=write_secret
    )

    # LLM: leader 拆计划 + 点评；worker 先调工具再给答案
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="写秘密文件")])]
    )
    fake_llm.set_invoke_responses([
        # worker 第 1 轮：调工具
        AIMessage(
            content="",
            tool_calls=[{"name": "write_secret", "args": {"content": "top secret"}, "id": "tc1", "type": "tool_call"}],
        ),
        # worker 第 2 轮：给最终答案
        AIMessage(content="文件已写入"),
        # leader 点评
        AIMessage(content="做得好"),
    ])

    reg = ToolRegistry()
    reg.register(dangerous_tool)

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["write_secret"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_secret"]),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-tool"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 tool_step 处 interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在工具级审批处暂停"

    # Resume：批准
    graph.invoke(Command(resume={"approved": True, "decider": "admin"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证工具被执行
    assert target.read_text(encoding="utf-8") == "top secret"

    # 验证 worker 产出
    values = state.values
    assert values["worker_outputs"]["w1"] == "文件已写入"

    # 验证轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "approval_requested" in event_types
    assert "approval_decided" in event_types
    assert "tool_call" in event_types
    assert "worker_end" in event_types


def test_e2e_tool_approval_rejected_skips_tool(fake_llm, fake_trace_writer):
    """E2E：工具级审批被拒绝 → 工具跳过 → Worker 继续给答案。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    executed = []
    def dangerous(x: str) -> str:
        executed.append(x)
        return "done"

    tool = StructuredTool.from_function(name="dangerous", description="d", func=dangerous)

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "dangerous", "args": {"x": "data"}, "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="好的，我换个方案"),
        AIMessage(content="完成"),
    ])

    reg = ToolRegistry()
    reg.register(tool)

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["dangerous"],
                approval_policy=ApprovalPolicy(level="tool", targets=["dangerous"]),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-tool-reject"}}
    initial = _make_initial_state()

    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "应 interrupt"

    # Resume：拒绝
    graph.invoke(Command(resume={"approved": False, "decider": "admin"}), config)
    state = graph.get_state(config)

    # 工具未执行
    assert len(executed) == 0

    # Worker 最终仍有产出（LLM 换方案后给答案）
    values = state.values
    assert "w1" in values.get("worker_outputs", {})


def test_e2e_mcp_tools_via_fake_loader(fake_llm, fake_trace_writer):
    """E2E：通过 fake MCP loader 加载工具，Worker 使用 mcp: 前缀工具。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver

    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    # fake MCP 工具
    def search(query: str) -> str:
        return f"搜索结果: {query}"

    mcp_tool = StructuredTool.from_function(name="search", description="搜索", func=search)

    fake_loader = lambda server: [mcp_tool]  # noqa: E731
    reg = ToolRegistry(mcp_loader=fake_loader)

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="搜索测试")])]
    )
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:searcher:search", "args": {"query": "hello"}, "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="搜索完成"),
        AIMessage(content="好的"),
    ])

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["mcp:searcher:search"],
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
        mcp_servers=[MCPServer(name="searcher", command="python")],
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-mcp"}}
    result = graph.invoke(_make_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该已完成"
    assert state.values["worker_outputs"]["w1"] == "搜索完成"

    # 验证 tool_call 轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "tool_call" in event_types


# ============ SP1 Task 9: 递归编译多级层级测试 ============


def test_compile_recursive_three_level_chain(fake_llm):
    """3 级 supervisor 链编译成功，node_names 包含各层节点。"""
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="3 level",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                children=[Agent(name="eng", role="worker", system_prompt="eng")],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    # ceo 的 child 是 cto（supervisor → agent_cto 节点，内部含子图）
    assert "agent_cto" in node_names


def test_compile_with_team_ref(fake_llm):
    """Team 嵌套：父 Team 引用子 Team 作为 child。"""
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            children=[Agent(name="w", role="worker")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(sub_team)
    main_team = Team(
        name="main", description="main",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            children=[TeamRef(name="sub", alias="qa")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(main_team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "subteam_qa" in node_names


def test_compile_team_ref_not_registered_raises(fake_llm):
    """TeamRef 指向未注册 Team → KeyError。"""
    import pytest
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[TeamRef(name="nonexistent")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(KeyError, match="Team not registered"):
        compiler.compile(team)


def test_compile_max_depth_exceeded_raises(fake_llm):
    """depth > MAX_DEPTH → ValueError。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    compiler.MAX_DEPTH = 3  # 测试用调小

    # 构造 4 级链
    leaf = Agent(name="w", role="worker")
    l3 = Agent(name="l3", role="supervisor", children=[leaf])
    l2 = Agent(name="l2", role="supervisor", children=[l3])
    l1 = Agent(name="l1", role="supervisor", children=[l2])
    root = Agent(name="root", role="supervisor", children=[l1])
    team = Team(
        name="t", description="deep", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="Max depth exceeded"):
        compiler.compile(team)


def test_compile_circular_team_ref_raises(fake_llm):
    """循环 TeamRef：A 引用 B，B 引用 A → ValueError。"""
    import pytest
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    # 通过 register_team 注册两个互相引用的 Team
    # 通过 TeamRef 名称引用，运行时编译期检测循环
    team_a = Team(
        name="team_a", description="a",
        root=Agent(name="la", role="supervisor",
                   children=[TeamRef(name="team_b", alias="b")]),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    team_b = Team(
        name="team_b", description="b",
        root=Agent(name="lb", role="supervisor",
                   children=[TeamRef(name="team_a", alias="a")]),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(team_a)
    compiler.register_team(team_b)
    with pytest.raises(ValueError, match="Circular team reference"):
        compiler.compile(team_a)


def test_compile_supervisor_with_tools_raises(fake_llm):
    """supervisor 不能有 tools。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(name="w", role="worker")],
            tools=["read_file"],  # 非法
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="supervisor cannot have tools"):
        compiler.compile(team)


def test_compile_worker_with_children_raises(fake_llm):
    """worker 不能有 children。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(
                name="w", role="worker",
                children=[Agent(name="x", role="worker")],  # 非法
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="worker cannot have children"):
        compiler.compile(team)


def test_compile_with_library_ref(fake_llm):
    """专家库引用：Agent(ref=...) 经 resolve 后编译。"""
    from langchain_core.tools import StructuredTool

    from agentteam.domain.agent import Agent
    from agentteam.domain.library import AgentLibrary
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template", tools=["read_file"], max_iterations=5,
    ))
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry()
    # 注册库模板中声明的 read_file 工具，否则 get_tools 会抛 KeyError
    reg.register(StructuredTool.from_function(
        name="read_file", description="read", func=lambda path: ""
    ))
    compiler = TeamCompiler(provider, reg, library=lib)
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(name="eng", role="worker", ref="library:code_engineer")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(team)
    assert graph is not None  # 编译成功
