"""Agent.engine（混编字段）的序列化往返测试。"""
from agentteam.domain.agent import Agent
from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


def _team(root: Agent) -> Team:
    return Team(
        name="t", description="d", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )


def test_agent_engine_roundtrip():
    """root.engine 序列化保留；未设置的 agent 不输出 engine 键。"""
    team = _team(Agent(
        name="leader", role="supervisor", engine="langgraph",
        children=[Agent(name="w1", role="worker")],
    ))
    d = team_to_dict(team)
    assert d["root"]["engine"] == "langgraph"
    # 未显式声明的 agent 不输出 engine（与 Team.engine 同风格，旧客户端兼容）
    assert "engine" not in d["root"]["children"][0]

    team2 = team_from_dict(d)
    assert team2.root.engine == "langgraph"
    assert team2.root.children[0].engine is None


def test_agent_engine_none_omitted():
    """engine=None 的团队序列化不含 engine 键（旧 schema 输出不变）。"""
    team = _team(Agent(name="l", role="supervisor",
                       children=[Agent(name="w", role="worker")]))
    d = team_to_dict(team)
    assert "engine" not in d["root"]
