"""个人助理团队预置 — 助理→信息收集员+日程协调员+提醒通知员。

展示能力:
- SP1:supervisor→worker 平级编排 + tool 级审批(创建日程/发邮件)
- SP2:Team 级 3 个 MCP 挂载(search + calendar + email)
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="personal_assistant",
    description="个人助理团队 — 助理→信息收集员+日程协调员+提醒通知员,挂接搜索+日历+邮件 MCP,创建日程与发邮件需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="search", command="npx",
            args=["-y", "@modelcontextprotocol/server-search"],
            transport="stdio",
        ),
        MCPServer(
            name="calendar", command="npx",
            args=["-y", "@modelcontextprotocol/server-google-calendar"],
            transport="stdio",
        ),
        MCPServer(
            name="email", command="npx",
            args=["-y", "@modelcontextprotocol/server-gmail"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="assistant", role="supervisor",
        system_prompt=(
            "你是个人助理,派活给信息收集员(info_collector)、"
            "日程协调员(schedule_coordinator)和提醒通知员(notifier),"
            "汇总用户当日待办与信息。"
        ),
        children=[
            Agent(
                name="info_collector", role="worker",
                system_prompt=(
                    "你是信息收集员,使用 search MCP 搜索信息(query)与趋势(trends),"
                    "使用 read_file 查阅本地资料,为用户整理摘要。"
                ),
                tools=["mcp:search:query", "mcp:search:trends", "read_file"],
                max_iterations=8,
            ),
            Agent(
                name="schedule_coordinator", role="worker",
                system_prompt=(
                    "你是日程协调员,使用 calendar MCP 查询空闲时段(find_slots)"
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
                name="notifier", role="worker",
                system_prompt=(
                    "你是提醒通知员,使用 email MCP 发送邮件(send_email)"
                    "与查看收件箱(list_inbox),必要时加急提醒用户。发送邮件前需审批。"
                ),
                tools=["mcp:email:send_email", "mcp:email:list_inbox"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:email:send_email"],
                ),
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "personal_assistant",
    "title": "个人助理团队",
    "description": "助理→信息收集员+日程协调员+提醒通知员,挂接搜索+日历+邮件 MCP,创建日程与发邮件需审批",
    "category": "productivity",
    "tags": ["个人助理", "日程", "邮件", "MCP", "审批"],
    "deps_teams": [],
    "deps_library": [],
}
