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


def test_team_with_mcp_servers():
    from agentteam.domain.mcp_server import MCPServer

    leader = Leader(system_prompt="你是主管")
    server = MCPServer(name="fetch", command="python", args=["-m", "mcp_server_fetch"])
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
        mcp_servers=[server],
    )
    assert len(team.mcp_servers) == 1
    assert team.mcp_servers[0].name == "fetch"


def test_team_mcp_servers_defaults_empty():
    leader = Leader(system_prompt="你是主管")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.mcp_servers == []


def test_leader_to_agent_basic():
    from agentteam.domain.agent import Agent
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    a = leader.to_agent()
    assert isinstance(a, Agent)
    assert a.role == "supervisor"
    assert a.system_prompt == "你是主管"
    assert a.children == []
    assert a.approval_policy is None


def test_leader_to_agent_with_children_and_policy():
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    ap = ApprovalPolicy(level="step")
    leader = Leader(name="lead", system_prompt="你是主管", approval_policy=ap)
    child = Agent(name="w", role="worker")
    a = leader.to_agent(children=[child])
    assert a.role == "supervisor"
    assert a.approval_policy is ap
    assert len(a.children) == 1
    assert a.children[0].name == "w"
