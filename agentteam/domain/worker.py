from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


@dataclass
class Worker:
    """角色说明书：定义一个 Worker 的职责、模型、工具与审批策略。"""

    name: str
    role: str
    description: str
    system_prompt: str
    model: ModelRef | None = None
    tools: list[str] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None
    max_iterations: int = 10

    def to_agent(self) -> Agent:
        """转换为统一 Agent 节点。"""
        return Agent(
            name=self.name, role="worker",
            system_prompt=self.system_prompt, model=self.model,
            tools=list(self.tools), approval_policy=self.approval_policy,
            max_iterations=self.max_iterations,
        )
