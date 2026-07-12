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
    assert d["leader"]["model"]["provider"] == "qwen"
    assert d["workers"][0]["approval_policy"]["level"] == "tool"
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
