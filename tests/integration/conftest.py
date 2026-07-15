"""集成测试共享 fixture。"""
from __future__ import annotations

import time

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _wait_for_status(run_repo, run_id, timeout=10.0, target_statuses=None):
    """轮询 run 状态直到非 running/pending 或匹配目标状态。"""
    target = target_statuses or {"completed", "failed", "interrupted"}
    for _ in range(int(timeout * 10)):
        run = run_repo.get_run(run_id)
        if run and run["status"] in target:
            return run["status"]
        time.sleep(0.1)
    return None


@pytest.fixture
def integration_db(tmp_path):
    """集成测试用临时 SQLite 连接。"""
    conn = init_db(tmp_path / "integration.db")
    yield conn
    conn.close()


@pytest.fixture
def run_manager(integration_db):
    """RunManager + RunRepo + AuditRepo + EventBus。"""
    run_repo = RunRepo(integration_db)
    audit_repo = AuditRepo(integration_db)
    bus = EventBus()
    return RunManager(run_repo, audit_repo, bus)


@pytest.fixture
def run_repo(integration_db):
    return RunRepo(integration_db)


def make_dev_team_compiled(
    fake_llm: FakeLLM,
    conn,
    leader_policy: ApprovalPolicy | None = None,
    worker_policy: ApprovalPolicy | None = None,
    mcp_loader=None,
):
    """构建一个简化的 2-worker 研发小队 Team + 编译好的 graph。

    基于 DEV_TEAM 结构,但只用 analyst + coder 两个 worker,
    使 FakeLLM 响应编排可控。可通过参数覆盖审批策略。
    """
    leader = Leader(
        name="tech_lead",
        system_prompt="你是技术主管",
        model=ModelRef("qwen", "qwen-max"),
        approval_policy=leader_policy,
    )
    workers = [
        Worker(
            name="analyst",
            role="需求分析员",
            description="拆用户故事",
            system_prompt="你是需求分析员",
            tools=["search_web"],
            max_iterations=5,
        ),
        Worker(
            name="coder",
            role="代码工程师",
            description="写代码",
            system_prompt="你是代码工程师",
            tools=["read_file", "write_file"],
            approval_policy=worker_policy,
            max_iterations=10,
        ),
    ]
    team = Team(
        name="dev_team_test",
        description="测试用研发小队",
        leader=leader,
        workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file", "list_dir", "search_web"],
    )

    reg = ToolRegistry(mcp_loader=mcp_loader)
    from agentteam.tools.skills import register_builtin_skills
    register_builtin_skills(reg)

    return compile_team_with_registry(team, fake_llm, conn, reg)


def compile_team_with_registry(
    team,
    fake_llm: FakeLLM,
    conn,
    reg: ToolRegistry,
):
    """用指定的 team + registry 编译 graph(供需要自定义 team/registry 的测试使用)。"""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from agentteam.runtime.graph import TeamCompiler
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    saver = SqliteSaver(conn)
    saver.setup()
    return compiler.compile(team, checkpointer=saver)
