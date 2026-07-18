# SP2 多级 MCP 挂载设计

> 上游目标：企业级 Agent 专家团队（多级层级 + 多级 MCP + 企业能力）
> 前置依赖：SP1（Agent 层级核心）已完成

## 1. 问题陈述

SP1 完成后，MCP 仅能挂在 Team 级（`Team.mcp_servers`），Worker 与 sub-Team 不能独立挂 MCP。企业场景中：
- 不同 Worker 需要不同工具集（如 coder 挂 git MCP，tester 挂 test MCP）
- sub-Team 引用时需要覆盖或扩展其 MCP 配置
- 当前所有 Worker 共享 Team 级 MCP，无法做工具级隔离

## 2. 设计目标

- Worker 级 MCP：`Agent.mcp_servers` 字段，Worker 自身声明需要的 MCP 服务
- sub-Team 级覆盖：`TeamRef.mcp_overrides` 字段，引用 sub-Team 时可追加 MCP 服务
- 编译期注册：`TeamCompiler` 递归编译时，在每个 Agent 层级注册其 mcp_servers
- 向后兼容：现有 `Team.mcp_servers` 与 `mcp:{server}:{tool}` 命名空间不变

## 3. 数据模型变更

### 3.1 Agent 新增 mcp_servers 字段

```python
@dataclass
class Agent:
    name: str
    role: Literal["supervisor", "worker"]
    system_prompt: str = ""
    model: ModelRef | None = None
    children: list[Union["Agent", "TeamRef"]] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 10
    ref: str | None = None
    mcp_servers: list[MCPServer] = field(default_factory=list)  # 新增
```

约束：
- worker 角色可同时有 `tools`（引用已注册工具名）和 `mcp_servers`（声明 MCP 服务，编译期注册）
- supervisor 角色可有 `mcp_servers`（供其自身在 leader_plan/leader_review 中使用，虽然当前 supervisor 不直接调工具，但预留扩展）
- 编译期注册的 MCP 工具名仍为 `mcp:{server.name}:{tool.name}`，Agent.tools 中引用此全名

### 3.2 TeamRef 新增 mcp_overrides 字段

```python
@dataclass
class TeamRef:
    name: str
    alias: str | None = None
    mcp_overrides: list[MCPServer] = field(default_factory=list)  # 新增
```

语义：
- `mcp_overrides` 是**扩展**语义，不是替换：sub-Team 自身的 `mcp_servers` 仍然注册，`mcp_overrides` 追加注册额外服务
- 若 `mcp_overrides` 中的 server.name 与 sub-Team 已有 server 重名，由于 `register_mcp_tools` 幂等，后者跳过（不覆盖）

## 4. 编译流程变更

### 4.1 TeamCompiler._compile_agent

在 `_validate` 之后、分派之前，注册当前 Agent 的 mcp_servers：

```python
def _compile_agent(self, agent, default_model, checkpointer, trace_writer, audit_repo, depth, path):
    agent = self._lib.resolve(agent)
    self._validate(agent, depth, path)
    # 新增：注册 Agent 级 MCP
    for server in agent.mcp_servers:
        self._tr.register_mcp_tools(server)
    if agent.role == "worker":
        return self._compile_worker(agent, default_model, trace_writer, audit_repo)
    return self._compile_supervisor(agent, default_model, checkpointer, trace_writer, audit_repo, depth, path)
```

### 4.2 TeamRef 编译

在 `_compile_supervisor` 中处理 TeamRef 时，注册 mcp_overrides：

```python
if isinstance(child, TeamRef):
    sub_team = self._team_registry.get(child.name)
    # ...
    # 新增：注册 TeamRef 的 mcp_overrides（在编译 sub_team 之前）
    for server in child.mcp_overrides:
        self._tr.register_mcp_tools(server)
    sub_graph = self._compile_agent(sub_team.root, ...)
```

## 5. 序列化变更

### 5.1 Agent 序列化

`_agent_to_dict` 新增 `mcp_servers` 字段：
```python
def _agent_to_dict(agent):
    return {
        # ... 现有字段 ...
        "mcp_servers": [asdict(s) for s in agent.mcp_servers],  # 新增
    }
```

`_agent_from_dict` 新增解析：
```python
def _agent_from_dict(d):
    return Agent(
        # ... 现有字段 ...
        mcp_servers=[_mcp_server_from_dict(s) for s in d.get("mcp_servers", [])],  # 新增
    )
```

### 5.2 TeamRef 序列化

`_teamref_to_dict`（内联于 `_agent_to_dict` 的 children 处理）：
```python
if isinstance(c, TeamRef):
    children.append({
        "_type": "TeamRef",
        "name": c.name,
        "alias": c.alias,
        "mcp_overrides": [asdict(s) for s in c.mcp_overrides],  # 新增
    })
```

`_teamref_from_dict`：
```python
if c.get("_type") == "TeamRef":
    children.append(TeamRef(
        name=c["name"],
        alias=c.get("alias"),
        mcp_overrides=[_mcp_server_from_dict(s) for s in c.get("mcp_overrides", [])],
    ))
```

## 6. AgentLibrary.resolve 变更

`resolve` 方法需处理 `mcp_servers` 字段（与 `tools` 类似的覆盖语义）：

```python
# 在 resolve 的 ref 分支中：
if agent.mcp_servers:
    resolved.mcp_servers = list(agent.mcp_servers)
# 否则保留 template 的 mcp_servers
```

sentinel 限制（与 tools 一致）：无法通过覆盖清空 mcp_servers 为空列表。

## 7. 命名空间策略

保持现有 `mcp:{server.name}:{tool.name}` 命名空间，**不引入 scope 前缀**。

理由：
- `register_mcp_tools` 已幂等，同名 server 重复注册跳过
- 企业场景中同一 MCP 服务（如 git）通常共享，不需隔离
- 引入 scope 前缀会增加 Agent.tools 引用复杂度（需知道挂载点的 scope）

已知限制（文档记录）：若两个 Agent 挂载同名但不同配置的 MCP server，后者注册被跳过。规避：用不同 server.name。

## 8. 向后兼容

- `Team.mcp_servers` 字段与编译期注册逻辑不变
- 旧 schema（leader+workers）的序列化/反序列化不受影响（mcp_servers 在 Team 级）
- 新 schema（root）中 Agent 与 TeamRef 的新字段默认空列表，不影响现有配置
- `mcp:{server}:{tool}` 命名空间不变

## 9. 测试策略

- 单元测试：Agent/TeamRef 新字段默认值、序列化往返
- 编译测试：Agent.mcp_servers 注册、TeamRef.mcp_overrides 注册
- E2E 测试：Worker 级 MCP 工具调用、sub-Team 级 MCP 覆盖
- 向后兼容：现有 Team.mcp_servers 配置仍工作

## 10. 交付物

- `agentteam/domain/agent.py` — Agent + TeamRef 新增 mcp_servers/mcp_overrides 字段
- `agentteam/runtime/graph.py` — TeamCompiler 注册 Agent/TeamRef 级 MCP
- `agentteam/api/serializer.py` — 序列化新字段
- `agentteam/domain/library.py` — resolve 处理 mcp_servers
- `examples/multi_level_mcp.py` — 多级 MCP 示例
- 测试文件若干
