import sqlite3

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef


def _make_agent(name="coder") -> Agent:
    return Agent(
        name=name,
        role="worker",
        system_prompt="code prompt",
        model=ModelRef(provider="qwen", name="qwen-max"),
        tools=["read_file", "write_file"],
        max_iterations=5,
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )


def test_library_repo_upsert_and_get(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    agent = _make_agent("coder")
    repo.upsert(agent)

    got = repo.get("coder")
    assert got is not None
    assert got.name == "coder"
    assert got.role == "worker"
    assert got.system_prompt == "code prompt"
    assert got.model.provider == "qwen"
    assert got.model.name == "qwen-max"
    assert got.tools == ["read_file", "write_file"]
    assert got.max_iterations == 5
    assert got.approval_policy.level == "tool"
    assert got.approval_policy.targets == ["write_file"]
    assert len(got.mcp_servers) == 1
    assert got.mcp_servers[0].name == "git"


def test_library_repo_get_missing_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    assert repo.get("nonexistent") is None
