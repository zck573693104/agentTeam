from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


@dataclass
class Leader:
    """主管智能体：拆解任务、分配步骤、汇总产出。

    保留作为兼容层；新代码用 Agent(role="supervisor")。
    """

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


@dataclass(init=False)
class Team:
    """Team：顶层容器，root agent + 默认模型 + 团队级 MCP。

    两种构造方式：
    1. 新方式：Team(root=agent, ...)  —— 直接用 Agent 树
    2. 旧方式：Team(leader=..., workers=...)  —— 兼容，__init__ 内转 root

    property leader/workers 从 root 反推。
    """

    name: str
    description: str
    default_model: ModelRef
    root: Agent | None
    skills: list[str]
    mcp_servers: list[MCPServer]

    def __init__(
        self,
        *,
        name: str,
        description: str,
        default_model: ModelRef,
        root: Agent | None = None,
        leader: Leader | None = None,
        workers: list[Worker] | None = None,
        skills: list[str] | None = None,
        mcp_servers: list[MCPServer] | None = None,
    ):
        self.name = name
        self.description = description
        self.default_model = default_model
        self.skills = list(skills) if skills else []
        self.mcp_servers = list(mcp_servers) if mcp_servers else []
        if root is not None:
            self.root = root
        elif leader is not None:
            self.root = leader.to_agent(
                children=[w.to_agent() for w in (workers or [])]
            )
        else:
            raise ValueError("Team requires either root or leader+workers")

    @property
    def leader(self) -> Leader:
        return Leader(
            name=self.root.name, role="主管",
            system_prompt=self.root.system_prompt,
            model=self.root.model,
            approval_policy=self.root.approval_policy,
        )

    @leader.setter
    def leader(self, value: Leader | None) -> None:
        if value is None:
            return
        children = list(self.root.children) if self.root else []
        self.root = value.to_agent(children=children)

    @property
    def workers(self) -> list[Worker]:
        result: list[Worker] = []
        for child in self.root.children:
            if isinstance(child, Agent) and child.role == "worker":
                result.append(Worker(
                    name=child.name, role="", description="",
                    system_prompt=child.system_prompt, model=child.model,
                    tools=list(child.tools),
                    approval_policy=child.approval_policy,
                    max_iterations=child.max_iterations,
                ))
        return result

    @workers.setter
    def workers(self, value: list[Worker] | None) -> None:
        if self.root is None or value is None:
            return
        self.root = Agent(
            name=self.root.name, role=self.root.role,
            system_prompt=self.root.system_prompt, model=self.root.model,
            children=[w.to_agent() for w in value],
            approval_policy=self.root.approval_policy,
        )

    @classmethod
    def from_legacy(
        cls, *, name, description, leader, workers, default_model,
        skills=None, mcp_servers=None,
    ) -> "Team":
        root = leader.to_agent(children=[w.to_agent() for w in workers])
        return cls(
            name=name, description=description, root=root,
            default_model=default_model,
            skills=list(skills) if skills else [],
            mcp_servers=list(mcp_servers) if mcp_servers else [],
        )
