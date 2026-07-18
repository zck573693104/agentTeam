"""内容营销团队预置 — 主编→策划→写手→SEO。

展示能力:
- SP1:supervisor→worker 平级编排 + tool 级审批(写文件/社媒发布)
- SP2:Team 级 2 个 MCP 挂载(search + social)
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="content_marketing",
    description="内容营销团队 — 主编→策划→写手→SEO,挂接搜索 MCP + 社媒 MCP,写文与发布需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="search", command="npx",
            args=["-y", "@modelcontextprotocol/server-search"],
            transport="stdio",
        ),
        MCPServer(
            name="social", command="npx",
            args=["-y", "@modelcontextprotocol/server-social-media"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="editor_in_chief", role="supervisor",
        system_prompt=(
            "你是主编,派活给选题策划(planner)、写手(writer)和 SEO 优化师(seo),"
            "汇总内容产出。"
        ),
        children=[
            Agent(
                name="planner", role="worker",
                system_prompt=(
                    "你是选题策划,使用 search MCP 搜索热点(query)与趋势(trends),"
                    "输出选题清单。"
                ),
                tools=["mcp:search:query", "mcp:search:trends"],
                max_iterations=5,
            ),
            Agent(
                name="writer", role="worker",
                system_prompt=(
                    "你是写手,使用 read_file/write_file 撰写文章。"
                    "写文件前需审批。"
                ),
                tools=["read_file", "write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
                max_iterations=10,
            ),
            Agent(
                name="seo", role="worker",
                system_prompt=(
                    "你是 SEO 优化师,使用 search MCP 查关键词(keywords),"
                    "使用 social MCP 安排发布(schedule_post)。发布前需审批。"
                ),
                tools=["mcp:search:keywords", "mcp:social:schedule_post"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:social:schedule_post"],
                ),
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "content_marketing",
    "title": "内容营销团队",
    "description": "主编→策划→写手→SEO,挂接搜索 MCP + 社媒 MCP,写文与发布需审批",
    "category": "marketing",
    "tags": ["内容", "营销", "MCP", "审批", "SEO"],
    "deps_teams": [],
    "deps_library": [],
}
