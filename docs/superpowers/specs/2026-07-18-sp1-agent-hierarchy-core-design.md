# SP1 —— Agent 层级核心设计

> 日期：2026-07-18
> 状态：待评审
> 上游目标：企业级 Agent 专家团队（多级层级 + 多级 MCP + 企业能力）
> 子项目定位：5 个子项目中的基础层，后续 SP2（多级 MCP）/ SP3（配置持久化）/ SP4（热更新）/ SP5（预置团队）均依赖本 spec

## 1. 背景与目标

### 1.1 当前架构限制

AgentTeam v1（M1–M6）已实现 Leader-Worker 两层架构：

- `Leader` 与 `Worker` 为两个独立 dataclass，不能递归
- 层级固定为 1 级 supervisor + N 个 worker
- Team 配置中 `leader + workers` 字段是平铺的，无法表达多级派发
- MCP 仅能挂在 Team 级（`Team.mcp_servers`），Worker 不能独立挂 MCP
- 团队定义只能 inline（写在 `examples/dev_team.py`），不可复用

### 1.2 SP1 目标

将领域模型重构为**递归 Agent 图**，支持：

1. **多级 Supervisor 链**：Leader → 子 Leader → Workers，任意深度
2. **Team 嵌套组合**：一个 Team 可作为另一 Team 的 child 节点，子 Team 内部独立编排
3. **专家 Agent 库**：Agent 可独立定义并注册到库，多个 Team 用 `$ref` 引用复用
4. **各层独立审批**：每个 supervisor 节点可配 `approval_policy`，仅作用于本层 plan

### 1.3 非目标（SP1 不做，留给后续 SP）

- MCP 多级挂载（SP2）：本 spec 中 MCP 仍保持 Team 级挂载，但 Agent 模型预留扩展位
- 声明式 YAML/JSON 配置文件（SP3）：本 spec 仅定义 Python dataclass，配置文件格式留给 SP3
- DB 持久化与 CRUD API（SP3）：本 spec 不动 `TeamStore`（内存注册表）
- 运行时热更新（SP4）
- 预置企业场景团队（SP5）

### 1.4 成功标准

- 现有 `tests/` 全部通过（不破坏既有功能）
- 现有 `examples/dev_team.py` 不动一字仍可注册与运行（向后兼容）
- 新增多级层级集成测试：3 级 supervisor 链跑通
- 新增 Team 嵌套集成测试：父 Team 引用子 Team 跑通
- 新增专家库集成测试：`$ref` 引用 + inline 混用跑通
- 新增跨层审批测试：父层 step 级 + 子层 tool 级同时触发，分别 resume 跑通

## 2. 领域模型

### 2.1 新增 `Agent` dataclass

文件：`agentteam/domain/agent.py`（新建）

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Union

from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


@dataclass
class TeamRef:
    """引用另一个 Team 作为本节点的 child。
    
    编译时由 TeamCompiler 从 _team_registry 取出目标 Team，
    编译其 root 作为本节点。alias 用于在父 Team 内重命名，防重名。
    """
    name: str                # 目标 Team 名称
    alias: str | None = None # 在父 Team 内的别名；None 时用 sub_team.root.name


@dataclass
class Agent:
    """统一智能体节点。
    
    - role="supervisor"：派活给 children，跑 plan→children→review 循环
    - role="worker"：叶子节点，跑 ReAct 工具循环
    
    约束（编译期校验）：
    - supervisor 必须有 children，tools 必须为空
    - worker 必须无 children，可有 tools
    - ref 与 children 可同时存在：ref 指向库时，库定义作为模板，
      调用处的 children 覆盖模板的 children（同理 system_prompt/model 等字段）
    """
    name: str
    role: Literal["supervisor", "worker"]
    system_prompt: str = ""
    model: ModelRef | None = None

    # supervisor 专属
    children: list[Union["Agent", "TeamRef"]] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None

    # worker 专属
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 10

    # 专家库引用（解析前填充；解析后由 AgentLibrary.resolve 置空）
    ref: str | None = None  # 格式："library:agent_name"
```

### 2.2 `Team` 重构

文件：`agentteam/domain/team.py`（修改）

```python
@dataclass
class Team:
    """Team 退化为顶层容器：root agent + 默认模型 + 团队级 MCP。
    
    向后兼容：保留 leader/workers 作为 @property，从 root 反推。
    旧代码用 Team.from_legacy(leader=..., workers=...) 构造。
    """
    name: str
    description: str
    root: Agent                       # 新字段：必须是 role="supervisor"
    default_model: ModelRef
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)

    # —— 向后兼容 property ——
    @property
    def leader(self) -> Leader:
        """从 root 反推 Leader（仅 root 为 supervisor 时有意义）。"""
        return Leader(
            name=self.root.name,
            role="主管",
            system_prompt=self.root.system_prompt,
            model=self.root.model,
            approval_policy=self.root.approval_policy,
        )

    @property
    def workers(self) -> list[Worker]:
        """从 root.children 反推 Worker 列表（仅当 children 都是 worker Agent）。"""
        result = []
        for child in self.root.children:
            if isinstance(child, Agent) and child.role == "worker":
                result.append(Worker(
                    name=child.name, role="", description="",
                    system_prompt=child.system_prompt, model=child.model,
                    tools=child.tools, approval_policy=child.approval_policy,
                    max_iterations=child.max_iterations,
                ))
        return result

    @classmethod
    def from_legacy(cls, *, name, description, leader, workers, default_model,
                    skills=None, mcp_servers=None) -> "Team":
        """旧 leader+workers 配置转新 root+children。"""
        root = Agent(
            name=leader.name, role="supervisor",
            system_prompt=leader.system_prompt, model=leader.model,
            children=[w.to_agent() for w in workers],
            approval_policy=leader.approval_policy,
        )
        return cls(name=name, description=description, root=root,
                   default_model=default_model,
                   skills=skills or [], mcp_servers=mcp_servers or [])
```

### 2.3 `Leader`/`Worker` 转薄壳

文件：`agentteam/domain/team.py`、`agentteam/domain/worker.py`（修改）

```python
@dataclass
class Leader:
    """旧 Leader 类，保留作为兼容层。新增 to_agent() 转换方法。"""
    name: str = "leader"
    role: str = "主管"
    system_prompt: str = ""
    model: ModelRef | None = None
    approval_policy: ApprovalPolicy | None = None

    def to_agent(self, children: list[Agent | TeamRef] | None = None) -> Agent:
        return Agent(
            name=self.name, role="supervisor",
            system_prompt=self.system_prompt, model=self.model,
            children=children or [], approval_policy=self.approval_policy,
        )


@dataclass
class Worker:
    """旧 Worker 类，保留作为兼容层。新增 to_agent() 转换方法。"""
    name: str
    role: str
    description: str
    system_prompt: str
    model: ModelRef | None = None
    tools: list[str] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None
    max_iterations: int = 10

    def to_agent(self) -> Agent:
        return Agent(
            name=self.name, role="worker",
            system_prompt=self.system_prompt, model=self.model,
            tools=self.tools, approval_policy=self.approval_policy,
            max_iterations=self.max_iterations,
        )
```

### 2.4 `AgentLibrary` 专家库

文件：`agentteam/domain/library.py`（新建）

```python
from __future__ import annotations
from dataclasses import dataclass, field
from copy import deepcopy

from agentteam.domain.agent import Agent


@dataclass
class AgentLibrary:
    """专家 Agent 库：name → Agent 定义。
    
    - register(agent): 注册命名 Agent
    - get(name): 取库中 Agent（不拷贝）
    - resolve(agent): 递归解析 ref —— 若 agent.ref 指向库，
      深拷贝库定义，保留调用处对其他字段的覆盖
    
    解析规则：
    - agent.ref 为 None → 返回 agent 的拷贝（children 递归 resolve）
    - agent.ref = "library:code_engineer" → 深拷贝库中 "code_engineer"，
      然后用 agent 调用处的非 None/非空字段覆盖：
      system_prompt 非空则覆盖；model 非空则覆盖；children 非空则覆盖；
      approval_policy 非空则覆盖；tools 非空则覆盖；max_iterations != 10 则覆盖
    - name 永远用调用处的（库里的 name 是模板名）
    - resolved.ref 置空（防止二次解析）
    - 库中 Agent 的 children 若也带 ref，递归 resolve
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

    def _resolve_child(self, child):
        if isinstance(child, Agent):
            return self.resolve(child)
        return child  # TeamRef 不解析
```

### 2.5 编译期校验规则

`TeamCompiler._validate(agent, depth)`:

| 校验 | 失败行为 |
|---|---|
| `depth > MAX_DEPTH (=8)` | `ValueError("Max depth exceeded: >8")` |
| `agent.role == "supervisor"` 且 `not agent.children` | `ValueError("supervisor must have children")` |
| `agent.role == "supervisor"` 且 `agent.tools` 非空 | `ValueError("supervisor cannot have tools")` |
| `agent.role == "worker"` 且 `agent.children` 非空 | `ValueError("worker cannot have children")` |
| `agent.ref` 不为空且不匹配 `library:` 前缀 | `ValueError("Unsupported ref scheme")`（resolve 抛） |
| `TeamRef.name` 不在 `_team_registry` | `KeyError("Team not registered: {name}")` |
| 循环 TeamRef（path 中已含同名） | `ValueError("Circular team reference: {path}")` |
| `Team.root.role != "supervisor"` | `ValueError("Team.root must be supervisor")` |

## 3. TeamCompiler 递归编译

### 3.1 类签名

文件：`agentteam/runtime/graph.py`（重写）

```python
class TeamCompiler:
    MAX_DEPTH = 8

    def __init__(self, model_provider, tool_registry, library=None):
        self._mp = model_provider
        self._tr = tool_registry
        self._lib = library or AgentLibrary()
        self._team_registry: dict[str, Team] = {}

    def register_team(self, team: Team) -> None:
        """注册可被 TeamRef 引用的 Team。"""
        self._team_registry[team.name] = team

    def register_library(self, library: AgentLibrary) -> None:
        """注入专家库（也可在构造时传入）。"""
        self._lib = library

    def compile(self, team, checkpointer=None, trace_writer=None, audit_repo=None):
        # 加载 team 级 MCP（沿用现状）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)
        # 校验 root
        if team.root.role != "supervisor":
            raise ValueError("Team.root must be supervisor")
        # 递归编译 root
        return self._compile_agent(
            team.root, team.default_model, checkpointer, trace_writer, audit_repo,
            depth=0, path=f"team:{team.name}",
        )
```

### 3.2 递归编译算法

```python
def _compile_agent(self, agent, default_model, checkpointer, trace_writer, audit_repo,
                   depth, path) -> CompiledGraph:
    # 1. 解析 ref（深拷贝库定义，保留覆盖）
    agent = self._lib.resolve(agent)
    # 2. 校验
    self._validate(agent, depth, path)
    # 3. 按 role 分派
    if agent.role == "worker":
        return self._compile_worker(agent, default_model, trace_writer, audit_repo)
    return self._compile_supervisor(
        agent, default_model, checkpointer, trace_writer, audit_repo, depth, path,
    )


def _compile_supervisor(self, agent, default_model, checkpointer, trace_writer,
                        audit_repo, depth, path) -> CompiledGraph:
    graph = StateGraph(TeamState)
    llm = self._mp.get_llm(agent.model or default_model)

    # leader_plan
    graph.add_node("leader_plan",
        make_leader_plan_node(agent, llm, trace_writer))

    # step_gate（仅当本层 step 级审批）
    step_policy = agent.approval_policy
    has_step_gate = step_policy is not None and step_policy.level == "step"
    if has_step_gate:
        graph.add_node("step_gate",
            make_step_gate(step_policy, trace_writer, audit_repo))

    # 递归编译 children
    child_targets: dict[str, str] = {}  # logical name → physical node name
    worker_gates: dict[str, bool] = {}

    for child in agent.children:
        if isinstance(child, TeamRef):
            sub_team = self._team_registry.get(child.name)
            if sub_team is None:
                raise KeyError(f"Team not registered: {child.name}")
            alias = child.alias or sub_team.root.name
            if alias in path.split("."):
                raise ValueError(f"Circular team reference: {path}.{alias}")
            sub_graph = self._compile_agent(
                sub_team.root, sub_team.default_model, checkpointer,
                trace_writer, audit_repo,
                depth=depth + 1, path=f"{path}.{alias}",
            )
            node_name = f"subteam_{alias}"
            graph.add_node(node_name, sub_graph)
            child_targets[alias] = node_name
            worker_gates[alias] = False
        else:
            sub_graph = self._compile_agent(
                child, default_model, checkpointer, trace_writer, audit_repo,
                depth=depth + 1, path=f"{path}.{child.name}",
            )
            node_name = f"agent_{child.name}"
            graph.add_node(node_name, sub_graph)
            child_targets[child.name] = node_name

            # worker 级审批 gate（仅 worker 角色可能有）
            wp = child.approval_policy
            has_gate = wp is not None and wp.level == "worker"
            if has_gate:
                gate_name = f"worker_gate_{child.name}"
                graph.add_node(
                    gate_name,
                    make_worker_gate(child.name, wp, trace_writer, audit_repo),
                )
            worker_gates[child.name] = has_gate

    # leader_review
    graph.add_node("leader_review",
        make_leader_review_node(agent, llm, trace_writer))

    # 路由目标映射（包含 END）
    physical_targets = {}
    for logical, _ in child_targets.items():
        if worker_gates.get(logical):
            physical_targets[logical] = f"worker_gate_{logical}"
        else:
            physical_targets[logical] = child_targets[logical]
    physical_targets[END] = END

    # 边：START → leader_plan
    graph.add_edge(START, "leader_plan")

    # leader_plan → step_gate 或直接路由
    if has_step_gate:
        graph.add_edge("leader_plan", "step_gate")
        graph.add_conditional_edges("step_gate", route_to_worker, physical_targets)
    else:
        graph.add_conditional_edges("leader_plan", route_from_plan, physical_targets)

    # worker_gate → agent（条件边：拒绝→END）
    for logical, has_gate in worker_gates.items():
        if has_gate:
            gate_name = f"worker_gate_{logical}"
            target_node = child_targets[logical]
            graph.add_conditional_edges(
                gate_name,
                make_route_after_worker_gate(target_node),
                {target_node: target_node, END: END},
            )

    # agent/subteam → leader_review
    for logical, node_name in child_targets.items():
        graph.add_edge(node_name, "leader_review")

    # leader_review → step_gate 或直接路由
    if has_step_gate:
        graph.add_edge("leader_review", "step_gate")
    else:
        graph.add_conditional_edges("leader_review", route_from_review, physical_targets)

    return graph.compile(checkpointer=checkpointer)


def _compile_worker(self, agent, default_model, trace_writer, audit_repo):
    """worker 沿用现有 make_worker_subgraph。"""
    llm = self._mp.get_llm(agent.model or default_model)
    tools = self._tr.get_tools(agent.tools) if agent.tools else []
    return make_worker_subgraph(agent, llm, tools, trace_writer, audit_repo)
```

### 3.3 路由函数调整

`route_from_plan` / `route_from_review` / `route_to_worker` 沿用现有签名，但 `route_from_plan` 内 `f"worker_{plan[0]['worker']}"` 改为通过闭包映射：

```python
# 不再是模块级函数，改为工厂函数
def make_route_from_plan(child_targets: dict[str, str]):
    def route_from_plan(state: TeamState) -> str:
        plan = state.get("plan", [])
        if not plan:
            return END
        return child_targets[plan[0]["worker"]]
    return route_from_plan
```

**兼容性**：模块级 `route_from_plan` / `route_from_review` 保留为旧签名（仅用于旧测试），新代码用 `make_route_*` 工厂。

### 3.4 `make_leader_plan_node` / `make_leader_review_node` 适配

签名从 `(leader: Leader, llm, ...)` 改为 `(agent: Agent, llm, ...)`。`Leader` 特定字段（`name`/`role`/`system_prompt`/`model`/`approval_policy`）在 `Agent` 上都有同名字段，节点工厂内部访问方式不变。仅在类型注解上从 `Leader` 改为 `Agent`，并访问 `agent.name` 等。

`make_worker_subgraph` 同理：从 `Worker` 改为 `Agent`，访问 `agent.tools` / `agent.max_iterations` / `agent.approval_policy`。

## 4. 状态 schema

文件：`agentteam/runtime/state.py`（增量修改）

### 4.1 TeamState 扩展

```python
class TeamState(TypedDict):
    messages: Annotated[list, add_messages]
    task: str
    plan: list[Step]
    current_step: int
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    audit_events: Annotated[list, operator.add]
    run_id: str
    pending_approval: dict | None
    total_tokens: Annotated[int, operator.add]
    # 新增：跨层执行路径追踪
    path: str
```

`path` 在 `RunManager._run_in_background` 初始化为 `"team:{team_name}"`，通过 `initial` state 传入。`TeamCompiler._compile_supervisor` 在构造 `make_leader_plan_node` / `make_leader_review_node` 时把 `path` 作为参数传入节点工厂；节点函数读取 `state.get("path", "")` 写入 trace 事件的 `payload.path` 字段，并在返回值中保留 `path` 不变（不追加）。子 supervisor 子图通过 LangGraph 子图机制独立维护自己的 `path`——子图 invoke 时由父图的 `subteam_{alias}` 节点函数注入 `path=f"{parent_path}.{alias}"` 到子图 initial state。`path` 不参与路由，仅用于 trace 与错误定位。

### 4.2 WorkerState 不变

`WorkerState` 沿用现有字段，worker 子图内部不需要感知层级。

### 4.3 子图状态隔离

LangGraph 子图机制天然隔离：
- 子 supervisor 编译为独立 StateGraph，作为父图的节点
- 共享 key（`messages`/`worker_outputs`/`audit_events`/`total_tokens`/`run_id`/`pending_approval`）自动映射回父图
- 非共享 key（`plan`/`current_step`/`path`）在子图内部独立维护，不冒泡

## 5. 审批策略

### 5.1 各层独立

- `Agent.approval_policy` 仅作用于本 supervisor 的 plan
- 子 Team 的审批独立运作，父 Team 不感知也不阻塞
- LangGraph `interrupt()` + checkpoint 自动定位中断的子图，`RunManager.resume_run` 沿用现有机制

### 5.2 三种粒度（沿用现有）

| 粒度 | 触发时机 | 实现 |
|---|---|---|
| step 级 | 本 supervisor 每次 plan 步骤前 | `step_gate` 节点 + `interrupt()` |
| worker 级 | 本 supervisor 的某 child 执行前 | `worker_gate_{name}` 节点 + `interrupt()`，targets = child name/alias |
| tool 级 | worker 内 ReAct 工具调用前 | `tool_step` 节点 + `interrupt()`，targets = 工具名（沿用现有） |

### 5.3 跨层中断示例

```
root (step 级审批)
  ├─ child A (worker，无审批)
  └─ child B (supervisor，tool 级审批)
       └─ child B1 (worker，tools=[write_file] 审批)
```

执行流程：
1. root.leader_plan → step_gate interrupt → 用户批准
2. 路由到 child A → 执行 → root.leader_review
3. step_gate interrupt → 用户批准
4. 路由到 child B（supervisor 子图）
5. child B.leader_plan → 路由到 child B1
6. child B1.tool_step interrupt → 用户批准 → 执行 write_file
7. child B1.finalize → child B.leader_review → 结束
8. 回到 root.leader_review → 结束

每次 interrupt 都通过 RunManager.resume_run 续跑，LangGraph checkpoint 自动定位。

## 6. 向后兼容

### 6.1 `dev_team.py` 不动

现有 dict 配置仍走 `team_from_dict(data)` → `Team`。`team_from_dict` 内部识别旧 schema（有 `leader`+`workers`）调用 `Team.from_legacy()`，识别新 schema（有 `root`）按新结构解析。

### 6.2 `serializer.py` 扩展

文件：`agentteam/api/serializer.py`（修改）

```python
def team_from_dict(data: dict[str, Any]) -> Team:
    if "root" in data:
        # 新 schema
        return Team(
            name=data["name"],
            description=data["description"],
            root=_agent_from_dict(data["root"]),
            default_model=_model_ref_from_dict(data["default_model"]),
            skills=data.get("skills", []),
            mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
        )
    # 旧 schema（leader + workers）
    leader = _leader_from_dict(data["leader"])
    workers = [_worker_from_dict(w) for w in data["workers"]]
    return Team.from_legacy(
        name=data["name"], description=data["description"],
        leader=leader, workers=workers,
        default_model=_model_ref_from_dict(data["default_model"]),
        skills=data.get("skills", []),
        mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
    )


def _agent_from_dict(d: dict) -> Agent:
    children: list[Agent | TeamRef] = []
    for c in d.get("children", []):
        if c.get("_type") == "TeamRef":
            children.append(TeamRef(name=c["name"], alias=c.get("alias")))
        else:
            children.append(_agent_from_dict(c))
    return Agent(
        name=d["name"], role=d["role"],
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        children=children,
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
        tools=d.get("tools", []),
        max_iterations=d.get("max_iterations", 10),
        ref=d.get("ref"),
    )


def team_to_dict(team: Team) -> dict[str, Any]:
    """新 schema 序列化（旧 schema 不再单独输出）。"""
    return {
        "name": team.name,
        "description": team.description,
        "root": _agent_to_dict(team.root),
        "default_model": asdict(team.default_model),
        "skills": team.skills,
        "mcp_servers": [asdict(s) for s in team.mcp_servers],
    }


def _agent_to_dict(agent: Agent) -> dict:
    children = []
    for c in agent.children:
        if isinstance(c, TeamRef):
            children.append({"_type": "TeamRef", "name": c.name, "alias": c.alias})
        else:
            children.append(_agent_to_dict(c))
    return {
        "name": agent.name, "role": agent.role,
        "system_prompt": agent.system_prompt,
        "model": asdict(agent.model) if agent.model else None,
        "children": children,
        "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        "tools": agent.tools,
        "max_iterations": agent.max_iterations,
        "ref": agent.ref,
    }
```

### 6.3 `TeamStore` 不动

`TeamStore` 继续存 `Team` 对象。旧测试用旧 schema 注册仍可工作（因 `team_from_dict` 自动适配）。

### 6.4 `RunManager` 不动

`RunManager` 仅依赖 `graph.invoke()` 与 `graph.get_state()`，对内部结构无感。递归子图对它透明。

### 6.5 `routes/runs.py` 微调

`runs_router` 在创建 run 时调用 `TeamCompiler.compile(team)`。新增 `library` 与 `team_registry` 依赖：

- `library`：从 `app.state` 取（SP1 中可在 `create_app` 中初始化空库，CLI 注册 dev_team 时同时注册到库）
- `team_registry`：TeamCompiler 内部维护，`compile` 前遍历 `TeamStore` 中所有 team 调 `register_team`，使 TeamRef 可解析

## 7. CLI 与示例

### 7.1 `examples/dev_team.py` 不动

保持现有 dict 结构。验证用：`team_from_dict(DEV_TEAM)` 经 `from_legacy` 转 `Team.root`。

### 7.2 新增 `examples/multi_level_team.py`（验证用）

展示 3 级 supervisor 链 + Team 嵌套 + 专家库引用：

```python
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef

# 专家库
LIB = AgentLibrary()
LIB.register(Agent(
    name="code_engineer", role="worker",
    system_prompt="你是代码工程师，用 read_file/write_file 完成编码任务。",
    tools=["read_file", "write_file"], max_iterations=10,
))

# 子 Team：测试小队
TEST_TEAM = Team(
    name="test_subteam",
    description="测试小队",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管，派活给 tester。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员，写测试用例。",
            tools=["read_file", "write_file"], max_iterations=5,
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)

# 主 Team：3 级 supervisor 链 + Team 嵌套 + 专家库引用
MULTI_LEVEL_TEAM = Team(
    name="multi_level",
    description="3 级层级 + Team 嵌套 + 专家库",
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO，派活给技术副总裁。",
        children=[Agent(
            name="cto", role="supervisor",
            system_prompt="你是 CTO，派活给工程师和测试小队。",
            children=[
                # 专家库引用：复用 code_engineer 模板，覆盖 name
                Agent(name="eng", role="worker", ref="library:code_engineer"),
                # Team 嵌套
                TeamRef(name="test_subteam", alias="qa_team"),
            ],
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)
```

### 7.3 CLI 命令扩展

`agentteam` CLI 新增子命令：

- `agentteam register-team <file.py>` —— 注册任意 Team 配置文件（SP1 仅支持 Python 文件，YAML 留给 SP3）
- `agentteam list-teams` —— 列出已注册 Team
- `agentteam register-library <file.py>` —— 注册专家库

`register-dev-team` 保留不变。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| depth > MAX_DEPTH (8) | 编译期 `ValueError` |
| 循环 TeamRef | 编译期 path 检测 `ValueError` |
| TeamRef 指向未注册 Team | 编译期 `KeyError` |
| Agent role 与字段不符（supervisor 有 tools 等） | 编译期 `ValueError` |
| ref 指向库中不存在的 Agent | `AgentLibrary.resolve` 抛 `KeyError` |
| ref scheme 不支持（非 `library:`） | `AgentLibrary.resolve` 抛 `ValueError` |
| worker tools 引用不存在工具 | 编译期 `KeyError`（沿用现有 ToolRegistry） |
| 子图执行异常 | 沿用现有：异常冒泡到 RunManager，记 error 事件 |
| 子 Team 嵌套层数过深导致递归栈溢出 | MAX_DEPTH 兜底；Python 默认递归深度 1000 远大于 8 |

## 9. 测试策略

### 9.1 单元测试

文件：`tests/domain/test_agent.py`（新建）

- `Agent` 构造与字段约束
- `TeamRef` 数据结构
- `Leader.to_agent()` / `Worker.to_agent()` 转换正确性
- `Team.from_legacy()` 与 `Team.leader`/`Team.workers` property 互逆

文件：`tests/domain/test_library.py`（新建）

- `AgentLibrary.register` 重复注册报错
- `AgentLibrary.get` 取已注册/未注册
- `AgentLibrary.resolve` 三场景：
  - 无 ref → 返回等价 Agent
  - ref 指向库 → 深拷贝 + 字段覆盖
  - ref 与 children 同时存在 → 抛错（在 _validate 而非 resolve）
- 递归 resolve：库中 Agent 的 children 也带 ref

文件：`tests/runtime/test_graph.py`（增量）

- `_validate` 各失败场景
- 编译 3 级 supervisor 链：node_names 包含 `agent_ceo`/`agent_cto`/`agent_eng`/`subteam_qa_team`
- 编译 TeamRef 未注册 → 抛 `KeyError`
- 编译循环 TeamRef → 抛 `ValueError`
- 编译深度超限 → 抛 `ValueError`

### 9.2 集成测试

文件：`tests/integration/test_multi_level.py`（新建）

- **3 级链 E2E**：FakeLLM 设定 CEO 拆 1 步给 CTO，CTO 拆 1 步给 eng，eng 给答案。验证 `worker_outputs["eng"]` 与 `audit_events` 包含 3 个 `leader_plan`。
- **Team 嵌套 E2E**：FakeLLM 设定 CEO 拆 1 步给 qa_team（TeamRef），qa_team 内部 test_lead 拆 1 步给 tester。验证 `worker_outputs["tester"]` 存在。
- **专家库引用 E2E**：`Agent(ref="library:code_engineer")` 被 CEO 直接派活，验证库中 system_prompt 生效。
- **混合 E2E**：上述 3 个特性同时出现在一个 Team 中。

文件：`tests/integration/test_cross_level_approval.py`（新建）

- **父 step + 子 tool 同时触发**：root 配 step 级审批，child worker 配 tool 级审批。先 interrupt 在 root.step_gate → resume → 路由到 child → child tool_step interrupt → resume → 完成。验证 `audit_events` 包含 2 组 `approval_requested`/`approval_decided`。
- **跨层拒绝**：root step 级拒绝 → 图终止，子图不执行。

### 9.3 兼容性测试

文件：`tests/integration/test_legacy_compat.py`（新建）

- `team_from_dict(DEV_TEAM)`（旧 schema）→ `Team` 对象，`root.role == "supervisor"`
- `Team.from_legacy(leader=..., workers=...)` → `Team.leader`/`Team.workers` 反推一致
- 旧 `tests/runtime/test_graph.py` 全部用例不修改通过
- 旧 `tests/integration/test_e2e_*.py` 全部用例不修改通过
- `examples/dev_team.py` 不动，`agentteam register-dev-team` 跑通

### 9.4 测试矩阵

| 测试文件 | 现有/新增 | 数量预估 |
|---|---|---|
| `tests/domain/test_agent.py` | 新增 | 6 |
| `tests/domain/test_library.py` | 新增 | 8 |
| `tests/domain/test_team.py` | 现有 + 增量 | +3 |
| `tests/runtime/test_graph.py` | 现有 + 增量 | +8 |
| `tests/api/test_serializer.py` | 现有 + 增量 | +4 |
| `tests/integration/test_multi_level.py` | 新增 | 4 |
| `tests/integration/test_cross_level_approval.py` | 新增 | 2 |
| `tests/integration/test_legacy_compat.py` | 新增 | 5 |

## 10. 实施顺序建议

为降低风险，建议分 4 个 commit：

1. **Commit 1：领域模型**（不破坏现有功能）
   - 新建 `agent.py` / `library.py`
   - `team.py` 加 `Team.root` + `from_legacy` + property
   - `worker.py` 加 `to_agent()`
   - 单元测试：`test_agent.py` / `test_library.py`

2. **Commit 2：serializer 双轨**
   - `serializer.py` 加新 schema 解析
   - 旧 schema 仍走 `from_legacy`
   - 测试：`test_serializer.py` 加新 schema 用例

3. **Commit 3：TeamCompiler 递归**
   - `graph.py` 重写 `_compile_supervisor` / `_compile_agent`
   - `nodes.py` 类型从 `Leader`/`Worker` 改 `Agent`
   - `state.py` 加 `path` 字段
   - 路由函数改工厂模式，保留旧模块级函数
   - 测试：`test_graph.py` 增量 + `test_multi_level.py`

4. **Commit 4：跨层审批 + CLI + 示例**
   - `test_cross_level_approval.py`
   - `examples/multi_level_team.py`
   - CLI `register-team` / `list-teams` / `register-library`
   - `routes/runs.py` 注入 library 与 team_registry

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 递归子图状态隔离不如预期 | LangGraph 子图机制成熟，且现有 worker 子图已验证；先用最简 3 级链集成测试验证 |
| 向后兼容 property 性能 | `Team.leader`/`workers` 每次调用都构造新对象，但调用频率低（仅序列化与 API 响应），可接受 |
| 旧测试用 `route_from_plan(state) == "worker_coder"` 硬编码节点名 | 保留模块级 `route_from_plan` 旧签名（默认 `f"worker_{name}"`），新代码用工厂模式 |
| `dev_team.py` 的 worker 含 `mcp:git:git_status` 等工具引用 | 不影响：`Worker.to_agent()` 透传 `tools` 列表，编译时 `ToolRegistry.get_tools` 沿用 |
| `AgentLibrary.resolve` 深拷贝性能 | 库通常 <100 个 Agent，每次 compile 调用 resolve 一次，可接受；如需优化可加缓存 |
| 多级层级调试复杂 | `path` 字段 + `trace_writer` 已记录每层 `leader_plan`/`worker_start`，可在 Web UI 树状展示（SP5 实现） |

## 12. 与后续 SP 的衔接

- **SP2（多级 MCP）**：`Agent` 已有 `tools` 字段，SP2 增加 `Agent.mcp_servers`（worker 级）与 `TeamRef.mcp_overrides`（子 Team 级覆盖）。`ToolRegistry` 改造为支持命名空间（`mcp:{server}:{tool}` 已有，扩展为 `mcp:{scope}:{server}:{tool}`）。
- **SP3（配置持久化）**：本 spec 的 `team_to_dict` / `team_from_dict` 新 schema 直接作为 DB 序列化格式。`teams` 表存 `team_to_dict(team)` JSON。`AgentLibrary` 也持久化到 `agents` 表。
- **SP4（热更新）**：`TeamStore` 改为从 DB 读，`RunManager` 在 run 启动时快照 Team 配置，避免 run 中途配置变更。
- **SP5（预置团队）**：基于本 spec 的 `examples/multi_level_team.py` 模式，预置研发/运维/数据分析/客服等场景团队。

## 13. 验收清单

- [ ] `tests/` 全部通过（无回归）
- [ ] `examples/dev_team.py` 一字不改可注册运行
- [ ] 新增 `tests/domain/test_agent.py` 通过
- [ ] 新增 `tests/domain/test_library.py` 通过
- [ ] 新增 `tests/integration/test_multi_level.py` 通过（3 级链 + 嵌套 + 专家库）
- [ ] 新增 `tests/integration/test_cross_level_approval.py` 通过
- [ ] 新增 `tests/integration/test_legacy_compat.py` 通过
- [ ] `examples/multi_level_team.py` 可通过 CLI 注册运行
- [ ] `agentteam register-team` / `list-teams` / `register-library` CLI 可用
- [ ] 编译期校验：depth/cycle/role/ref 互斥等错误场景均抛错
