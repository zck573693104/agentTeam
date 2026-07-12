# M4 — MCP 集成与工具级审批 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Worker 重构为 ReAct 子图（支持工具级 interrupt 审批），并集成 MCP 工具加载。

**Architecture:** Worker 单函数节点 → 4 节点子图（init_worker → agent_step → tool_step → 循环）。tool_step 内按批次 interrupt 审批。MCP 工具通过 langchain-mcp-adapters 在编译时 eager 加载，注册到 ToolRegistry。

**Tech Stack:** LangGraph 1.1.3 (StateGraph 子图、interrupt)、langchain-mcp-adapters 0.3.0 (MCP 工具加载)、langchain-core 1.4.8 (BaseTool, messages)

**Spec:** `docs/superpowers/specs/2026-07-12-m4-mcp-tool-approval-design.md`

**基线分支:** `feat/m4-mcp-tool-approval`（从 `feat/m3-approval-trace` @ 52879af 创建）

---

## 子图路由设计

```
START → init_worker → agent_step
agent_step ──(有 tool_calls)──→ tool_step
agent_step ──(无 tool_calls)──→ finalize → END
tool_step ──(iteration < max)──→ agent_step
tool_step ──(iteration >= max)──→ finalize → END
```

此路由保证 LLM 被调用恰好 `max_iterations` 次（与 M2 行为一致）：每次 `agent_step` 调 LLM，`tool_step` 递增 iteration，达到上限后路由到 finalize 而非再次调 LLM。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `agentteam/domain/mcp_server.py` | **新建** — MCPServer dataclass |
| `agentteam/tools/mcp.py` | **新建** — default_mcp_loader()，lazy import langchain-mcp-adapters |
| `agentteam/domain/team.py` | 修改 — 加 mcp_servers 字段 |
| `agentteam/domain/__init__.py` | 修改 — 导出 MCPServer |
| `agentteam/tools/registry.py` | 修改 — 加 mcp_loader 参数、register_mcp_tools() |
| `agentteam/runtime/state.py` | 修改 — 加 WorkerState |
| `agentteam/runtime/nodes.py` | 修改 — 加 make_init_worker/make_agent_step/make_tool_step/make_finalize/make_worker_subgraph，重写 make_worker_node 为子图包装器 |
| `agentteam/runtime/graph.py` | 修改 — TeamCompiler 使用 make_worker_subgraph，编译时加载 MCP 工具 |
| `agentteam/runtime/__init__.py` | 修改 — 导出 WorkerState, make_worker_subgraph, MCPServer |
| `pyproject.toml` | 修改 — 加 langchain-mcp-adapters 依赖 |
| `README.md` | 修改 — M4 标记完成 |
| `tests/conftest.py` | 修改 — 加 fake_mcp_loader fixture |
| `tests/domain/test_mcp_server.py` | **新建** — MCPServer 模型测试 |
| `tests/tools/test_registry.py` | 修改 — 加 register_mcp_tools 测试 |
| `tests/runtime/test_state.py` | 修改 — 加 WorkerState 测试 |
| `tests/runtime/test_nodes.py` | 修改 — 加子图节点测试，更新现有 worker 测试 |
| `tests/runtime/test_graph.py` | 修改 — 加 E2E 工具级审批 + MCP 测试 |

---

## Task 1: MCPServer 领域模型 + Team.mcp_servers 字段

**Files:**
- Create: `agentteam/domain/mcp_server.py`
- Modify: `agentteam/domain/team.py`
- Modify: `agentteam/domain/__init__.py`
- Create: `tests/domain/test_mcp_server.py`
- Modify: `tests/domain/test_team.py`

- [ ] **Step 1: 写 MCPServer 模型测试**

Create `tests/domain/test_mcp_server.py`:

```python
from agentteam.domain.mcp_server import MCPServer


def test_mcp_server_stdio_defaults():
    """stdio 模式的 MCPServer 有合理默认值。"""
    server = MCPServer(name="fetch", command="python")
    assert server.name == "fetch"
    assert server.command == "python"
    assert server.args == []
    assert server.env == {}
    assert server.transport == "stdio"
    assert server.url is None


def test_mcp_server_with_args_and_env():
    """MCPServer 支持 args 和 env。"""
    server = MCPServer(
        name="git",
        command="uvx",
        args=["mcp-server-git"],
        env={"GIT_REPO": "/tmp/repo"},
    )
    assert server.args == ["mcp-server-git"]
    assert server.env["GIT_REPO"] == "/tmp/repo"


def test_mcp_server_http_transport():
    """HTTP 模式的 MCPServer 使用 url 而非 command。"""
    server = MCPServer(
        name="remote",
        command="",
        transport="http",
        url="http://localhost:8080/mcp",
    )
    assert server.transport == "http"
    assert server.url == "http://localhost:8080/mcp"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/domain/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agentteam.domain.mcp_server'`

- [ ] **Step 3: 实现 MCPServer**

Create `agentteam/domain/mcp_server.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MCPServer:
    """MCP 服务配置：command/args/env 启动 stdio 子进程，或连接 HTTP 端点。"""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio", "http"] = "stdio"
    url: str | None = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/domain/test_mcp_server.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 写 Team.mcp_servers 字段测试**

Add to `tests/domain/test_team.py`:

```python
def test_team_with_mcp_servers():
    from agentteam.domain.mcp_server import MCPServer

    leader = Leader(system_prompt="你是主管")
    server = MCPServer(name="fetch", command="python", args=["-m", "mcp_server_fetch"])
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
        mcp_servers=[server],
    )
    assert len(team.mcp_servers) == 1
    assert team.mcp_servers[0].name == "fetch"


def test_team_mcp_servers_defaults_empty():
    leader = Leader(system_prompt="你是主管")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.mcp_servers == []
```

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/domain/test_team.py::test_team_with_mcp_servers -v`
Expected: FAIL — `TypeError: Team.__init__() got an unexpected keyword argument 'mcp_servers'`

- [ ] **Step 7: 给 Team 加 mcp_servers 字段**

Modify `agentteam/domain/team.py` — 在 `Team` dataclass 的 `skills` 字段后加 `mcp_servers`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

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
```

- [ ] **Step 8: 更新 domain/__init__.py 导出 MCPServer**

Modify `agentteam/domain/__init__.py`:

```python
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker

__all__ = ["ApprovalPolicy", "Leader", "MCPServer", "Team", "Worker"]
```

- [ ] **Step 9: 运行所有 domain 测试确认通过**

Run: `python -m pytest tests/domain/ -v`
Expected: PASS (all domain tests)

- [ ] **Step 10: Commit**

```bash
git add agentteam/domain/mcp_server.py agentteam/domain/team.py agentteam/domain/__init__.py tests/domain/test_mcp_server.py tests/domain/test_team.py
git commit -m "feat(domain): add MCPServer model and Team.mcp_servers field"
```

---

## Task 2: ToolRegistry.register_mcp_tools + default_mcp_loader

**Files:**
- Modify: `agentteam/tools/registry.py`
- Create: `agentteam/tools/mcp.py`
- Modify: `tests/tools/test_registry.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: 写 register_mcp_tools 测试**

Add to `tests/tools/test_registry.py`:

```python
def test_register_mcp_tools_with_fake_loader():
    """register_mcp_tools 用注入的 loader 加载工具，加 mcp: 前缀。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    fake_tools = [_make_tool("fetch"), _make_tool("search")]
    fake_loader = lambda server: fake_tools  # noqa: E731

    reg = ToolRegistry(mcp_loader=fake_loader)
    server = MCPServer(name="remote", command="python")
    registered = reg.register_mcp_tools(server)

    assert set(registered) == {"mcp:remote:fetch", "mcp:remote:search"}
    assert set(reg.list_names()) == {"mcp:remote:fetch", "mcp:remote:search"}


def test_register_mcp_tools_get_by_prefixed_name():
    """注册后可用 mcp: 前缀名获取工具。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    fake_tools = [_make_tool("fetch")]
    reg = ToolRegistry(mcp_loader=lambda s: fake_tools)
    server = MCPServer(name="srv", command="python")
    reg.register_mcp_tools(server)

    tools = reg.get_tools(["mcp:srv:fetch"])
    assert len(tools) == 1
    assert tools[0].name == "mcp:srv:fetch"


def test_register_mcp_tools_empty_returns_empty_list():
    """MCP server 无工具时返回空列表，不报错。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry(mcp_loader=lambda s: [])
    server = MCPServer(name="empty", command="python")
    registered = reg.register_mcp_tools(server)
    assert registered == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/tools/test_registry.py::test_register_mcp_tools_with_fake_loader -v`
Expected: FAIL — `TypeError: ToolRegistry.__init__() got an unexpected keyword argument 'mcp_loader'`

- [ ] **Step 3: 实现 default_mcp_loader**

Create `agentteam/tools/mcp.py`:

```python
from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer


def default_mcp_loader(server: MCPServer) -> list[BaseTool]:
    """用 langchain-mcp-adapters 的 MultiServerMCPClient 加载 MCP 工具。

    lazy import：仅在实际调用时引入 langchain-mcp-adapters，
    测试中通过注入 fake loader 避免安装此依赖。
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    if server.transport == "http":
        server_config = {
            server.name: {"url": server.url, "transport": "http"}
        }
    else:
        server_config = {
            server.name: {
                "command": server.command,
                "args": server.args,
                "env": server.env,
                "transport": "stdio",
            }
        }

    import asyncio

    client = MultiServerMCPClient(server_config)
    return asyncio.run(client.get_tools())
```

- [ ] **Step 4: 实现 ToolRegistry.register_mcp_tools**

Modify `agentteam/tools/registry.py` — 替换整个文件:

```python
from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self, mcp_loader=None) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mcp_loader = mcp_loader  # None 时用 default_mcp_loader

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        """加载 MCP 工具并注册，加 mcp:{server.name}: 前缀防冲突。"""
        from agentteam.tools.mcp import default_mcp_loader

        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        registered = []
        for tool in tools:
            tool.name = f"mcp:{server.name}:{tool.name}"
            self.register(tool)
            registered.append(tool.name)
        return registered

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not found: {missing}")
        return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/tools/test_registry.py -v`
Expected: PASS (all registry tests including new ones)

- [ ] **Step 6: 全量回归测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (93 existing + 3 new = 96)

- [ ] **Step 7: Commit**

```bash
git add agentteam/tools/registry.py agentteam/tools/mcp.py tests/tools/test_registry.py
git commit -m "feat(tools): add ToolRegistry.register_mcp_tools with injectable loader"
```

---

## Task 3: WorkerState

**Files:**
- Modify: `agentteam/runtime/state.py`
- Modify: `tests/runtime/test_state.py`

- [ ] **Step 1: 写 WorkerState 测试**

Add to `tests/runtime/test_state.py`:

```python
def test_worker_state_typeddict_accepts_fields():
    """WorkerState 包含共享字段和 worker 内部字段。"""
    from agentteam.runtime.state import WorkerState

    state: WorkerState = {
        "messages": [],
        "plan": [],
        "current_step": 0,
        "run_id": "run-1",
        "pending_approval": None,
        "audit_events": [],
        "worker_outputs": {},
        "react_messages": [],
        "tool_calls": [],
        "iteration": 0,
        "final_answer": "",
    }
    assert state["iteration"] == 0
    assert state["final_answer"] == ""
    assert state["tool_calls"] == []
    assert state["react_messages"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_state.py::test_worker_state_typeddict_accepts_fields -v`
Expected: FAIL — `ImportError: cannot import name 'WorkerState'`

- [ ] **Step 3: 实现 WorkerState**

Modify `agentteam/runtime/state.py` — 在文件末尾（`is_rejected` 之后）加:

```python
class WorkerState(TypedDict):
    """Worker 子图状态。

    共享 key（与 TeamState 同名）由 LangGraph 自动映射到父图；
    worker 内部 key 不映射回 TeamState，子图内部管理。
    """

    # —— 与 TeamState 共享 ——
    messages: Annotated[list, add_messages]
    plan: list[Step]
    current_step: int
    run_id: str
    pending_approval: dict | None
    audit_events: Annotated[list, operator.add]
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    # —— Worker 内部 ——
    react_messages: Annotated[list, add_messages]
    tool_calls: list[dict]
    iteration: int
    final_answer: str
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/runtime/test_state.py -v`
Expected: PASS (all state tests)

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/state.py tests/runtime/test_state.py
git commit -m "feat(runtime): add WorkerState for worker subgraph"
```

---

## Task 4: Worker 子图节点 — init_worker / agent_step / finalize

**Files:**
- Modify: `agentteam/runtime/nodes.py`
- Modify: `tests/runtime/test_nodes.py`

- [ ] **Step 1: 写 init_worker 测试**

Add to `tests/runtime/test_nodes.py` (after existing imports, add new imports at top):

```python
from langchain_core.messages import HumanMessage, SystemMessage
from agentteam.runtime.nodes import make_init_worker
```

Add these test functions:

```python
def test_init_worker_sets_react_messages(fake_llm):
    """init_worker 从 plan/current_step 取 instruction，初始化 react_messages。"""
    worker = Worker(name="coder", role="r", description="", system_prompt="你是代码工程师")
    node = make_init_worker(worker)
    state = {
        "plan": [{"worker": "coder", "instruction": "写 hello", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 2
    assert isinstance(result["react_messages"][0], SystemMessage)
    assert isinstance(result["react_messages"][1], HumanMessage)
    assert result["react_messages"][0].content == "你是代码工程师"
    assert result["react_messages"][1].content == "写 hello"


def test_init_worker_resets_iteration_and_tool_calls(fake_llm):
    """init_worker 初始化 iteration=0, tool_calls=[], final_answer=""。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_init_worker(worker)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)
    assert result["iteration"] == 0
    assert result["tool_calls"] == []
    assert result["final_answer"] == ""


def test_init_worker_emits_worker_start_trace(fake_llm, fake_trace_writer):
    """init_worker emit worker_start 轨迹事件。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_init_worker(worker, trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "worker_start"
    assert fake_trace_writer.events[0]["actor"] == "w1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_nodes.py::test_init_worker_sets_react_messages -v`
Expected: FAIL — `ImportError: cannot import name 'make_init_worker'`

- [ ] **Step 3: 实现 make_init_worker**

Modify `agentteam/runtime/nodes.py` — 在 `make_worker_node` 函数之前加:

```python
def make_init_worker(
    worker: Worker,
    trace_writer: TraceWriter | None = None,
):
    """创建 init_worker 节点：初始化 ReAct 循环的 react_messages 和计数器。"""

    def init_worker(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        step = state["plan"][state["current_step"]]
        instruction = step["instruction"]

        if trace_writer:
            trace_writer.emit(run_id, "worker_start", worker.name)

        return {
            "react_messages": [
                SystemMessage(content=worker.system_prompt),
                HumanMessage(content=instruction),
            ],
            "tool_calls": [],
            "iteration": 0,
            "final_answer": "",
        }

    return init_worker
```

- [ ] **Step 4: 运行 init_worker 测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k init_worker -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 写 agent_step 测试**

Add to `tests/runtime/test_nodes.py`:

```python
from agentteam.runtime.nodes import make_agent_step


def test_agent_step_with_tool_calls(fake_llm):
    """agent_step 有 tool_calls 时写入 tool_calls，追加 AIMessage 到 react_messages。"""
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}],
        ),
    ])
    worker = Worker(name="w1", role="r", description="", system_prompt="test", max_iterations=5)
    from agentteam.tools.skills.file_ops import read_file
    node = make_agent_step(worker, fake_llm, [read_file])
    state = {
        "react_messages": [SystemMessage(content="test"), HumanMessage(content="do x")],
        "iteration": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 1  # AIMessage appended
    assert result["tool_calls"] == [{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}]
    assert result["final_answer"] == ""


def test_agent_step_with_final_answer(fake_llm):
    """agent_step 无 tool_calls 时写入 final_answer。"""
    fake_llm.set_invoke_responses([AIMessage(content="任务完成")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_agent_step(worker, fake_llm, [])
    state = {
        "react_messages": [SystemMessage(content="test"), HumanMessage(content="do x")],
        "iteration": 0,
    }
    result = node(state)
    assert result["final_answer"] == "任务完成"
    assert result["tool_calls"] == []


def test_agent_step_appends_ai_message_to_react(fake_llm):
    """agent_step 始终追加 AIMessage 到 react_messages（不论有无 tool_calls）。"""
    fake_llm.set_invoke_responses([AIMessage(content="思考中...")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_agent_step(worker, fake_llm, [])
    state = {
        "react_messages": [SystemMessage(content="test")],
        "iteration": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 1
    assert isinstance(result["react_messages"][0], AIMessage)
```

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_nodes.py -k agent_step -v`
Expected: FAIL — `ImportError: cannot import name 'make_agent_step'`

- [ ] **Step 7: 实现 make_agent_step**

Modify `agentteam/runtime/nodes.py` — 在 `make_init_worker` 之后加:

```python
def make_agent_step(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
):
    """创建 agent_step 节点：LLM 决策调用工具或给出最终答案。"""

    llm_with_tools = llm.bind_tools(tools) if tools else llm

    def agent_step(state: dict) -> dict:
        react_messages = state.get("react_messages", [])
        response = llm_with_tools.invoke(react_messages)

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return {
                "react_messages": [response],
                "tool_calls": tool_calls,
                "final_answer": "",
            }
        return {
            "react_messages": [response],
            "tool_calls": [],
            "final_answer": response.content,
        }

    return agent_step
```

- [ ] **Step 8: 运行 agent_step 测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k agent_step -v`
Expected: PASS (3 tests)

- [ ] **Step 9: 写 finalize 测试**

Add to `tests/runtime/test_nodes.py`:

```python
from agentteam.runtime.nodes import make_finalize


def test_finalize_writes_worker_output(fake_llm):
    """finalize 写 worker_outputs 和汇总 messages。"""
    worker = Worker(name="coder", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "print('hello')",
        "react_messages": [],
        "run_id": "run-1",
    }
    result = node(state)
    assert result["worker_outputs"] == {"coder": "print('hello')"}
    assert len(result["messages"]) == 1
    assert "coder" in result["messages"][0].content
    assert len(result["audit_events"]) == 1
    assert result["audit_events"][0]["event_type"] == "worker_end"


def test_finalize_fallback_to_last_ai_message(fake_llm):
    """final_answer 为空时（max_iterations 达上限），用最后一条 AIMessage 兜底。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "",
        "react_messages": [
            SystemMessage(content="test"),
            HumanMessage(content="do x"),
            AIMessage(content="还在思考..."),
        ],
        "run_id": "run-1",
    }
    result = node(state)
    assert result["worker_outputs"]["w1"] == "还在思考..."


def test_finalize_emits_worker_end_trace(fake_llm, fake_trace_writer):
    """finalize emit worker_end 轨迹事件。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_finalize(worker, trace_writer=fake_trace_writer)
    state = {
        "final_answer": "done",
        "react_messages": [],
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "worker_end"
    assert fake_trace_writer.events[0]["actor"] == "w1"
```

- [ ] **Step 10: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_nodes.py -k finalize -v`
Expected: FAIL — `ImportError: cannot import name 'make_finalize'`

- [ ] **Step 11: 实现 make_finalize**

Modify `agentteam/runtime/nodes.py` — 在 `make_agent_step` 之后加:

```python
def make_finalize(
    worker: Worker,
    trace_writer: TraceWriter | None = None,
):
    """创建 finalize 节点：写 worker_outputs、汇总 messages、emit worker_end。"""

    def finalize(state: dict) -> dict:
        run_id = state.get("run_id", "")
        final_answer = state.get("final_answer", "")

        # max_iterations 达上限时，用最后一条 AIMessage 兜底
        if not final_answer:
            react_messages = state.get("react_messages", [])
            for msg in reversed(react_messages):
                if isinstance(msg, AIMessage):
                    final_answer = msg.content
                    break

        if trace_writer:
            trace_writer.emit(
                run_id, "worker_end", worker.name,
                {"answer_length": len(final_answer)},
            )
        return {
            "worker_outputs": {worker.name: final_answer},
            "messages": [
                AIMessage(content=f"[{worker.name}] {final_answer}", name=worker.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": worker.name}],
        }

    return finalize
```

- [ ] **Step 12: 运行所有新节点测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k "init_worker or agent_step or finalize" -v`
Expected: PASS (9 tests)

- [ ] **Step 13: 全量回归测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (96 existing + 9 new = 105)

- [ ] **Step 14: Commit**

```bash
git add agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add init_worker, agent_step, finalize subgraph nodes"
```

---

## Task 5: tool_step 节点（无审批 + 工具级审批）

**Files:**
- Modify: `agentteam/runtime/nodes.py`
- Modify: `tests/runtime/test_nodes.py`

- [ ] **Step 1: 写 tool_step 无审批测试**

Add to `tests/runtime/test_nodes.py`:

```python
from agentteam.runtime.nodes import make_tool_step


def test_tool_step_executes_tools(fake_llm, tmp_path):
    """tool_step 执行工具，回灌 ToolMessage，递增 iteration，清空 tool_calls。"""
    from agentteam.tools.skills.file_ops import write_file

    target = tmp_path / "out.txt"
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    tool_calls = [{"name": "write_file", "args": {"path": str(target), "content": "hi"}, "id": "tc1", "type": "tool_call"}]
    node = make_tool_step(worker, [write_file], approval_policy=None)
    state = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1"}
    result = node(state)

    assert target.read_text(encoding="utf-8") == "hi"
    assert len(result["react_messages"]) == 1
    assert result["iteration"] == 1
    assert result["tool_calls"] == []


def test_tool_step_handles_missing_tool(fake_llm):
    """工具不存在时回灌错误消息，不抛异常。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    tool_calls = [{"name": "nope", "args": {}, "id": "tc1", "type": "tool_call"}]
    node = make_tool_step(worker, [], approval_policy=None)
    state = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1"}
    result = node(state)
    assert "不存在" in result["react_messages"][0].content


def test_tool_step_handles_tool_exception(fake_llm):
    """工具执行出错时回灌错误消息，不抛异常。"""
    from langchain_core.tools import StructuredTool

    def boom():
        raise RuntimeError("boom")

    bad_tool = StructuredTool.from_function(name="boom", description="fails", func=boom)
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    tool_calls = [{"name": "boom", "args": {}, "id": "tc1", "type": "tool_call"}]
    node = make_tool_step(worker, [bad_tool], approval_policy=None)
    state = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1"}
    result = node(state)
    assert "boom" in result["react_messages"][0].content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_nodes.py -k tool_step -v`
Expected: FAIL — `ImportError: cannot import name 'make_tool_step'`

- [ ] **Step 3: 实现 make_tool_step（无审批部分先实现）**

Modify `agentteam/runtime/nodes.py` — 在 `make_finalize` 之后、`make_worker_node` 之前加:

```python
def make_tool_step(
    worker: Worker,
    tools: list[BaseTool],
    approval_policy: ApprovalPolicy | None = None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 tool_step 节点：检查工具级审批 → interrupt → 执行工具 → 回灌结果。

    审批按批次：批次中任一工具匹配 targets 则触发一次 interrupt。
    所有副作用（DB 写、trace、工具执行）放在 interrupt() 之后。
    """
    from langgraph.types import interrupt
    from agentteam.runtime.approval import _should_approve

    tool_map = {t.name: t for t in tools}

    def tool_step(state: dict) -> dict:
        run_id = state.get("run_id", "")
        tool_calls = state.get("tool_calls", [])
        iteration = state.get("iteration", 0)
        new_messages = []

        # 检查是否需要工具级审批
        needs_approval = (
            approval_policy is not None
            and approval_policy.level == "tool"
            and any(_should_approve(approval_policy, tc["name"]) for tc in tool_calls)
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

            # 副作用在 interrupt 之后
            if audit_repo is not None:
                approval_id = audit_repo.add_approval(run_id)
                audit_repo.decide_approval(
                    approval_id, "approved" if approved else "rejected", decider
                )
            if trace_writer is not None:
                trace_writer.emit(
                    run_id, "approval_requested", "system",
                    {"gate": "tool", "worker": worker.name,
                     "tools": [tc["name"] for tc in tool_calls]},
                )
                trace_writer.emit(
                    run_id, "approval_decided", decider,
                    {"gate": "tool", "approved": approved},
                )

            if not approved:
                for tc in tool_calls:
                    new_messages.append(
                        ToolMessage(content="工具调用已被拒绝", tool_call_id=tc["id"])
                    )
                return {
                    "react_messages": new_messages,
                    "tool_calls": [],
                    "iteration": iteration + 1,
                }

        # 执行工具
        if trace_writer is not None:
            trace_writer.emit(
                run_id, "tool_call", worker.name,
                {"tools": [tc["name"] for tc in tool_calls]},
            )

        for tc in tool_calls:
            tool = tool_map.get(tc["name"])
            if tool is None:
                result = f"工具 {tc['name']} 不存在"
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as e:
                    result = f"工具执行出错：{type(e).__name__}: {e}"
            new_messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )

        return {
            "react_messages": new_messages,
            "tool_calls": [],
            "iteration": iteration + 1,
        }

    return tool_step
```

- [ ] **Step 4: 运行无审批测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k "tool_step and not approval" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 写工具级审批测试（需要 checkpointer interrupt/resume）**

Add to `tests/runtime/test_nodes.py`:

```python
def test_tool_step_approval_approved_executes_tool(fake_llm, tmp_path):
    """工具级审批：interrupt → resume approved → 工具执行。"""
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.runtime.state import WorkerState

    executed = []

    def dangerous_tool(x: str) -> str:
        executed.append(x)
        return f"executed: {x}"

    tool = StructuredTool.from_function(name="dangerous", description="d", func=dangerous_tool)
    worker = Worker(
        name="w1", role="r", description="", system_prompt="test",
        approval_policy=ApprovalPolicy(level="tool", targets=["dangerous"]),
    )
    tool_calls = [{"name": "dangerous", "args": {"x": "data"}, "id": "tc1", "type": "tool_call"}]

    # 用最小子图测试 interrupt/resume
    sg = StateGraph(WorkerState)
    sg.add_node("tool_step", make_tool_step(worker, [tool], worker.approval_policy))
    sg.add_edge(START, "tool_step")
    sg.add_edge("tool_step", END)
    compiled = sg.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "t1"}}
    initial = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1", "react_messages": []}

    # 第一次 invoke：应 interrupt
    compiled.invoke(initial, config)
    state = compiled.get_state(config)
    assert state.next, "应在 tool_step interrupt"

    # Resume：批准
    result = compiled.invoke(Command(resume={"approved": True, "decider": "user"}), config)
    assert len(executed) == 1, "工具应被执行一次"
    assert "executed: data" in result["react_messages"][-1].content


def test_tool_step_approval_rejected_skips_tool(fake_llm):
    """工具级审批：interrupt → resume rejected → 工具跳过，回灌拒绝消息。"""
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.runtime.state import WorkerState

    executed = []

    def dangerous_tool(x: str) -> str:
        executed.append(x)
        return "should not reach"

    tool = StructuredTool.from_function(name="dangerous", description="d", func=dangerous_tool)
    worker = Worker(
        name="w1", role="r", description="", system_prompt="test",
        approval_policy=ApprovalPolicy(level="tool", targets=["dangerous"]),
    )
    tool_calls = [{"name": "dangerous", "args": {"x": "data"}, "id": "tc1", "type": "tool_call"}]

    sg = StateGraph(WorkerState)
    sg.add_node("tool_step", make_tool_step(worker, [tool], worker.approval_policy))
    sg.add_edge(START, "tool_step")
    sg.add_edge("tool_step", END)
    compiled = sg.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "t2"}}
    initial = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1", "react_messages": []}

    compiled.invoke(initial, config)
    state = compiled.get_state(config)
    assert state.next, "应 interrupt"

    result = compiled.invoke(Command(resume={"approved": False, "decider": "user"}), config)
    assert len(executed) == 0, "工具不应执行"
    assert "拒绝" in result["react_messages"][-1].content
    assert result["iteration"] == 1


def test_tool_step_no_approval_for_unlisted_tool(fake_llm):
    """工具不在 targets 列表中时不触发审批，直接执行。"""
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.runtime.state import WorkerState

    executed = []

    def safe_tool(x: str) -> str:
        executed.append(x)
        return "ok"

    tool = StructuredTool.from_function(name="safe", description="s", func=safe_tool)
    worker = Worker(
        name="w1", role="r", description="", system_prompt="test",
        approval_policy=ApprovalPolicy(level="tool", targets=["dangerous"]),
    )
    tool_calls = [{"name": "safe", "args": {"x": "data"}, "id": "tc1", "type": "tool_call"}]

    sg = StateGraph(WorkerState)
    sg.add_node("tool_step", make_tool_step(worker, [tool], worker.approval_policy))
    sg.add_edge(START, "tool_step")
    sg.add_edge("tool_step", END)
    compiled = sg.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "t3"}}
    initial = {"tool_calls": tool_calls, "iteration": 0, "run_id": "r1", "react_messages": []}

    result = compiled.invoke(initial, config)
    assert len(executed) == 1, "工具应直接执行（无需审批）"
```

- [ ] **Step 6: 运行审批测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k "tool_step and approval" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: 全量回归测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (105 existing + 6 new = 111)

- [ ] **Step 8: Commit**

```bash
git add agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add tool_step node with tool-level approval interrupt"
```

---

## Task 6: make_worker_subgraph + 向后兼容 make_worker_node

**Files:**
- Modify: `agentteam/runtime/nodes.py`
- Modify: `tests/runtime/test_nodes.py`

- [ ] **Step 1: 写子图集成测试**

Add to `tests/runtime/test_nodes.py`:

```python
from agentteam.runtime.nodes import make_worker_subgraph


def test_worker_subgraph_direct_answer(fake_llm):
    """子图：LLM 直接给最终答案（不调工具）→ finalize。"""
    fake_llm.set_invoke_responses([AIMessage(content="hello world")])
    worker = Worker(name="coder", role="r", description="", system_prompt="你是代码工程师")
    subgraph = make_worker_subgraph(worker, fake_llm, [])

    state = {
        "plan": [{"worker": "coder", "instruction": "写 hello", "status": "pending"}],
        "current_step": 0,
    }
    result = subgraph.invoke(state)

    assert result["worker_outputs"] == {"coder": "hello world"}
    assert len(result["messages"]) == 1
    assert "coder" in result["messages"][0].content


def test_worker_subgraph_react_with_tool(fake_llm, tmp_path):
    """子图：LLM 调工具 → 工具执行 → LLM 给最终答案。"""
    from agentteam.tools.skills.file_ops import write_file

    target = tmp_path / "out.txt"
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "write_file", "args": {"path": str(target), "content": "hi"}, "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="已写入文件"),
    ])
    worker = Worker(name="coder", role="r", description="", system_prompt="test", tools=["write_file"])
    subgraph = make_worker_subgraph(worker, fake_llm, [write_file])

    state = {
        "plan": [{"worker": "coder", "instruction": "写文件", "status": "pending"}],
        "current_step": 0,
    }
    result = subgraph.invoke(state)

    assert target.read_text(encoding="utf-8") == "hi"
    assert result["worker_outputs"]["coder"] == "已写入文件"


def test_worker_subgraph_respects_max_iterations(fake_llm):
    """子图：max_iterations 到达时强制结束，LLM 被调用恰好 max_iterations 次。"""
    tool_call_response = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}],
    )
    fake_llm.set_invoke_responses([tool_call_response] * 100)
    worker = Worker(name="w1", role="r", description="", system_prompt="test", max_iterations=3)
    subgraph = make_worker_subgraph(worker, fake_llm, [])

    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "r1",
    }
    result = subgraph.invoke(state)
    assert fake_llm._inv_idx == 3
    assert result["worker_outputs"]["w1"] is not None


def test_worker_subgraph_emits_trace_events(fake_llm, fake_trace_writer):
    """子图：emit worker_start 和 worker_end 轨迹事件。"""
    fake_llm.set_invoke_responses([AIMessage(content="done")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    subgraph = make_worker_subgraph(worker, fake_llm, [], trace_writer=fake_trace_writer)

    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "run-1",
    }
    subgraph.invoke(state)
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "worker_start" in event_types
    assert "worker_end" in event_types
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/runtime/test_nodes.py -k worker_subgraph -v`
Expected: FAIL — `ImportError: cannot import name 'make_worker_subgraph'`

- [ ] **Step 3: 实现 make_worker_subgraph**

Modify `agentteam/runtime/nodes.py` — 在 `make_tool_step` 之后、`make_worker_node` 之前加:

```python
def make_worker_subgraph(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """编译 Worker ReAct 子图：init_worker → agent_step → tool_step → 循环 → finalize。

    返回 compiled subgraph，可直接作为父图的节点。
    """
    from langgraph.graph import END, START, StateGraph
    from agentteam.runtime.state import WorkerState

    approval_policy = worker.approval_policy

    sg = StateGraph(WorkerState)
    sg.add_node("init_worker", make_init_worker(worker, trace_writer))
    sg.add_node("agent_step", make_agent_step(worker, llm, tools))
    sg.add_node(
        "tool_step",
        make_tool_step(worker, tools, approval_policy, trace_writer, audit_repo),
    )
    sg.add_node("finalize", make_finalize(worker, trace_writer))

    # 边
    sg.add_edge(START, "init_worker")
    sg.add_edge("init_worker", "agent_step")

    # agent_step → tool_step（有 tool_calls）或 finalize（无 tool_calls）
    def route_after_agent(state: dict) -> str:
        if state.get("final_answer"):
            return "finalize"
        if not state.get("tool_calls"):
            return "finalize"
        return "tool_step"

    sg.add_conditional_edges("agent_step", route_after_agent)

    # tool_step → agent_step（未达上限）或 finalize（达上限）
    max_iter = worker.max_iterations

    def route_after_tool(state: dict) -> str:
        if state.get("iteration", 0) >= max_iter:
            return "finalize"
        return "agent_step"

    sg.add_conditional_edges("tool_step", route_after_tool)
    sg.add_edge("finalize", END)

    return sg.compile()
```

- [ ] **Step 4: 重写 make_worker_node 为子图包装器**

Modify `agentteam/runtime/nodes.py` — 替换现有的 `make_worker_node` 函数（整个函数体）:

```python
def make_worker_node(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
):
    """向后兼容包装器：返回可调用函数，内部使用子图。

    注意：此包装器不传 checkpointer，不支持 interrupt/resume。
    仅用于无工具级审批的场景和单元测试。
    工具级审批需通过 TeamCompiler 编译完整图（含 checkpointer）。
    """
    subgraph = make_worker_subgraph(worker, llm, tools, trace_writer)

    def worker_node(state: TeamState) -> dict:
        return subgraph.invoke(state)

    return worker_node
```

- [ ] **Step 5: 运行子图测试确认通过**

Run: `python -m pytest tests/runtime/test_nodes.py -k worker_subgraph -v`
Expected: PASS (4 tests)

- [ ] **Step 6: 运行现有 worker_node 测试确认通过（向后兼容）**

Run: `python -m pytest tests/runtime/test_nodes.py -k "worker_node" -v`
Expected: PASS (existing worker_node tests still pass with wrapper)

- [ ] **Step 7: 全量回归测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (111 existing + 4 new = 115)

- [ ] **Step 8: Commit**

```bash
git add agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add make_worker_subgraph, refactor make_worker_node as wrapper"
```

---

## Task 7: TeamCompiler 集成（使用子图 + 加载 MCP 工具）

**Files:**
- Modify: `agentteam/runtime/graph.py`
- Modify: `tests/runtime/test_graph.py`（仅验证回归）

- [ ] **Step 1: 修改 TeamCompiler 使用 make_worker_subgraph**

Modify `agentteam/runtime/graph.py` — 更新 import 和 compile 方法:

Replace the import line:
```python
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
```
with:
```python
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_subgraph,
)
```

In the `compile` method, replace the worker node creation block. Find this code:
```python
            graph.add_node(
                f"worker_{worker.name}",
                make_worker_node(worker, llm, tools, trace_writer),
            )
```
Replace with:
```python
            graph.add_node(
                f"worker_{worker.name}",
                make_worker_subgraph(worker, llm, tools, trace_writer, audit_repo),
            )
```

Also add MCP tool loading at the beginning of the `compile` method, right after `graph = StateGraph(TeamState)`:

```python
        # 加载 MCP 工具到 registry（编译时 eager loading）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)
```

The full updated `compile` method should look like:

```python
    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        graph = StateGraph(TeamState)

        # 加载 MCP 工具到 registry（编译时 eager loading）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)

        leader_model = team.leader.model or team.default_model
        leader_llm = self._mp.get_llm(leader_model)
        graph.add_node(
            "leader_plan", make_leader_plan_node(team.leader, leader_llm, trace_writer)
        )
        graph.add_node(
            "leader_review",
            make_leader_review_node(team.leader, leader_llm, trace_writer),
        )

        # Step gate（仅在 leader 有 step 级策略时添加）
        step_policy = team.leader.approval_policy
        has_step_gate = step_policy is not None and step_policy.level == "step"
        if has_step_gate:
            graph.add_node(
                "step_gate", make_step_gate(step_policy, trace_writer, audit_repo)
            )

        # Worker 节点（子图）+ worker gate
        worker_gates: dict[str, bool] = {}
        for worker in team.workers:
            worker_model = worker.model or team.default_model
            llm = self._mp.get_llm(worker_model)
            tools = self._tr.get_tools(worker.tools) if worker.tools else []
            graph.add_node(
                f"worker_{worker.name}",
                make_worker_subgraph(worker, llm, tools, trace_writer, audit_repo),
            )

            wp = worker.approval_policy
            has_gate = wp is not None and wp.level == "worker"
            if has_gate:
                graph.add_node(
                    f"worker_gate_{worker.name}",
                    make_worker_gate(worker.name, wp, trace_writer, audit_repo),
                )
            worker_gates[worker.name] = has_gate

        # 路由目标映射：逻辑名 → 物理节点名（gate 或 worker）
        def physical_target(worker_name: str) -> str:
            if worker_gates.get(worker_name):
                return f"worker_gate_{worker_name}"
            return f"worker_{worker_name}"

        worker_targets = {
            f"worker_{w.name}": physical_target(w.name) for w in team.workers
        }
        worker_targets[END] = END

        # 边
        graph.add_edge(START, "leader_plan")

        if has_step_gate:
            graph.add_edge("leader_plan", "step_gate")
            graph.add_conditional_edges("step_gate", route_to_worker, worker_targets)
        else:
            graph.add_conditional_edges(
                "leader_plan", route_from_plan, worker_targets
            )

        # worker_gate → worker（条件边：拒绝→END）
        for worker in team.workers:
            if worker_gates[worker.name]:
                gate_name = f"worker_gate_{worker.name}"
                worker_node = f"worker_{worker.name}"
                graph.add_conditional_edges(
                    gate_name,
                    make_route_after_worker_gate(worker_node),
                    {worker_node: worker_node, END: END},
                )

        # worker → leader_review
        for worker in team.workers:
            graph.add_edge(f"worker_{worker.name}", "leader_review")

        # leader_review → step_gate 或直接路由
        if has_step_gate:
            graph.add_edge("leader_review", "step_gate")
        else:
            graph.add_conditional_edges(
                "leader_review", route_from_review, worker_targets
            )

        return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 2: 运行全量回归测试**

Run: `python -m pytest tests/runtime/test_graph.py -v`
Expected: PASS (all 15 graph tests — M2 + M3 regression)

- [ ] **Step 3: 运行全量测试确认无回归**

Run: `python -m pytest --tb=short -q`
Expected: PASS (115 tests, all green)

- [ ] **Step 4: Commit**

```bash
git add agentteam/runtime/graph.py
git commit -m "feat(runtime): TeamCompiler uses worker subgraph, loads MCP tools at compile time"
```

---

## Task 8: E2E 测试 — 工具级审批 + MCP 工具

**Files:**
- Modify: `tests/runtime/test_graph.py`
- Modify: `tests/conftest.py`（加 fake_mcp_loader fixture）

- [ ] **Step 1: 加 fake_mcp_loader fixture 到 conftest.py**

Modify `tests/conftest.py` — 在文件末尾加:

```python
@pytest.fixture
def fake_mcp_loader():
    """返回一个 fake MCP loader，产出指定的 fake 工具。"""
    from langchain_core.tools import StructuredTool

    def _loader(tools=None):
        tools = tools or []
        def loader(server):
            return list(tools)
        return loader

    return _loader
```

- [ ] **Step 2: 写 E2E 工具级审批测试**

Add to `tests/runtime/test_graph.py`:

```python
def test_e2e_tool_approval_interrupt_resume(fake_llm, fake_trace_writer, tmp_path):
    """E2E：Worker 调用需审批的工具 → interrupt → resume approved → 完成。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    # 一个需要审批的工具
    target = tmp_path / "secret.txt"
    def write_secret(content: str) -> str:
        target.write_text(content, encoding="utf-8")
        return "written"

    dangerous_tool = StructuredTool.from_function(
        name="write_secret", description="写秘密文件", func=write_secret
    )

    # LLM: leader 拆计划 + 点评；worker 先调工具再给答案
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="写秘密文件")])]
    )
    fake_llm.set_invoke_responses([
        # worker 第 1 轮：调工具
        AIMessage(
            content="",
            tool_calls=[{"name": "write_secret", "args": {"content": "top secret"}, "id": "tc1", "type": "tool_call"}],
        ),
        # worker 第 2 轮：给最终答案
        AIMessage(content="文件已写入"),
        # leader 点评
        AIMessage(content="做得好"),
    ])

    reg = ToolRegistry()
    reg.register(dangerous_tool)

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["write_secret"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_secret"]),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-tool"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 tool_step 处 interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在工具级审批处暂停"

    # Resume：批准
    graph.invoke(Command(resume={"approved": True, "decider": "admin"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证工具被执行
    assert target.read_text(encoding="utf-8") == "top secret"

    # 验证 worker 产出
    values = state.values
    assert values["worker_outputs"]["w1"] == "文件已写入"

    # 验证轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "approval_requested" in event_types
    assert "approval_decided" in event_types
    assert "tool_call" in event_types
    assert "worker_end" in event_types


def test_e2e_tool_approval_rejected_skips_tool(fake_llm, fake_trace_writer):
    """E2E：工具级审批被拒绝 → 工具跳过 → Worker 继续给答案。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    executed = []
    def dangerous(x: str) -> str:
        executed.append(x)
        return "done"

    tool = StructuredTool.from_function(name="dangerous", description="d", func=dangerous)

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "dangerous", "args": {"x": "data"}, "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="好的，我换个方案"),
        AIMessage(content="完成"),
    ])

    reg = ToolRegistry()
    reg.register(tool)

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["dangerous"],
                approval_policy=ApprovalPolicy(level="tool", targets=["dangerous"]),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-tool-reject"}}
    initial = _make_initial_state()

    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "应 interrupt"

    # Resume：拒绝
    graph.invoke(Command(resume={"approved": False, "decider": "admin"}), config)
    state = graph.get_state(config)

    # 工具未执行
    assert len(executed) == 0

    # Worker 最终仍有产出（LLM 换方案后给答案）
    values = state.values
    assert "w1" in values.get("worker_outputs", {})


def test_e2e_mcp_tools_via_fake_loader(fake_llm, fake_trace_writer):
    """E2E：通过 fake MCP loader 加载工具，Worker 使用 mcp: 前缀工具。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import MemorySaver

    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    # fake MCP 工具
    def search(query: str) -> str:
        return f"搜索结果: {query}"

    mcp_tool = StructuredTool.from_function(name="search", description="搜索", func=search)

    fake_loader = lambda server: [mcp_tool]  # noqa: E731
    reg = ToolRegistry(mcp_loader=fake_loader)

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="搜索测试")])]
    )
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:searcher:search", "args": {"query": "hello"}, "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="搜索完成"),
        AIMessage(content="好的"),
    ])

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                tools=["mcp:searcher:search"],
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
        mcp_servers=[MCPServer(name="searcher", command="python")],
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-mcp"}}
    result = graph.invoke(_make_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该已完成"
    assert state.values["worker_outputs"]["w1"] == "搜索完成"

    # 验证 tool_call 轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "tool_call" in event_types
```

- [ ] **Step 3: 运行 E2E 测试确认通过**

Run: `python -m pytest tests/runtime/test_graph.py -k "e2e_tool or e2e_mcp" -v`
Expected: PASS (3 new E2E tests)

- [ ] **Step 4: 全量回归测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (115 existing + 3 new = 118)

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/runtime/test_graph.py
git commit -m "test(runtime): E2E tests for tool-level approval and MCP tool loading"
```

---

## Task 9: 依赖 + 导出 + README

**Files:**
- Modify: `pyproject.toml`
- Modify: `agentteam/runtime/__init__.py`
- Modify: `README.md`

- [ ] **Step 1: 加 langchain-mcp-adapters 依赖**

Modify `pyproject.toml` — 在 `dependencies` 列表中加一行:

```toml
dependencies = [
    "langchain-core>=0.3",
    "langgraph>=0.2",
    "langgraph-checkpoint-sqlite>=3.0",
    "langchain-mcp-adapters>=0.3.0",
    "pydantic>=2",
]
```

- [ ] **Step 2: 安装新依赖**

Run: `pip install langchain-mcp-adapters>=0.3.0`
Expected: 成功安装

- [ ] **Step 3: 验证 default_mcp_loader 可 import**

Run: `python -c "from agentteam.tools.mcp import default_mcp_loader; print('OK')"`
Expected: `OK`

- [ ] **Step 4: 更新 runtime/__init__.py 导出**

Modify `agentteam/runtime/__init__.py`:

```python
"""agentteam.runtime — 执行内核（TeamCompiler, nodes, state, trace, approval）。"""

from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import (
    make_agent_step,
    make_finalize,
    make_init_worker,
    make_leader_plan_node,
    make_leader_review_node,
    make_tool_step,
    make_worker_node,
    make_worker_subgraph,
)
from agentteam.runtime.state import TeamState, WorkerState, is_rejected
from agentteam.runtime.trace import FakeTraceWriter, SqliteTraceWriter, TraceWriter

__all__ = [
    "FakeTraceWriter",
    "SqliteTraceWriter",
    "TeamCompiler",
    "TeamState",
    "TraceWriter",
    "WorkerState",
    "is_rejected",
    "make_agent_step",
    "make_finalize",
    "make_init_worker",
    "make_leader_plan_node",
    "make_leader_review_node",
    "make_step_gate",
    "make_tool_step",
    "make_worker_gate",
    "make_worker_node",
    "make_worker_subgraph",
]
```

- [ ] **Step 5: 更新 domain/__init__.py 确认 MCPServer 已导出**

Verify `agentteam/domain/__init__.py` already has MCPServer (from Task 1). If not, add it.

- [ ] **Step 6: 更新 README**

Modify `README.md` — 更新模块描述和状态:

In the module list section, add MCP tools mention and update runtime module:

```markdown
## 模块

- `agentteam.models` —— 多供应商模型抽象（Qwen/OpenAI/Anthropic/Ollama）
- `agentteam.tools` —— ToolRegistry + 原生技能（read_file/write_file/list_dir）+ MCP 工具加载
- `agentteam.storage` —— SQLite 持久化（runs / run_events / approvals）
- `agentteam.domain` —— 领域模型（Team/Worker/Leader/ApprovalPolicy/MCPServer）
- `agentteam.runtime` —— 执行内核（TeamCompiler + LangGraph StateGraph 编译执行）
  - `state.py` — TeamState / WorkerState 状态 schema
  - `nodes.py` — leader_plan / worker ReAct 子图 / leader_review 节点工厂
  - `graph.py` — TeamCompiler（Team → StateGraph 编译，含审批门 + MCP 加载）
  - `trace.py` — TraceWriter 协议（SQLite / Fake 实现）
  - `approval.py` — 审批门节点（step 级 / worker 级 / tool 级，interrupt 实现）
```

Update the status section:

```markdown
## 状态

- [x] M1 基础设施层
- [x] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
- [x] M3 审批与轨迹
- [x] M4 MCP 集成（子图 ReAct + 工具级审批 + MCP 工具加载）
- [ ] M5 API + Web UI
- [ ] M6 示例团队 + 测试
```

- [ ] **Step 7: 全量测试**

Run: `python -m pytest --tb=short -q`
Expected: PASS (118 tests)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml agentteam/runtime/__init__.py agentteam/domain/__init__.py README.md
git commit -m "feat: add langchain-mcp-adapters dep, update exports and README for M4"
```

---

## 自审清单

完成所有 Task 后，检查：

- [ ] **Spec 覆盖**：MCPServer 模型 ✓、Team.mcp_servers ✓、ToolRegistry.register_mcp_tools ✓、default_mcp_loader ✓、WorkerState ✓、Worker 子图（init/agent/tool/finalize）✓、工具级审批 interrupt ✓、max_iterations ✓、MCP 编译时加载 ✓
- [ ] **无 placeholder**：所有步骤有完整代码
- [ ] **类型一致**：make_worker_subgraph 签名在 Task 6/7 一致；WorkerState 字段在 Task 3/4/5 一致
- [ ] **回归**：M1+M2+M3 测试全部通过（93 → 118）
