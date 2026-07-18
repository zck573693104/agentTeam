"""专家 Agent 库：name → Agent 定义，支持 $ref 引用复用。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from agentteam.domain.agent import Agent, TeamRef


@dataclass
class AgentLibrary:
    """专家 Agent 库。

    - register(agent): 注册命名 Agent
    - get(name): 取库中 Agent（不拷贝）
    - resolve(agent): 递归解析 ref —— 若 agent.ref 指向库，
      深拷贝库定义，调用处非空字段覆盖模板
    """
    agents: dict[str, Agent] = field(default_factory=dict)

    def register(self, agent: Agent) -> None:
        if agent.name in self.agents:
            raise ValueError(f"Agent already in library: {agent.name}")
        self.agents[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self.agents.get(name)

    def resolve(self, agent: Agent) -> Agent:
        if agent.ref is None:
            # 无 ref：返回拷贝，递归 resolve children
            return Agent(
                name=agent.name, role=agent.role,
                system_prompt=agent.system_prompt, model=agent.model,
                children=[self._resolve_child(c) for c in agent.children],
                approval_policy=agent.approval_policy,
                tools=list(agent.tools),
                max_iterations=agent.max_iterations,
                ref=None,
            )

        # ref 指向库
        if not agent.ref.startswith("library:"):
            raise ValueError(f"Unsupported ref scheme: {agent.ref}")
        lib_name = agent.ref[len("library:"):]
        tmpl = self.agents.get(lib_name)
        if tmpl is None:
            raise KeyError(f"Agent not found in library: {lib_name}")

        resolved = deepcopy(tmpl)
        resolved.ref = None
        # 调用处覆盖：非 None/非空字段覆盖
        if agent.system_prompt:
            resolved.system_prompt = agent.system_prompt
        if agent.model is not None:
            resolved.model = agent.model
        if agent.approval_policy is not None:
            resolved.approval_policy = agent.approval_policy
        if agent.tools:
            resolved.tools = list(agent.tools)
        if agent.max_iterations != 10:
            resolved.max_iterations = agent.max_iterations
        if agent.children:
            # 调用处 children 覆盖模板的 children
            resolved.children = list(agent.children)
        # name 用调用处的
        resolved.name = agent.name
        # 递归 resolve 所有 children（无论来源）
        resolved.children = [self._resolve_child(c) for c in resolved.children]
        return resolved

    def _resolve_child(self, child: Agent | TeamRef) -> Agent | TeamRef:
        if isinstance(child, Agent):
            return self.resolve(child)
        return child  # TeamRef 不解析
