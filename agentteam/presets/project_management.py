"""项目管理团队预置 — PM→任务拆解师+进度跟踪师+风险评估师。

展示能力:
- SP1:supervisor→worker 平级编排 + tool 级审批(创建/更新任务)
- SP2:Team 级 task MCP 挂载
- 专家库引用:risk_assessor 作为可复用风险专家模板
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


# —— 专家库 agents ——
LIB_AGENTS: list[Agent] = [
    Agent(
        name="risk_assessor", role="worker",
        system_prompt=(
            "你是风险评估师,使用 read_file 查看风险登记册与项目文档,"
            "按概率×影响评估风险等级,输出风险应对建议(规避/转移/缓解/接受)。"
        ),
        tools=["read_file"],
        max_iterations=5,
    ),
]


TEAM: Team = Team(
    name="project_management",
    description="项目管理团队 — PM→任务拆解师+进度跟踪师+风险评估师,挂接任务 MCP,创建/更新任务需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="task", command="npx",
            args=["-y", "@modelcontextprotocol/server-jira"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="project_manager", role="supervisor",
        system_prompt=(
            "你是项目经理(PM),派活给任务拆解师(task_breaker)、"
            "进度跟踪师(progress_tracker)和风险评估师(risk_assessor),"
            "汇总项目状态与风险。"
        ),
        children=[
            Agent(
                name="task_breaker", role="worker",
                system_prompt=(
                    "你是任务拆解师,使用 task MCP 创建任务(create_task)"
                    "与拆解子任务(create_subtask),WBS 分解。创建任务前需审批。"
                ),
                tools=["mcp:task:create_task", "mcp:task:create_subtask",
                       "mcp:task:list_tasks"],
                approval_policy=ApprovalPolicy(
                    level="tool",
                    targets=["mcp:task:create_task", "mcp:task:create_subtask"],
                ),
                max_iterations=8,
            ),
            Agent(
                name="progress_tracker", role="worker",
                system_prompt=(
                    "你是进度跟踪师,使用 task MCP 查询任务状态(list_tasks)"
                    "与更新进度(update_status),生成燃尽图数据。更新状态前需审批。"
                ),
                tools=["mcp:task:list_tasks", "mcp:task:update_status",
                       "mcp:task:get_task"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:task:update_status"],
                ),
                max_iterations=8,
            ),
            # 专家库引用:复用 risk_assessor 模板
            Agent(name="risk_assessor", role="worker", ref="library:risk_assessor"),
        ],
    ),
)


METADATA: dict = {
    "name": "project_management",
    "title": "项目管理团队",
    "description": "PM→任务拆解师+进度跟踪师+风险评估师,挂接任务 MCP,创建/更新任务需审批",
    "category": "productivity",
    "tags": ["项目管理", "任务", "MCP", "审批", "风险"],
    "deps_teams": [],
    "deps_library": ["risk_assessor"],
}
