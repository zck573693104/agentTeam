from agentteam.api.serializer import team_to_dict, team_from_dict
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def _make_team() -> Team:
    return Team(
        name="dev",
        description="研发小队",
        leader=Leader(
            name="leader",
            role="主管",
            system_prompt="你是主管",
            model=ModelRef(provider="qwen", name="qwen-max"),
        ),
        workers=[
            Worker(
                name="coder",
                role="代码工程师",
                description="写代码",
                system_prompt="你是代码工程师",
                model=None,
                tools=["read_file", "write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
                max_iterations=5,
            ),
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
        skills=[],
        mcp_servers=[
            MCPServer(name="git", command="mcp-server-git", args=["--repo", "."]),
        ],
    )


def test_team_to_dict_produces_json_serializable_dict():
    team = _make_team()
    d = team_to_dict(team)
    import json
    json.dumps(d)  # 不抛异常即可
    assert d["name"] == "dev"
    # 新 schema：to_dict 输出 root（Agent 树），不是 leader/workers
    assert "root" in d
    assert d["root"]["name"] == "leader"
    assert d["root"]["role"] == "supervisor"
    assert d["root"]["model"]["provider"] == "qwen"
    # 第一个 child（原 worker coder）保留 approval_policy
    assert d["root"]["children"][0]["approval_policy"]["level"] == "tool"
    assert d["mcp_servers"][0]["name"] == "git"


def test_team_from_dict_reconstructs_nested_objects():
    team = _make_team()
    d = team_to_dict(team)
    restored = team_from_dict(d)
    assert isinstance(restored, Team)
    assert isinstance(restored.leader, Leader)
    assert isinstance(restored.leader.model, ModelRef)
    assert isinstance(restored.workers[0], Worker)
    assert isinstance(restored.workers[0].approval_policy, ApprovalPolicy)
    assert isinstance(restored.mcp_servers[0], MCPServer)
    assert isinstance(restored.default_model, ModelRef)


def test_team_round_trip_preserves_all_fields():
    team = _make_team()
    restored = team_from_dict(team_to_dict(team))
    assert restored.name == team.name
    assert restored.leader.system_prompt == team.leader.system_prompt
    assert restored.workers[0].tools == team.workers[0].tools
    assert restored.workers[0].approval_policy.targets == team.workers[0].approval_policy.targets
    assert restored.workers[0].max_iterations == team.workers[0].max_iterations
    assert restored.mcp_servers[0].command == team.mcp_servers[0].command


def test_team_from_dict_with_null_optionals():
    d = {
        "name": "t",
        "description": "test",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x", "model": None, "approval_policy": None},
        "workers": [
            {"name": "w1", "role": "r", "description": "", "system_prompt": "x",
             "model": None, "tools": [], "approval_policy": None, "max_iterations": 10}
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
        "mcp_servers": [],
    }
    team = team_from_dict(d)
    assert team.leader.model is None
    assert team.leader.approval_policy is None
    assert team.workers[0].model is None
    assert team.workers[0].approval_policy is None


def test_team_from_dict_new_schema():
    """新 schema：dict 含 root 字段。"""
    data = {
        "name": "t",
        "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "system_prompt": "你是主管",
            "children": [
                {"name": "w1", "role": "worker", "tools": ["read_file"]},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": ["read_file"],
        "mcp_servers": [],
    }
    team = team_from_dict(data)
    assert team.name == "t"
    assert team.root.role == "supervisor"
    assert team.root.name == "lead"
    assert len(team.root.children) == 1
    assert team.root.children[0].name == "w1"
    assert team.root.children[0].tools == ["read_file"]


def test_team_from_dict_new_schema_with_teamref():
    """新 schema：children 中含 TeamRef。"""
    data = {
        "name": "t", "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "children": [
                {"_type": "TeamRef", "name": "sub_team", "alias": "qa"},
                {"name": "w", "role": "worker"},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    from agentteam.domain.agent import TeamRef
    assert isinstance(team.root.children[0], TeamRef)
    assert team.root.children[0].name == "sub_team"
    assert team.root.children[0].alias == "qa"


def test_team_from_dict_new_schema_with_ref():
    """新 schema：Agent 含 ref 字段。"""
    data = {
        "name": "t", "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "children": [
                {"name": "eng", "role": "worker", "ref": "library:code_engineer"},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    assert team.root.children[0].ref == "library:code_engineer"


def test_team_to_dict_new_schema_roundtrip():
    """新 schema 序列化/反序列化往返。"""
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="d",
        root=Agent(
            name="lead", role="supervisor",
            children=[
                Agent(name="w", role="worker", tools=["read_file"]),
                TeamRef(name="sub", alias="qa"),
            ],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    d = team_to_dict(team)
    assert "root" in d
    assert d["root"]["name"] == "lead"
    assert d["root"]["children"][0]["name"] == "w"
    assert d["root"]["children"][1]["_type"] == "TeamRef"
    # 往返
    team2 = team_from_dict(d)
    assert team2.root.name == "lead"
    assert len(team2.root.children) == 2


def test_team_from_dict_legacy_schema_still_works():
    """旧 schema（leader+workers）仍可解析。"""
    data = {
        "name": "dev_team",
        "description": "研发小队",
        "leader": {
            "name": "tech_lead", "role": "技术主管",
            "system_prompt": "你是主管",
            "model": {"provider": "qwen", "name": "qwen-max"},
        },
        "workers": [
            {"name": "coder", "role": "代码工程师", "description": "",
             "system_prompt": "你是代码工程师"},
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    assert team.root.role == "supervisor"
    assert team.root.name == "tech_lead"
    assert len(team.root.children) == 1
    assert team.root.children[0].name == "coder"
    # 兼容 property
    assert team.leader.name == "tech_lead"
    assert team.workers[0].name == "coder"


def test_agent_to_dict_includes_mcp_servers():
    from agentteam.domain.agent import Agent
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.api.serializer import _agent_to_dict
    a = Agent(
        name="w", role="worker",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )
    d = _agent_to_dict(a)
    assert "mcp_servers" in d
    assert len(d["mcp_servers"]) == 1
    assert d["mcp_servers"][0]["name"] == "git"


def test_agent_from_dict_parses_mcp_servers():
    from agentteam.domain.agent import Agent
    from agentteam.api.serializer import _agent_from_dict
    d = {
        "name": "w", "role": "worker",
        "mcp_servers": [{"name": "git", "command": "git-mcp", "args": [], "env": {}, "transport": "stdio", "url": None}],
    }
    a = _agent_from_dict(d)
    assert len(a.mcp_servers) == 1
    assert a.mcp_servers[0].name == "git"


def test_teamref_to_dict_includes_mcp_overrides():
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.api.serializer import _agent_to_dict
    parent = Agent(
        name="lead", role="supervisor",
        children=[TeamRef(name="sub", alias="qa",
                          mcp_overrides=[MCPServer(name="extra", command="x")])],
    )
    d = _agent_to_dict(parent)
    child = d["children"][0]
    assert child["_type"] == "TeamRef"
    assert "mcp_overrides" in child
    assert child["mcp_overrides"][0]["name"] == "extra"


def test_teamref_from_dict_parses_mcp_overrides():
    from agentteam.domain.agent import TeamRef
    from agentteam.api.serializer import _agent_from_dict
    d = {
        "name": "lead", "role": "supervisor",
        "children": [{
            "_type": "TeamRef", "name": "sub", "alias": "qa",
            "mcp_overrides": [{"name": "extra", "command": "x", "args": [], "env": {}, "transport": "stdio", "url": None}],
        }],
    }
    a = _agent_from_dict(d)
    ref = a.children[0]
    assert isinstance(ref, TeamRef)
    assert len(ref.mcp_overrides) == 1
    assert ref.mcp_overrides[0].name == "extra"


def test_team_to_dict_roundtrip_with_mcp():
    from agentteam.domain.agent import Agent
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.api.serializer import team_to_dict, team_from_dict
    team = Team(
        name="t", description="d",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(
                name="coder", role="worker",
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    d = team_to_dict(team)
    team2 = team_from_dict(d)
    assert len(team2.root.children[0].mcp_servers) == 1
    assert team2.root.children[0].mcp_servers[0].name == "git"
