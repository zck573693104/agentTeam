from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


@dataclass
class Leader:
    """主管智能体：拆解任务、分配步骤、汇总产出。"""

    name: str = "leader"
    role: str = "主管"
    system_prompt: str = ""
    model: ModelRef | None = None
    approval_policy: ApprovalPolicy | None = None

    def to_agent(
        self, children: list[Agent | TeamRef] | None = None
    ) -> Agent:
        """转换为统一 Agent 节点。children 可在调用处传入。"""
        return Agent(
            name=self.name, role="supervisor",
            system_prompt=self.system_prompt, model=self.model,
            children=list(children) if children else [],
            approval_policy=self.approval_policy,
        )


@dataclass
class Team:
    """一个 Leader + N 个 Worker + 工具集 + 默认模型，编译成一个图。"""

    name: str
    description: str
    leader: Leader
    workers: list[Worker]
    default_model: ModelRef
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
