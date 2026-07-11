from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.approval import ApprovalPolicy
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


@dataclass
class Team:
    """一个 Leader + N 个 Worker + 工具集 + 默认模型，编译成一个图。"""

    name: str
    description: str
    leader: Leader
    workers: list[Worker]
    default_model: ModelRef
    skills: list[str] = field(default_factory=list)
