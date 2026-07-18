"""数据分析团队预置 — 总监→SQL工程师→可视化师。

展示能力:
- SP1:supervisor→worker 平级编排(无审批,数据查询只读)
- SP2:Team 级 2 个 MCP 挂载(db + chart)
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="data_analysis",
    description="数据分析团队 — 总监→SQL工程师→可视化师,挂接数据库 MCP + 图表 MCP",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="db", command="npx",
            args=["-y", "@modelcontextprotocol/server-postgres",
                  "--connection-string", "postgresql://localhost/analytics"],
            transport="stdio",
        ),
        MCPServer(
            name="chart", command="npx",
            args=["-y", "@modelcontextprotocol/server-chart"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="analytics_director", role="supervisor",
        system_prompt=(
            "你是分析总监,派活给 SQL 工程师(sql_engineer)和可视化师(visualizer),"
            "汇总数据分析结果。"
        ),
        children=[
            Agent(
                name="sql_engineer", role="worker",
                system_prompt=(
                    "你是 SQL 工程师,使用 db MCP 执行查询(query)与查看 schema,"
                    "必要时 read_file 查看数据字典。"
                ),
                tools=["mcp:db:query", "mcp:db:schema", "read_file"],
                max_iterations=10,
            ),
            Agent(
                name="visualizer", role="worker",
                system_prompt=(
                    "你是可视化师,使用 chart MCP 渲染图表(render)并导出(export),"
                    "使用 write_file 保存图表配置。"
                ),
                tools=["mcp:chart:render", "mcp:chart:export", "write_file"],
                max_iterations=8,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "data_analysis",
    "title": "数据分析团队",
    "description": "总监→SQL工程师→可视化师,挂接数据库 MCP + 图表 MCP",
    "category": "analytics",
    "tags": ["数据分析", "MCP", "数据库", "可视化"],
    "deps_teams": [],
    "deps_library": [],
}
