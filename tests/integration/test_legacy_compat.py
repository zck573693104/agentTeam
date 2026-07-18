"""向后兼容集成测试：旧 schema 与 dev_team.py 仍可工作。"""
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.domain.agent import Agent
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def test_dev_team_legacy_dict_parses_to_root():
    """examples/dev_team.py 的 DEV_TEAM dict 仍可解析为 Team.root。"""
    from examples.dev_team import DEV_TEAM
    team = team_from_dict(DEV_TEAM)
    assert team.root.role == "supervisor"
    assert team.root.name == "tech_lead"
    # 4 个 worker
    assert len(team.root.children) == 4
    assert all(isinstance(c, Agent) for c in team.root.children)
    assert all(c.role == "worker" for c in team.root.children)
    # 兼容 property
    assert team.leader.name == "tech_lead"
    assert len(team.workers) == 4
    worker_names = [w.name for w in team.workers]
    assert "analyst" in worker_names
    assert "coder" in worker_names
    assert "tester" in worker_names
    assert "reviewer" in worker_names


def test_team_from_legacy_roundtrip():
    """Team.from_legacy 构造的 Team，leader/workers property 反推一致。"""
    leader = Leader(name="boss", system_prompt="你是主管")
    workers = [
        Worker(name="coder", role="代码工程师", description="",
               system_prompt="你是代码工程师"),
        Worker(name="tester", role="测试员", description="",
               system_prompt="你是测试员"),
    ]
    team = Team.from_legacy(
        name="dev", description="d", leader=leader, workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.leader.name == "boss"
    assert team.leader.system_prompt == "你是主管"
    assert [w.name for w in team.workers] == ["coder", "tester"]
    # root 也是 supervisor
    assert team.root.role == "supervisor"
    assert team.root.name == "boss"


def test_legacy_team_compiles_and_runs():
    """旧 leader+workers 构造的 Team 可编译并运行。"""
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    coder = Worker(
        name="coder", role="代码工程师", description="",
        system_prompt="你是代码工程师", model=ModelRef("qwen", "worker-model"),
    )
    team = Team(
        name="dev", description="d", leader=leader, workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # leader LLM：拆 1 步 + 1 次 review
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="写代码"),
    ])])
    leader_llm.set_invoke_responses([AIMessage(content="ok")])
    # worker LLM
    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([AIMessage(content="print('hi')")])

    provider = FakeModelProvider({
        "leader-model": leader_llm, "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "legacy"}}
    initial = {
        "messages": [], "task": "写 hi", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": "r",
        "pending_approval": None, "total_tokens": 0, "path": "team:dev",
    }
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["coder"] == "print('hi')"


def test_legacy_serializer_roundtrip():
    """旧 schema dict → Team → team_to_dict → 重新 team_from_dict 仍工作。

    team_to_dict 输出新 schema（含 root），所以第二次 from_dict 走新路径。
    """
    from examples.dev_team import DEV_TEAM
    team1 = team_from_dict(DEV_TEAM)
    d = team_to_dict(team1)
    # d 现在是新 schema
    assert "root" in d
    team2 = team_from_dict(d)
    assert team2.root.name == team1.root.name
    assert len(team2.root.children) == len(team1.root.children)


def test_existing_e2e_tests_still_pass():
    """现有 e2e 测试套件不修改通过——通过运行 pytest 验证。"""
    import subprocess
    result = subprocess.run(
        ["pytest", "tests/integration/test_e2e_normal.py",
         "tests/integration/test_e2e_approval.py",
         "tests/integration/test_e2e_error.py", "-v"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Existing e2e tests failed:\n{result.stdout}\n{result.stderr}"
