"""SP7b: Agent.version 字段测试。"""
from agentteam.domain.agent import Agent


def test_agent_version_defaults_to_1():
    """Agent.version 默认 1(向后兼容)。"""
    agent = Agent(name="w", role="worker")
    assert agent.version == 1


def test_agent_version_accepts_custom_value():
    """Agent.version 可在构造时传入。"""
    agent = Agent(name="w", role="worker", version=5)
    assert agent.version == 5
