from langgraph.graph import END

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler, route_from_plan, route_from_review
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
