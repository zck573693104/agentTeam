"""SP7b: AgentLibrary update_* 方法测试。"""
from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


def test_update_version_changes_agent_version():
    """update_version 修改内存中的 Agent.version。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker", version=1))
    lib.update_version("coder", 5)
    assert lib.get("coder").version == 5


def test_update_prompt_changes_agent_system_prompt():
    """update_prompt 修改内存中的 Agent.system_prompt。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker", system_prompt="old"))
    lib.update_prompt("coder", "new prompt")
    assert lib.get("coder").system_prompt == "new prompt"


def test_update_params_changes_agent_max_iterations_and_policy():
    """update_params 修改 max_iterations 和 approval_policy。"""
    from agentteam.domain.approval import ApprovalPolicy
    lib = AgentLibrary()
    lib.register(Agent(
        name="coder", role="worker",
        max_iterations=5,
        approval_policy=ApprovalPolicy(level="worker"),
    ))
    new_policy = ApprovalPolicy(level="tool", targets=["dangerous_tool"])
    lib.update_params("coder", {
        "max_iterations": 15,
        "approval_policy": new_policy,
    })
    agent = lib.get("coder")
    assert agent.max_iterations == 15
    assert agent.approval_policy == new_policy


def test_update_version_unknown_agent_is_noop():
    """update_version 未知 agent:不抛异常(幂等)。"""
    lib = AgentLibrary()
    lib.update_version("nonexistent", 5)  # 应不抛


def test_update_version_with_repo_persists():
    """有 repo 时,update_version 同步到 DB。"""
    from unittest.mock import MagicMock
    mock_repo = MagicMock()
    lib = AgentLibrary(repo=mock_repo)
    lib.register(Agent(name="coder", role="worker", version=1))
    lib.update_version("coder", 3)
    # repo.upsert 应被调用,传入更新后的 agent
    assert mock_repo.upsert.called
    updated_agent = mock_repo.upsert.call_args[0][0]
    assert updated_agent.version == 3
