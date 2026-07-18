"""企业级研发团队预置 — CEO→CTO→工程师+测试+审查。

展示 SP1-SP4 全部能力:
- SP1:3 级 supervisor 链 + 专家库引用 + TeamRef 嵌套 + 声明式审批
- SP2:Team 级 git MCP 挂载
- SP3/SP4:通过 install-preset 持久化与幂等安装
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


# —— 专家库 agents ——
LIB_AGENTS: list[Agent] = [
    Agent(
        name="code_engineer", role="worker",
        system_prompt=(
            "你是代码工程师,使用 read_file/write_file 完成编码任务,"
            "使用 git MCP 工具(git_status/git_diff/git_log)查看仓库状态。"
            "写文件前需审批。"
        ),
        tools=["read_file", "write_file",
               "mcp:git:git_status", "mcp:git:git_diff", "mcp:git:git_log"],
        max_iterations=10,
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
    ),
]


# —— 子 Team:测试小队(供主 Team 通过 TeamRef 引用) ——
TEST_SUBTEAM: Team = Team(
    name="test_subteam",
    description="测试小队 — 主管 + 测试员",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管,派活给 tester。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
            tools=["read_file", "write_file"], max_iterations=5,
            approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)


# —— 主 Team:企业级研发团队 ——
TEAM: Team = Team(
    name="enterprise_dev",
    description="企业级研发团队 — CEO→CTO→工程师+测试+审查,含 git MCP、写文件审批、嵌套测试小队",
    default_model=ModelRef("qwen", "qwen-max"),
    # Team 级 MCP:全队共享 git 服务
    mcp_servers=[
        MCPServer(
            name="git", command="npx",
            args=["-y", "@modelcontextprotocol/server-git", "--repository", "."],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO,派活给技术副总裁 CTO,审核 CTO 产出。",
        children=[Agent(
            name="cto", role="supervisor",
            system_prompt=(
                "你是 CTO,派活给工程师(eng)、审查员(reviewer)和测试小队(qa_team),"
                "汇总各方产出回复 CEO。"
            ),
            children=[
                # 专家库引用:复用 code_engineer 模板,覆盖 name 为 eng
                Agent(name="eng", role="worker", ref="library:code_engineer"),
                # 审查员
                Agent(
                    name="reviewer", role="worker",
                    system_prompt="你是代码审查员,使用 read_file 审查代码与测试质量,给出改进建议。",
                    tools=["read_file"], max_iterations=5,
                    approval_policy=ApprovalPolicy(level="worker"),
                ),
                # Team 嵌套:引用测试小队,重命名为 qa_team
                TeamRef(name="test_subteam", alias="qa_team"),
            ],
        )],
    ),
)


METADATA: dict = {
    "name": "enterprise_dev",
    "title": "企业级研发团队",
    "description": "CEO→CTO→工程师+测试+审查,含 git MCP、写文件审批、嵌套测试小队",
    "category": "research",
    "tags": ["研发", "MCP", "审批", "多层级", "专家库"],
    "deps_teams": ["test_subteam"],
    "deps_library": ["code_engineer"],
}
