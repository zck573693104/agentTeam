from agentteam.domain import ApprovalPolicy, Leader, Team, Worker
from agentteam.models.provider import ModelRef


def test_leader_defaults():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    assert leader.name == "leader"
    assert leader.role == "主管"
    assert leader.approval_policy is None


def test_team_construction():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    coder = Worker(name="coder", role="代码工程师", description="写代码", system_prompt="你是代码工程师")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.name == "dev"
    assert team.leader is leader
    assert len(team.workers) == 1
    assert team.skills == []


def test_team_with_skills():
    leader = Leader(system_prompt="你是主管")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file"],
    )
    assert team.skills == ["read_file", "write_file"]
