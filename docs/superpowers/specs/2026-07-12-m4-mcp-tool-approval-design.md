# M4 —— MCP 集成与工具级审批设计

> 日期：2026-07-12
> 状态：已通过设计评审，待实现
> 基线：`feat/m3-approval-trace` (52879af)
> 上游 spec：`2026-07-11-agent-team-design.md` §4.4 / §5.3 / §8.2

## 1. 目标

M4 实现两大特性：

1. **MCP 集成**（§5.3）：定义 `MCPServer` 领域模型，通过 `langchain-mcp-adapters` 加载外部 MCP 工具，注册到 `ToolRegistry` 供 Worker 使用。
2. **工具级审批**（§8.2）：在 Worker ReAct 循环中，调用指定工具前插入 `interrupt()`，支持人工审批。

工具级审批要求将当前单函数 Worker 节点重构为 ReAct 子图（§4.4），因为 `interrupt()` 会导致整个节点在 resume 时重新执行，for 循环内的 interrupt 会破坏 ReAct 状态。

## 2. Worker 子图重构

### 2.1 问题分析

当前 `make_worker_node`（`runtime/nodes.py:61-115`）是单函数节点 + for 循环：

```python
for _ in range(worker.max_iterations):
    response = llm_with_tools.invoke(messages)
    if not tool_calls:
        final_answer = response.content
        break
    for tc in tool_calls:
        result = tool.invoke(tc["args"])
```

如果在这个循环内调用 `interrupt()`，resume 时节点从头重新执行，LLM 会被再次调用 → 可能返回不同的 tool_calls → 状态不一致。

### 2.2 子图设计

将 Worker 拆成 4 个节点的子图：

```
START → init_worker → agent_step
agent_step ──(有 tool_calls 且 iteration < max)──→ tool_step
agent_step ──(最终答案 或 iteration >= max)──→ finalize → END
tool_step ──→ agent_step
```

| 节点 | 职责 |
|---|---|
| `init_worker` | 从 `plan[current_step]` 取 instruction + worker_name；初始化 `react_messages`（SystemMessage + HumanMessage）；`iteration=0`、`final_answer=""`、`tool_calls=[]` |
| `agent_step` | 用 `react_messages` 调 LLM（bind_tools）。有 tool_calls → 写 `tool_calls`、追加 AIMessage 到 `react_messages`；无 tool_calls → 写 `final_answer` |
| `tool_step` | 检查工具级审批策略 → 需要则 `interrupt()` → 执行工具 → ToolMessage 回灌 `react_messages` → `iteration += 1`、清空 `tool_calls` |
| `finalize` | 写 `worker_outputs[worker_name] = final_answer`；追加汇总 AIMessage 到 `messages`；emit `worker_end` trace 事件 |

### 2.3 WorkerState

子图使用独立的 `WorkerState`，共享 TeamState 部分 key（LangGraph 自动映射），加上 worker 内部 key：

```python
class WorkerState(TypedDict):
    # —— 与 TeamState 共享（LangGraph 自动映射）——
    messages: Annotated[list, add_messages]
    plan: list[Step]
    current_step: int
    run_id: str
    pending_approval: dict | None
    audit_events: Annotated[list, operator.add]
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    # —— Worker 内部（不映射回 TeamState）——
    react_messages: Annotated[list, add_messages]
    tool_calls: list[dict]
    iteration: int
    final_answer: str
```

`react_messages`、`tool_calls`、`iteration`、`final_answer` 不在 TeamState 中，子图内部管理，不会污染父图状态。

### 2.4 interrupt() 在 tool_step 中的正确性

`interrupt()` 语义：首次执行时暂停图；resume 时**当前节点从头重新执行**，`interrupt()` 返回 resume 值（不再暂停）。

`tool_step` 的执行流程：

1. **首次执行**：读取 `tool_calls`（来自 `agent_step` 写入的 state）→ 检查审批策略 → `interrupt()` 暂停
2. **resume 重新执行**：`tool_calls` 从 checkpoint 恢复（值不变）→ `interrupt()` 返回决策值 → 执行工具（副作用在 interrupt 之后，只执行一次）→ 返回结果

关键：`tool_calls` 存储在 checkpoint 中的 state 里，resume 时值不变，所以工具调用不会丢失也不会重复。

### 2.5 路由逻辑

```python
def route_after_agent(state: WorkerState) -> str:
    """agent_step 之后：有 tool_calls 且未达上限→tool_step，否则→finalize。"""
    if state.get("final_answer"):
        return "finalize"
    if state.get("iteration", 0) >= worker.max_iterations:
        return "finalize"
    if state.get("tool_calls"):
        return "tool_step"
    return "finalize"
```

`tool_step → agent_step` 为无条件边（循环回）。

### 2.6 max_iterations 处理

当 `iteration >= max_iterations` 时，`agent_step` 的路由跳到 `finalize`。此时 `final_answer` 可能为空（LLM 一直在调工具没给答案），`finalize` 节点用 `react_messages` 中最后一条 AIMessage 的 content 作为兜底答案。

## 3. MCP 集成

### 3.1 MCPServer 领域模型

新建 `agentteam/domain/mcp_server.py`：

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class MCPServer:
    """MCP 服务配置：command/args/env 启动 stdio 子进程，或连接 HTTP 端点。"""
    name: str                              # 唯一标识，如 "fetch" / "git"
    command: str                           # stdio 模式的可执行文件，如 "python" / "npx"
    args: list[str] = field(default_factory=list)   # 如 ["-m", "mcp_server_fetch"]
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio", "http"] = "stdio"
    url: str | None = None                 # transport="http" 时使用
```

### 3.2 Team.mcp_servers 字段

`agentteam/domain/team.py` 的 `Team` 新增：

```python
mcp_servers: list[MCPServer] = field(default_factory=list)
```

### 3.3 ToolRegistry.register_mcp_tools

`agentteam/tools/registry.py` 新增方法，支持注入 loader 便于测试：

```python
class ToolRegistry:
    def __init__(self, mcp_loader=None):
        self._tools: dict[str, BaseTool] = {}
        self._mcp_loader = mcp_loader  # None 时用默认 loader

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        """加载 MCP 工具并注册，加 mcp:{server.name}: 前缀防冲突。"""
        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        registered = []
        for tool in tools:
            tool.name = f"mcp:{server.name}:{tool.name}"
            self.register(tool)
            registered.append(tool.name)
        return registered
```

### 3.4 default_mcp_loader

新建 `agentteam/tools/mcp.py`：

```python
from __future__ import annotations
import asyncio
from langchain_core.tools import BaseTool
from agentteam.domain.mcp_server import MCPServer

def default_mcp_loader(server: MCPServer) -> list[BaseTool]:
    """用 langchain-mcp-adapters 的 MultiServerMCPClient 加载工具。"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_config = {
        server.name: {
            "command": server.command,
            "args": server.args,
            "env": server.env,
            "transport": server.transport,
        }
    }
    if server.transport == "http":
        server_config[server.name] = {"url": server.url, "transport": "http"}

    client = MultiServerMCPClient(server_config)
    return asyncio.run(client.get_tools())
```

`asyncio.run()` 创建临时事件循环执行异步加载。在 `TeamCompiler.compile()`（同步上下文）中调用，此时不在任何事件循环内，安全。

### 3.5 TeamCompiler 集成

`TeamCompiler.compile()` 在编译图之前加载 MCP 工具：

```python
def compile(self, team, checkpointer=None, trace_writer=None, audit_repo=None):
    # 加载 MCP 工具到 registry
    for server in team.mcp_servers:
        self._tr.register_mcp_tools(server)
    # ... 编译图（不变）
```

### 3.6 生命周期说明

Spec §5.3 原文："MCP 子进程在 run 开始时拉起、结束时优雅关闭"。

M4 实现为**编译时 eager loading**：`MultiServerMCPClient.get_tools()` 连接 MCP server、加载工具定义、断开。返回的 `BaseTool` 实例内部持有连接参数，每次 `invoke()` 时重新连接。

这与 spec 的"run 时拉起子进程"略有偏差，但更简单：
- 不需要管理子进程生命周期（启动/关闭/崩溃重启）
- 工具调用是无状态的，天然支持 checkpoint/resume
- 后续如需长连接优化，可替换 `default_mcp_loader` 实现

## 4. 工具级审批

### 4.1 策略

`ApprovalPolicy(level="tool", targets=["write_file", "mcp:git:commit"])` 表示调用 `write_file` 或 `mcp:git:commit` 前需要审批。

### 4.2 审批粒度：按批次

一次 LLM 响应可能返回多个 tool_calls。**按批次审批**：如果批次中任一工具调用匹配 targets，则对整个批次触发一次 `interrupt()`。

审批 payload：
```python
interrupt({
    "gate": "tool",
    "worker": worker.name,
    "tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls],
    "message": f"Worker {worker.name} 请求调用工具: {[tc['name'] for tc in tool_calls]}",
})
```

- **批准**：执行批次中所有工具
- **拒绝**：所有工具调用跳过，回灌 ToolMessage（内容为"工具调用已被拒绝"），LLM 下轮可换方案

### 4.3 tool_step 节点实现

```python
def make_tool_step(worker, tools, policy, trace_writer=None, audit_repo=None):
    tool_map = {t.name: t for t in tools}

    def tool_step(state: WorkerState) -> dict:
        run_id = state.get("run_id", "")
        tool_calls = state.get("tool_calls", [])
        iteration = state.get("iteration", 0)
        react_messages = []  # 追加到 state 的 react_messages

        # 检查是否需要审批
        needs_approval = (
            policy is not None
            and policy.level == "tool"
            and any(_should_approve(policy, tc["name"]) for tc in tool_calls)
        )

        if needs_approval:
            decision = interrupt({
                "gate": "tool",
                "worker": worker.name,
                "tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls],
                "message": f"Worker {worker.name} 请求调用工具: {[tc['name'] for tc in tool_calls]}",
            })
            approved = decision.get("approved", False)
            decider = decision.get("decider", "unknown")

            # 副作用在 interrupt 之后（resume 时执行一次）
            if audit_repo is not None:
                approval_id = audit_repo.add_approval(run_id)
                audit_repo.decide_approval(
                    approval_id, "approved" if approved else "rejected", decider
                )
            if trace_writer is not None:
                trace_writer.emit(run_id, "approval_requested", "system",
                                  {"gate": "tool", "worker": worker.name,
                                   "tools": [tc["name"] for tc in tool_calls]})
                trace_writer.emit(run_id, "approval_decided", decider,
                                  {"gate": "tool", "approved": approved})

            if not approved:
                for tc in tool_calls:
                    react_messages.append(
                        ToolMessage(content="工具调用已被拒绝", tool_call_id=tc["id"])
                    )
                return {
                    "react_messages": react_messages,
                    "tool_calls": [],
                    "iteration": iteration + 1,
                }

        # 执行工具
        if trace_writer is not None:
            trace_writer.emit(run_id, "tool_call", worker.name,
                              {"tools": [tc["name"] for tc in tool_calls]})

        for tc in tool_calls:
            tool = tool_map.get(tc["name"])
            if tool is None:
                result = f"工具 {tc['name']} 不存在"
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as e:
                    result = f"工具执行出错：{type(e).__name__}: {e}"
            react_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        return {
            "react_messages": react_messages,
            "tool_calls": [],
            "iteration": iteration + 1,
        }

    return tool_step
```

### 4.4 与 worker 级 / step 级审批的关系

三种审批粒度可共存：
- **step 级**：`step_gate` 节点（leader_plan 之后）—— M3 已实现
- **worker 级**：`worker_gate_{name}` 节点（worker 执行之前）—— M3 已实现
- **tool 级**：`tool_step` 节点内 interrupt（工具执行之前）—— M4 新增

Worker 级审批门在子图外部（主图节点），工具级审批在子图内部（`tool_step` 节点）。两者互不干扰。

## 5. 文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `agentteam/domain/mcp_server.py` | 新建 | MCPServer dataclass |
| `agentteam/tools/mcp.py` | 新建 | `default_mcp_loader()` |
| `agentteam/domain/team.py` | 修改 | 加 `mcp_servers` 字段 |
| `agentteam/tools/registry.py` | 修改 | 加 `__init__(mcp_loader=)`、`register_mcp_tools()` |
| `agentteam/runtime/state.py` | 修改 | 加 `WorkerState` |
| `agentteam/runtime/nodes.py` | 修改 | worker 拆成 `make_worker_subgraph()`（init/agent_step/tool_step/finalize） |
| `agentteam/runtime/graph.py` | 修改 | 编译 worker 子图，加载 MCP 工具 |
| `agentteam/runtime/__init__.py` | 修改 | 导出新增符号 |
| `pyproject.toml` | 修改 | 加 `langchain-mcp-adapters>=0.3.0` |
| `README.md` | 修改 | M4 标记完成 |

## 6. 测试策略

### 6.1 单元测试

- **MCPServer 模型**：字段默认值、transport 类型
- **ToolRegistry.register_mcp_tools**：注入 fake loader，验证 `mcp:` 前缀、注册正确性、重复注册报错
- **WorkerState**：schema 正确性
- **init_worker 节点**：从 plan/current_step 正确取 instruction，初始化 react_messages
- **agent_step 节点**：mock LLM → 有 tool_calls 时写 tool_calls + 追加 AIMessage；无 tool_calls 时写 final_answer
- **tool_step 节点（无审批）**：执行工具、回灌 ToolMessage、iteration 递增、清空 tool_calls
- **tool_step 节点（有审批）**：interrupt → resume approved → 工具执行；interrupt → resume rejected → 拒绝消息回灌
- **finalize 节点**：写 worker_outputs、messages、trace 事件
- **max_iterations**：达到上限时路由到 finalize，兜底答案取最后 AIMessage

### 6.2 集成测试

- **Worker 子图完整循环**：mock LLM 返回 tool_calls → tool 执行 → LLM 返回最终答案
- **E2E 工具级审批**：完整 run，触发 tool 级 interrupt → resume → 完成
- **E2E MCP 工具**：注入 fake MCP loader，Worker 使用 `mcp:` 前缀工具完成 run
- **M3 回归**：step 级 / worker 级审批仍正常工作

### 6.3 测试基础设施

- `conftest.py` 新增 `fake_mcp_loader` fixture（返回 fake BaseTool 列表）
- 复用 M3 的 `fake_trace_writer`、`fake_audit_repo` fixtures
- Mock LLM 复用 M2/M3 的模式（固定返回 AIMessage with/without tool_calls）

## 7. 依赖

| 新增依赖 | 用途 |
|---|---|
| `langchain-mcp-adapters>=0.3.0` | MCP 工具加载（`MultiServerMCPClient`、`get_tools()`） |

`langchain-mcp-adapters` 会自动拉入 `mcp` Python SDK。无需单独安装 `mcp`。

## 8. 非目标（M4 不做）

- MCP 子进程长连接管理（用 stateless 模式，每次 invoke 独立连接）
- MCP 子进程崩溃重启（v1 不做，工具不可用时 Worker 收到错误消息自行换方案）
- HTTP transport 的认证（仅支持无认证 HTTP 端点）
- 动态加载/卸载 MCP 工具（编译时加载，运行时不可变）
