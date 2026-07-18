"""客户支持团队预置 — 主管→一线→升级专员→投诉处理。

展示能力:
- SP1:supervisor→supervisor→worker 多级 + 声明式审批(升级 step 级,投诉 tool 级)
- SP2:Team 级 ticket MCP 挂载
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="customer_support",
    description="客户支持团队 — 主管→一线→升级专员→投诉处理,挂接工单 MCP,升级与投诉需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="ticket", command="npx",
            args=["-y", "@modelcontextprotocol/server-ticket"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="support_manager", role="supervisor",
        system_prompt=(
            "你是客服主管,派活给一线客服(frontline)、升级专员(escalation)"
            "和投诉处理员(complaint_handler),汇总处理结果。"
        ),
        children=[
            # 一线客服:处理常规工单
            Agent(
                name="frontline", role="worker",
                system_prompt=(
                    "你是一线客服,使用 ticket MCP 列出/查看工单、创建内部备注,"
                    "处理常规客户咨询。无法解决时升级给 escalation。"
                ),
                tools=["mcp:ticket:list_tickets", "mcp:ticket:get_ticket",
                       "mcp:ticket:create_note"],
                max_iterations=10,
            ),
            # 升级专员:supervisor,每步审批(防止升级决策失控)
            Agent(
                name="escalation", role="supervisor",
                system_prompt="你是升级专员,处理一线无法解决的复杂问题,派活给 specialist。",
                approval_policy=ApprovalPolicy(level="step"),
                children=[Agent(
                    name="specialist", role="worker",
                    system_prompt="你是技术专家,使用 ticket MCP 解决工单,必要时 read_file 查看文档。",
                    tools=["mcp:ticket:resolve_ticket", "read_file"],
                    max_iterations=8,
                )],
            ),
            # 投诉处理员:升级投诉前需审批
            Agent(
                name="complaint_handler", role="worker",
                system_prompt="你是投诉处理员,使用 ticket MCP 升级投诉工单。升级前需审批。",
                tools=["mcp:ticket:escalate_complaint"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:ticket:escalate_complaint"],
                ),
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "customer_support",
    "title": "客户支持团队",
    "description": "主管→一线→升级专员→投诉处理,挂接工单 MCP,升级与投诉需审批",
    "category": "support",
    "tags": ["客服", "MCP", "审批", "工单"],
    "deps_teams": [],
    "deps_library": [],
}
