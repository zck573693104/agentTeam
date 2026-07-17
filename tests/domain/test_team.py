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
    # 新设计下 leader 是从 root 反推的 property，必然是新对象，故只比较值
    assert team.leader.name == leader.name
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


def test_team_root_construction():
    """新 schema：直接用 root agent 构造 Team。"""
    from agentteam.domain.agent import Agent
    root = Agent(
        name="lead", role="supervisor", system_prompt="你是主管",
        children=[Agent(name="w1", role="worker")],
    )
    team = Team(
        name="t", description="d", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.root is root
    # 兼容 property：leader/workers 从 root 反推
    assert team.leader.name == "lead"
    assert len(team.workers) == 1
    assert team.workers[0].name == "w1"


def test_team_from_legacy():
    """旧 leader+workers 配置经 from_legacy 转 root。"""
    from agentteam.domain.agent import Agent
    leader = Leader(name="boss", system_prompt="你是主管")
    workers = [
        Worker(name="coder", role="代码工程师", description="", system_prompt=""),
        Worker(name="tester", role="测试员", description="", system_prompt=""),
    ]
    team = Team.from_legacy(
        name="dev", description="d", leader=leader, workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.root.role == "supervisor"
    assert team.root.name == "boss"
    assert len(team.root.children) == 2
    assert all(isinstance(c, Agent) for c in team.root.children)
    # property 反推一致
    assert team.leader.name == "boss"
    assert [w.name for w in team.workers] == ["coder", "tester"]


def test_team_legacy_construction_still_works():
    """旧 Team(leader=..., workers=...) 构造方式仍可用（不通过 from_legacy）。"""
    leader = Leader(system_prompt="你是主管")
    coder = Worker(name="coder", role="代码工程师", description="", system_prompt="")
    team = Team(
        name="dev", description="d", leader=leader, workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # 旧字段访问（新设计下 leader 是从 root 反推的 property，必然是新对象，故只比较值）
    assert team.leader.name == leader.name
    assert len(team.workers) == 1
    # root 也能访问
    assert team.root.role == "supervisor"
    assert team.root.name == "leader"


def test_team_property_workers_filters_non_worker_children():
    """property workers 仅返回 role=worker 的 children，跳过 supervisor/TeamRef。"""
    from agentteam.domain.agent import Agent, TeamRef
    root = Agent(
        name="lead", role="supervisor",
        children=[
            Agent(name="w1", role="worker"),
            Agent(name="sub", role="supervisor", children=[
                Agent(name="w2", role="worker")
            ]),
            TeamRef(name="other_team"),
        ],
    )
    team = Team(
        name="t", description="d", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # workers property 只返回 w1（worker 角色的直接 child）
    assert [w.name for w in team.workers] == ["w1"]
