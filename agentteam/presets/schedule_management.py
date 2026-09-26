"""日程管理团队预置 — 主管→日程规划师+提醒专员+优先级评估师。

展示能力:
- SP1:supervisor→worker 平级编排 + tool 级审批(创建日程)
- SP2:Team 级 calendar MCP 挂载
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="schedule_management",
    description="日程管理团队 — 主管→日程规划师+提醒专员+优先级评估师,挂接日历 MCP,创建日程需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="calendar", command="npx",
            args=["-y", "@modelcontextprotocol/server-google-calendar"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="schedule_manager", role="supervisor",
        system_prompt=(
            "你是日程主管,派活给日程规划师(planner)、提醒专员(reminder)"
            "和优先级评估师(prioritizer),汇总日程安排。"
        ),
        # P2 maker/checker 独立:review 用不同模型,避免同模型自评自批
        review_model=ModelRef("openai", "gpt-4o-mini"),
        children=[
            Agent(
                name="planner", role="worker",
                system_prompt=(
                    "你是日程规划师,使用 calendar MCP 查询空闲时段(find_slots)"
                    "与创建日程(create_event)。创建日程前需审批。"
                ),
                tools=["mcp:calendar:find_slots", "mcp:calendar:create_event",
                       "mcp:calendar:get_schedule"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:calendar:create_event"],
                ),
                max_iterations=8,
            ),
            Agent(
                name="reminder", role="worker",
                system_prompt=(
                    "你是提醒专员,使用 calendar MCP 设置提醒(set_reminder)"
                    "与查询当日日程(get_schedule),确保不漏重要事项。"
                ),
                tools=["mcp:calendar:set_reminder", "mcp:calendar:get_schedule"],
                max_iterations=5,
            ),
            Agent(
                name="prioritizer", role="worker",
                system_prompt=(
                    "你是优先级评估师,使用 read_file 查看任务清单,"
                    "按紧急重要矩阵评估事项优先级,输出排序建议。"
                ),
                tools=["read_file"],
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "schedule_management",
    "title": "日程管理团队",
    "description": "主管→日程规划师+提醒专员+优先级评估师,挂接日历 MCP,创建日程需审批",
    "category": "productivity",
    "tags": ["日程", "日历", "MCP", "审批", "提醒"],
    "deps_teams": [],
    "deps_library": [],
}
