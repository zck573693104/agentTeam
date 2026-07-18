"""多级 MCP 挂载示例：Team 级 + Worker 级 + TeamRef 覆盖。

展示 SP2 三层 MCP 挂载能力：
- Team.mcp_servers：全队共享的 MCP 服务
- Agent.mcp_servers：Worker 专属的 MCP 服务
- TeamRef.mcp_overrides：引用 sub-Team 时追加的 MCP 服务
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef

# —— 子 Team：测试小队 ——
TEST_SUBTEAM = Team(
    name="test_subteam",
    description="测试小队（自身无 MCP，由父 Team 通过 mcp_overrides 追加）",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员，用 test_runner 工具跑测试。",
            tools=["mcp:test:test_run"],
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)

# —— 主 Team：三层 MCP 挂载 ——
MULTI_LEVEL_MCP_TEAM = Team(
    name="multi_level_mcp",
    description="三层 MCP 挂载示例",
    # Team 级 MCP：全队共享
    mcp_servers=[MCPServer(name="shared", command="shared-mcp")],
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO，派活给 coder 和 qa 小队。",
        children=[
            # Worker 级 MCP：coder 专属的 git 服务
            Agent(
                name="coder", role="worker",
                system_prompt="你是代码工程师，用 git 工具操作代码。",
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
                tools=["mcp:git:git_status", "mcp:git:git_commit"],
            ),
            # TeamRef 级 MCP 覆盖：引用测试小队时追加 test 服务
            TeamRef(
                name="test_subteam", alias="qa",
                mcp_overrides=[MCPServer(name="test", command="test-mcp")],
            ),
        ],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)
