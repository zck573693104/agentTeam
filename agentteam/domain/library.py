"""专家 Agent 库:name → Agent 定义,支持 $ref 引用复用。"""
from __future__ import annotations

from copy import deepcopy

from agentteam.domain.agent import Agent, TeamRef


class AgentLibrary:
    """专家 Agent 库。

    - register(agent): 注册命名 Agent
    - get(name): 取库中 Agent(不拷贝)
    - resolve(agent): 递归解析 ref —— 若 agent.ref 指向库,
      深拷贝库定义,调用处非空字段覆盖模板

    Persistence:
    - repo=None(默认):纯内存模式,重启后丢失(向后兼容)
    - repo 提供:初始化时从 DB 加载,register 同步到 DB

    Limitations(sentinel 值限制,per spec L230-231):
    ---------------------------------------------------------------
    resolve() 使用 "字段非空才覆盖" 的 sentinel 判定逻辑,这意味着
    调用处无法将模板字段覆盖 *为* 以下"空/默认"值:

    - system_prompt: 无法覆盖为 ""(空字符串)。若模板 system_prompt="模板提示",
      调用处传 system_prompt="" 不会清空,仍保留模板值。
    - tools: 无法覆盖为 [](空列表)。若模板 tools=["read_file"],
      调用处传 tools=[] 不会清空,仍保留模板值。
    - children: 无法覆盖为 [](空列表)。若模板有 children,
      调用处传 children=[] 不会清空,仍保留模板值。
    - max_iterations: 无法覆盖为 10(默认值)。若模板 max_iterations=5,
      调用处传 max_iterations=10 不会还原为 10,仍保留模板值 5。
    - model / approval_policy: 这两个字段默认为 None,调用处传 None 表示
      "不覆盖",因此无法区分"清空为 None"与"不覆盖"两种语义。

    Workaround(绕过限制):
    - 如需清空某字段,直接修改库中模板对象(register 前修改 tmpl)。
    - 如需将 max_iterations 还原为 10,可先用其他值(如 9)触发覆盖,再后处理。
    - 如需彻底清空 tools/system_prompt,建议拆分为多个模板,或不用 ref 直接构造 Agent。

    此限制是 spec 有意为之(避免误清空),如需改变需修正 spec。

    循环引用保护:
    ----------------
    resolve() 内部维护 _visited 路径,追踪当前递归分支已展开的库 ref 名。
    若 A 的 children 中有 ref="library:A",或 A→B→A 形成间接环,
    resolve() 会抛出 ValueError("Circular library reference: A -> B -> A")。
    """

    def __init__(self, agents: dict[str, Agent] | None = None, repo=None) -> None:
        self.agents: dict[str, Agent] = dict(agents) if agents else {}
        self._repo = repo
        if repo is not None:
            for a in repo.list_all():
                self.agents[a.name] = a

    def register(self, agent: Agent) -> None:
        if agent.name in self.agents:
            raise ValueError(f"Agent already in library: {agent.name}")
        self.agents[agent.name] = agent
        if self._repo is not None:
            self._repo.upsert(agent)

    def get(self, name: str) -> Agent | None:
        return self.agents.get(name)

    def resolve(self, agent: Agent, _visited: list[str] | None = None) -> Agent:
        """递归解析 agent.ref，深拷贝库定义并由调用处非空字段覆盖。

        See class docstring "Limitations" section for sentinel value restrictions
        (system_prompt/tools/children/max_iterations 无法覆盖为空或默认值).

        Args:
            agent: 待解析的 Agent。若 ref=None，返回等价拷贝并递归 resolve children。
            _visited: 内部使用的"已展开 ref 名"路径，用于循环引用检测。
                外部调用者不应传此参数。每个递归分支独立维护一份副本
                （通过 `_visited + [lib_name]` 创建新列表），避免兄弟节点之间
                互相干扰。注意：仅当 agent.ref 指向库时才将 lib_name 加入路径，
                无 ref 节点会原样透传 _visited 给 children。

        Raises:
            ValueError: ref scheme 非 "library:"，或检测到循环引用。
            KeyError: ref 指向的库 Agent 不存在。
        """
        if _visited is None:
            _visited = []

        if agent.ref is None:
            # 无 ref：返回拷贝，递归 resolve children
            # 注意：不将当前 agent.name 加入 _visited —— visited 只追踪
            # 被展开的库 ref 名，而非任意 agent；visited 原样透传给 children。
            return Agent(
                name=agent.name, role=agent.role,
                system_prompt=agent.system_prompt, model=agent.model,
                children=[self._resolve_child(c, _visited) for c in agent.children],
                approval_policy=agent.approval_policy,
                tools=list(agent.tools),
                max_iterations=agent.max_iterations,
                ref=None,
                mcp_servers=list(agent.mcp_servers),
            )

        # ref 指向库
        if not agent.ref.startswith("library:"):
            raise ValueError(f"Unsupported ref scheme: {agent.ref}")
        lib_name = agent.ref[len("library:"):]

        # 循环引用检测：lib_name 已在当前路径中 → 抛错并展示链路
        if lib_name in _visited:
            path = _visited + [lib_name]
            raise ValueError(f"Circular library reference: {' -> '.join(path)}")

        tmpl = self.agents.get(lib_name)
        if tmpl is None:
            raise KeyError(f"Agent not found in library: {lib_name}")

        # 进入此 ref 子树前，将 lib_name 追加到 visited
        # 使用 _visited + [lib_name] 创建新列表，使兄弟节点的解析互不影响
        visited = _visited + [lib_name]

        resolved = deepcopy(tmpl)
        resolved.ref = None
        # 调用处覆盖：非 None/非空字段覆盖（详见类文档 "Limitations"）
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
        if agent.mcp_servers:
            resolved.mcp_servers = list(agent.mcp_servers)
        # name 和 role 始终用调用处的（与 name 平行：避免模板 role 静默覆盖
        # 调用处意图，例如 caller 想把 worker 模板实例化为 supervisor）
        resolved.name = agent.name
        resolved.role = agent.role
        # 递归 resolve 所有 children（无论来源），传入更新后的 visited
        resolved.children = [self._resolve_child(c, visited) for c in resolved.children]
        return resolved

    def _resolve_child(
        self, child: Agent | TeamRef, _visited: list[str] | None = None
    ) -> Agent | TeamRef:
        """递归解析 child；TeamRef 直接透传不解析。"""
        if isinstance(child, Agent):
            return self.resolve(child, _visited)
        return child  # TeamRef 不解析
