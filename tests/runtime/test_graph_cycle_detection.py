"""BUG-04: TeamCompiler 循环 TeamRef 检测的假阳性回归测试。

原实现用 `alias in path.split(".")` 做循环检测，当两条不同的 sub-team
引用链复用同一 alias（如 A→B(alias="x")→C(alias="x")）时会被误判为
循环引用。实际上 A→B→C 是线性链。修复后用 `sub_team.name`（被引用的
Team 的唯一名）做检测，真正的循环引用（如 A→B→A）仍应被识别。
"""
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _make_compiler(fake_llm):
    provider = FakeModelProvider({"qwen-max": fake_llm})
    return TeamCompiler(provider, ToolRegistry())


def test_linear_chain_same_alias_not_circular(fake_llm):
    """A→B(alias="x")→C(alias="x") 是线性链，不应抛循环错误。

    两次 TeamRef 都用 alias="x"，但 sub_team.name 分别为 team_b / team_c，
    互不相同。原实现误判为循环引用并抛 ValueError。
    """
    compiler = _make_compiler(fake_llm)

    team_c = Team(
        name="team_c", description="c",
        root=Agent(
            name="lc", role="supervisor", system_prompt="c",
            children=[Agent(name="w_c", role="worker", system_prompt="wc")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    team_b = Team(
        name="team_b", description="b",
        root=Agent(
            name="lb", role="supervisor", system_prompt="b",
            children=[TeamRef(name="team_c", alias="x")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    team_a = Team(
        name="team_a", description="a",
        root=Agent(
            name="la", role="supervisor", system_prompt="a",
            children=[TeamRef(name="team_b", alias="x")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(team_a)
    compiler.register_team(team_b)
    compiler.register_team(team_c)

    # 编译应成功，不应抛 ValueError
    graph = compiler.compile(team_a)
    assert graph is not None

    # 顶层图应含 subteam_x 节点（指向 team_b）
    node_names = set(graph.get_graph().nodes.keys())
    assert "subteam_x" in node_names


def test_circular_team_ref_still_detected(fake_llm):
    """真正的循环引用 A→B→A 仍应被检测并抛 ValueError。

    回归保障：修复假阳性时不能放过真循环。
    """
    import pytest

    compiler = _make_compiler(fake_llm)

    team_a = Team(
        name="team_a", description="a",
        root=Agent(
            name="la", role="supervisor", system_prompt="a",
            children=[TeamRef(name="team_b", alias="b")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    team_b = Team(
        name="team_b", description="b",
        root=Agent(
            name="lb", role="supervisor", system_prompt="b",
            children=[TeamRef(name="team_a", alias="a")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(team_a)
    compiler.register_team(team_b)

    with pytest.raises(ValueError, match="Circular team reference"):
        compiler.compile(team_a)


def test_self_reference_team_still_detected(fake_llm):
    """Team 直接引用自身（A→A）也应被识别为循环。"""
    import pytest

    compiler = _make_compiler(fake_llm)

    team_a = Team(
        name="team_a", description="a",
        root=Agent(
            name="la", role="supervisor", system_prompt="a",
            children=[TeamRef(name="team_a", alias="self")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(team_a)

    with pytest.raises(ValueError, match="Circular team reference"):
        compiler.compile(team_a)
