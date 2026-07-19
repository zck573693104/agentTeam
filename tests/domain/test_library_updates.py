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


# ---------------------------------------------------------------------------
# BUG-03: DB 失败时内存保持旧值(与 register/update/delete 一致)
# ---------------------------------------------------------------------------


def test_update_version_db_failure_keeps_old_value():
    """DB upsert 失败时,内存 Agent.version 保持旧值(BUG-03 update_version)。"""
    import pytest
    from tests.domain.test_library_concurrency import _FailingUpsertRepo
    lib = AgentLibrary(repo=_FailingUpsertRepo())
    # 绕过失败的 repo 直接放旧值进内存
    lib.agents["coder"] = Agent(name="coder", role="worker", version=1)
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        lib.update_version("coder", 5)
    # DB 失败 → 内存仍是旧值
    assert lib.get("coder").version == 1


def test_update_prompt_db_failure_keeps_old_value():
    """DB upsert 失败时,内存 Agent.system_prompt 保持旧值(BUG-03 update_prompt)。"""
    import pytest
    from tests.domain.test_library_concurrency import _FailingUpsertRepo
    lib = AgentLibrary(repo=_FailingUpsertRepo())
    lib.agents["coder"] = Agent(name="coder", role="worker", system_prompt="old")
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        lib.update_prompt("coder", "new")
    assert lib.get("coder").system_prompt == "old"


def test_update_params_db_failure_keeps_old_value():
    """DB upsert 失败时,内存 Agent.max_iterations 保持旧值(BUG-03 update_params)。"""
    import pytest
    from tests.domain.test_library_concurrency import _FailingUpsertRepo
    lib = AgentLibrary(repo=_FailingUpsertRepo())
    lib.agents["coder"] = Agent(name="coder", role="worker", max_iterations=5)
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        lib.update_params("coder", {"max_iterations": 15})
    assert lib.get("coder").max_iterations == 5
