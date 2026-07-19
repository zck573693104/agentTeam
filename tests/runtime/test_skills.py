"""SP7a Skill 系统测试。"""
from agentteam.domain.agent import Agent


def test_agent_skills_field_defaults_empty():
    """Agent.skills 默认空 list(向后兼容)。"""
    agent = Agent(name="w1", role="worker")
    assert agent.skills == []


def test_agent_skills_field_accepts_list():
    """Agent.skills 可在构造时传入。"""
    agent = Agent(name="w1", role="worker", skills=["code_review", "testing"])
    assert agent.skills == ["code_review", "testing"]
