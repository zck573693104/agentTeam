# SP2 多级 MCP 挂载 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agent 与 TeamRef 上新增 MCP 挂载字段，TeamCompiler 递归注册多级 MCP，实现 Worker 级与 sub-Team 级 MCP 挂载。

**Architecture:** Agent 新增 `mcp_servers` 字段，TeamRef 新增 `mcp_overrides` 字段。TeamCompiler 在 `_compile_agent` 中注册 Agent 级 MCP，在 TeamRef 分支中注册 mcp_overrides。序列化与库解析同步更新。保持 `mcp:{server}:{tool}` 命名空间与 Team.mcp_servers 向后兼容。

**Tech Stack:** Python 3.10+ dataclasses, LangGraph, pytest

**Spec:** [docs/superpowers/specs/2026-07-18-sp2-multi-level-mcp-design.md](file:///d/project/agentTeam/docs/superpowers/specs/2026-07-18-sp2-multi-level-mcp-design.md)

---

## File Structure

**修改文件：**
- `agentteam/domain/agent.py` — Agent 加 mcp_servers，TeamRef 加 mcp_overrides
- `agentteam/domain/library.py` — resolve 处理 mcp_servers 覆盖
- `agentteam/api/serializer.py` — 序列化 Agent.mcp_servers 与 TeamRef.mcp_overrides
- `agentteam/runtime/graph.py` — TeamCompiler 注册 Agent/TeamRef 级 MCP

**新建文件：**
- `examples/multi_level_mcp.py` — 多级 MCP 示例
- `tests/integration/test_multi_level_mcp.py` — 多级 MCP E2E 测试

---

## Commit 1：数据模型（不破坏现有功能）

### Task 1: Agent 新增 mcp_servers 字段 + TeamRef 新增 mcp_overrides 字段

**Files:**
- Modify: `agentteam/domain/agent.py`
- Test: `tests/domain/test_agent.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/domain/test_agent.py` 末尾追加：

```python
def test_agent_worker_with_mcp_servers():
    from agentteam.domain.mcp_server import MCPServer
    server = MCPServer(name="git", command="git-mcp")
    a = Agent(name="coder", role="worker", mcp_servers=[server])
    assert len(a.mcp_servers) == 1
    assert a.mcp_servers[0].name == "git"


def test_agent_mcp_servers_defaults_empty():
    a = Agent(name="w", role="worker")
    assert a.mcp_servers == []


def test_team_ref_with_mcp_overrides():
    from agentteam.domain.mcp_server import MCPServer
    server = MCPServer(name="extra", command="extra-mcp")
    ref = TeamRef(name="sub", alias="qa", mcp_overrides=[server])
    assert ref.mcp_overrides[0].name == "extra"


def test_team_ref_mcp_overrides_defaults_empty():
    ref = TeamRef(name="sub")
    assert ref.mcp_overrides == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_agent.py -v -k "mcp_servers or mcp_overrides"`
Expected: FAIL with `TypeError: Agent.__init__() got an unexpected keyword argument 'mcp_servers'`

- [ ] **Step 3: 实现 Agent.mcp_servers 与 TeamRef.mcp_overrides**

修改 `agentteam/domain/agent.py`：

```python
"""统一 Agent 节点 + TeamRef 引用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef


@dataclass
class TeamRef:
    """引用另一个 Team 作为本节点的 child。

    编译时由 TeamCompiler 从 _team_registry 取出目标 Team，
    编译其 root 作为本节点。alias 用于在父 Team 内重命名，防重名。

    mcp_overrides：引用 sub-Team 时追加注册的 MCP 服务（扩展语义，
    不替换 sub-Team 自身的 mcp_servers）。
    """
    name: str
    alias: str | None = None
    mcp_overrides: list[MCPServer] = field(default_factory=list)


@dataclass
class Agent:
    """统一智能体节点。

    - role="supervisor"：派活给 children，跑 plan→children→review 循环
    - role="worker"：叶子节点，跑 ReAct 工具循环

    约束（编译期校验，见 TeamCompiler._validate）：
    - supervisor 必须有 children，tools 必须为空
    - worker 必须无 children，可有 tools
    - ref 与 children 可同时存在：ref 指向库时作为模板，调用处 children 覆盖模板 children

    mcp_servers：本 Agent 级别挂载的 MCP 服务，编译期由 TeamCompiler 注册到
    ToolRegistry。Worker 在 tools 中用 `mcp:{server.name}:{tool.name}` 引用。
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

    # MCP 挂载（任意角色可挂，worker 在 tools 中引用注册后的工具名）
    mcp_servers: list[MCPServer] = field(default_factory=list)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_agent.py -v`
Expected: 全部 pass（含新增 4 个 + 既有 8 个 = 12）

- [ ] **Step 5: 运行全套确认无回归**

Run: `pytest tests/ -v`
Expected: 264 passed（无回归）

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/agent.py tests/domain/test_agent.py
git commit -m "feat(domain): Agent 加 mcp_servers，TeamRef 加 mcp_overrides"
```

---

### Task 2: AgentLibrary.resolve 处理 mcp_servers 覆盖

**Files:**
- Modify: `agentteam/domain/library.py`
- Test: `tests/domain/test_library.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/domain/test_library.py` 末尾追加：

```python
def test_resolve_mcp_servers_from_template():
    """ref 模式：调用方未传 mcp_servers，保留模板的 mcp_servers。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    ))
    caller = Agent(name="eng", role="worker", ref="library:code_engineer")
    resolved = lib.resolve(caller)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "git"


def test_resolve_mcp_servers_override_from_caller():
    """ref 模式：调用方传了 mcp_servers，覆盖模板的。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    ))
    caller = Agent(
        name="eng", role="worker", ref="library:code_engineer",
        mcp_servers=[MCPServer(name="custom", command="custom-mcp")],
    )
    resolved = lib.resolve(caller)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "custom"


def test_resolve_no_ref_preserves_mcp_servers():
    """无 ref 模式：mcp_servers 原样传递。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    a = Agent(
        name="w", role="worker",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )
    resolved = lib.resolve(a)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "git"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_library.py -v -k "mcp_servers"`
Expected: FAIL（resolved.mcp_servers 为空，因为 resolve 未处理 mcp_servers）

- [ ] **Step 3: 实现 resolve 处理 mcp_servers**

修改 `agentteam/domain/library.py` 的 `resolve` 方法。在 ref 分支中（`if agent.tools:` 之后）加：

```python
if agent.mcp_servers:
    resolved.mcp_servers = list(agent.mcp_servers)
```

同时在无 ref 分支的 `Agent(...)` 构造中加 `mcp_servers=list(agent.mcp_servers)`。

完整修改后的 resolve 方法关键部分：

```python
def resolve(self, agent: Agent, _visited: list[str] | None = None) -> Agent:
    if _visited is None:
        _visited = []
    if agent.ref is None:
        return Agent(
            name=agent.name, role=agent.role,
            system_prompt=agent.system_prompt, model=agent.model,
            children=[self._resolve_child(c, _visited) for c in agent.children],
            approval_policy=agent.approval_policy,
            tools=list(agent.tools),
            max_iterations=agent.max_iterations,
            ref=None,
            mcp_servers=list(agent.mcp_servers),  # 新增
        )
    # ... ref 分支不变直到覆盖逻辑 ...
    if agent.tools:
        resolved.tools = list(agent.tools)
    if agent.max_iterations != 10:
        resolved.max_iterations = agent.max_iterations
    if agent.children:
        resolved.children = list(agent.children)
    if agent.mcp_servers:  # 新增
        resolved.mcp_servers = list(agent.mcp_servers)
    resolved.name = agent.name
    resolved.role = agent.role
    resolved.children = [self._resolve_child(c, visited) for c in resolved.children]
    return resolved
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_library.py -v`
Expected: 全部 pass（含新增 3 个）

- [ ] **Step 5: 运行全套确认无回归**

Run: `pytest tests/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/library.py tests/domain/test_library.py
git commit -m "feat(library): resolve 处理 mcp_servers 覆盖"
```

---

## Commit 2：序列化与编译

### Task 3: 序列化 Agent.mcp_servers 与 TeamRef.mcp_overrides

**Files:**
- Modify: `agentteam/api/serializer.py`
- Test: `tests/api/test_serializer.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/api/test_serializer.py` 末尾追加：

```python
def test_agent_to_dict_includes_mcp_servers():
    from agentteam.domain.agent import Agent
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.api.serializer import _agent_to_dict
    a = Agent(
        name="w", role="worker",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )
    d = _agent_to_dict(a)
    assert "mcp_servers" in d
    assert len(d["mcp_servers"]) == 1
    assert d["mcp_servers"][0]["name"] == "git"


def test_agent_from_dict_parses_mcp_servers():
    from agentteam.domain.agent import Agent
    from agentteam.api.serializer import _agent_from_dict
    d = {
        "name": "w", "role": "worker",
        "mcp_servers": [{"name": "git", "command": "git-mcp", "args": [], "env": {}, "transport": "stdio", "url": None}],
    }
    a = _agent_from_dict(d)
    assert len(a.mcp_servers) == 1
    assert a.mcp_servers[0].name == "git"


def test_teamref_to_dict_includes_mcp_overrides():
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.api.serializer import _agent_to_dict
    parent = Agent(
        name="lead", role="supervisor",
        children=[TeamRef(name="sub", alias="qa",
                          mcp_overrides=[MCPServer(name="extra", command="x")])],
    )
    d = _agent_to_dict(parent)
    child = d["children"][0]
    assert child["_type"] == "TeamRef"
    assert "mcp_overrides" in child
    assert child["mcp_overrides"][0]["name"] == "extra"


def test_teamref_from_dict_parses_mcp_overrides():
    from agentteam.domain.agent import TeamRef
    from agentteam.api.serializer import _agent_from_dict
    d = {
        "name": "lead", "role": "supervisor",
        "children": [{
            "_type": "TeamRef", "name": "sub", "alias": "qa",
            "mcp_overrides": [{"name": "extra", "command": "x", "args": [], "env": {}, "transport": "stdio", "url": None}],
        }],
    }
    a = _agent_from_dict(d)
    ref = a.children[0]
    assert isinstance(ref, TeamRef)
    assert len(ref.mcp_overrides) == 1
    assert ref.mcp_overrides[0].name == "extra"


def test_team_to_dict_roundtrip_with_mcp():
    from agentteam.domain.agent import Agent
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.api.serializer import team_to_dict, team_from_dict
    team = Team(
        name="t", description="d",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(
                name="coder", role="worker",
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    d = team_to_dict(team)
    team2 = team_from_dict(d)
    assert len(team2.root.children[0].mcp_servers) == 1
    assert team2.root.children[0].mcp_servers[0].name == "git"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/api/test_serializer.py -v -k "mcp"`
Expected: FAIL（序列化未包含 mcp_servers/mcp_overrides）

- [ ] **Step 3: 实现序列化**

修改 `agentteam/api/serializer.py`：

1. `_agent_to_dict` 返回字典加 `"mcp_servers": [asdict(s) for s in agent.mcp_servers]`
2. `_agent_from_dict` 构造 Agent 加 `mcp_servers=[_mcp_server_from_dict(s) for s in d.get("mcp_servers", [])]`
3. children 中 TeamRef 分支加 `"mcp_overrides": [asdict(s) for s in c.mcp_overrides]`
4. children 解析 TeamRef 分支加 `mcp_overrides=[_mcp_server_from_dict(s) for s in c.get("mcp_overrides", [])]`

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/api/test_serializer.py -v`
Expected: 全部 pass

- [ ] **Step 5: 运行全套确认无回归**

Run: `pytest tests/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/api/serializer.py tests/api/test_serializer.py
git commit -m "feat(api): 序列化 Agent.mcp_servers 与 TeamRef.mcp_overrides"
```

---

### Task 4: TeamCompiler 注册 Agent/TeamRef 级 MCP

**Files:**
- Modify: `agentteam/runtime/graph.py`
- Test: `tests/runtime/test_graph.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/runtime/test_graph.py` 末尾追加：

```python
def test_compile_registers_agent_mcp_servers():
    """Worker Agent 的 mcp_servers 在编译期注册到 ToolRegistry。"""
    from agentteam.domain.agent import Agent
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    # 用 fake mcp_loader 避免真实 MCP 连接
    from langchain_core.tools import StructuredTool
    def fake_loader(server):
        return [StructuredTool.from_function(
            name="git_status", description="git status", func=lambda: "ok")]
    reg = ToolRegistry(mcp_loader=fake_loader)

    provider = FakeModelProvider({"qwen-max": FakeLLM()})
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t", description="d",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(
                name="coder", role="worker",
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
                tools=["mcp:git:git_status"],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.compile(team)
    # 工具应已注册
    assert "mcp:git:git_status" in reg.list_names()


def test_compile_registers_teamref_mcp_overrides():
    """TeamRef 的 mcp_overrides 在编译期注册到 ToolRegistry。"""
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider
    from langchain_core.tools import StructuredTool

    def fake_loader(server):
        return [StructuredTool.from_function(
            name="extra_tool", description="extra", func=lambda: "ok")]
    reg = ToolRegistry(mcp_loader=fake_loader)

    provider = FakeModelProvider({"qwen-max": FakeLLM()})
    compiler = TeamCompiler(provider, reg)

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor",
            children=[Agent(name="w", role="worker")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="main",
        root=Agent(
            name="lead", role="supervisor",
            children=[TeamRef(
                name="sub", alias="qa",
                mcp_overrides=[MCPServer(name="extra", command="extra-mcp")],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.compile(main_team)
    assert "mcp:extra:extra_tool" in reg.list_names()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/runtime/test_graph.py -v -k "registers_agent_mcp or registers_teamref_mcp"`
Expected: FAIL（MCP 工具未注册，KeyError 或 assert 失败）

- [ ] **Step 3: 实现 TeamCompiler 注册逻辑**

修改 `agentteam/runtime/graph.py`：

1. `_compile_agent` 方法中，`self._validate(agent, depth, path)` 之后加：
```python
# 注册 Agent 级 MCP
for server in agent.mcp_servers:
    self._tr.register_mcp_tools(server)
```

2. `_compile_supervisor` 方法中，TeamRef 分支（`if isinstance(child, TeamRef):`）内，`sub_graph = self._compile_agent(...)` 之前加：
```python
# 注册 TeamRef 的 mcp_overrides
for server in child.mcp_overrides:
    self._tr.register_mcp_tools(server)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/runtime/test_graph.py -v -k "registers_agent_mcp or registers_teamref_mcp"`
Expected: 2 passed

- [ ] **Step 5: 运行全套确认无回归**

Run: `pytest tests/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/runtime/graph.py tests/runtime/test_graph.py
git commit -m "feat(runtime): TeamCompiler 注册 Agent/TeamRef 级 MCP"
```

---

## Commit 3：E2E 与示例

### Task 5: 多级 MCP E2E 集成测试

**Files:**
- Create: `tests/integration/test_multi_level_mcp.py`

- [ ] **Step 1: 写 E2E 测试**

创建 `tests/integration/test_multi_level_mcp.py`：

```python
"""多级 MCP 挂载 E2E 测试。"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _make_mcp_loader(tool_name: str, response: str = "ok"):
    """构造 fake mcp_loader，返回单个工具。"""
    def loader(server):
        return [StructuredTool.from_function(
            name=tool_name, description=f"fake {tool_name}",
            func=lambda **kwargs: response,
        )]
    return loader


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_e2e_worker_level_mcp():
    """Worker 级 MCP：coder 挂载 git MCP，调用 git_status 工具。"""
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="check git status"),
    ])])
    leader_llm.set_invoke_responses([AIMessage(content="coder done")])

    coder_llm = FakeLLM()
    coder_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:git:git_status", "args": {},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="git status: clean"),
    ])

    reg = ToolRegistry(mcp_loader=_make_mcp_loader("git_status", "clean"))
    provider = FakeModelProvider({
        "leader-model": leader_llm, "coder-model": coder_llm,
    })
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t", description="worker mcp",
        root=Agent(
            name="lead", role="supervisor", system_prompt="lead",
            model=ModelRef("qwen", "leader-model"),
            children=[Agent(
                name="coder", role="worker", system_prompt="coder",
                model=ModelRef("qwen", "coder-model"),
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
                tools=["mcp:git:git_status"],
            )],
        ),
        default_model=ModelRef("qwen", "leader-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "wmcp"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["coder"] == "git status: clean"


def test_e2e_teamref_mcp_overrides():
    """TeamRef 级 MCP 覆盖：父 Team 引用 sub-Team 时追加 MCP 服务。"""
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="qa", instruction="run extra tool"),
    ])])
    parent_llm.set_invoke_responses([AIMessage(content="qa done")])

    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="tester", instruction="test"),
    ])])
    sub_llm.set_invoke_responses([AIMessage(content="sub done")])

    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:extra:extra_tool", "args": {},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="extra result"),
    ])

    def fake_loader(server):
        if server.name == "extra":
            return [StructuredTool.from_function(
                name="extra_tool", description="extra", func=lambda **k: "extra-ok")]
        return []

    reg = ToolRegistry(mcp_loader=fake_loader)
    provider = FakeModelProvider({
        "parent-model": parent_llm,
        "sub-model": sub_llm,
        "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, reg)

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
                tools=["mcp:extra:extra_tool"],
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="teamref mcp",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            model=ModelRef("qwen", "parent-model"),
            children=[TeamRef(
                name="sub", alias="qa",
                mcp_overrides=[MCPServer(name="extra", command="extra-mcp")],
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tmcp"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["tester"] == "extra result"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `pytest tests/integration/test_multi_level_mcp.py -v`
Expected: 2 passed

- [ ] **Step 3: 运行全套确认无回归**

Run: `pytest tests/ -v`
Expected: 全部 pass（baseline + 2 新增）

- [ ] **Step 4: 提交**

```bash
git add tests/integration/test_multi_level_mcp.py
git commit -m "test(integration): 多级 MCP E2E（worker 级 + teamref 覆盖）"
```

---

### Task 6: 多级 MCP 示例文件

**Files:**
- Create: `examples/multi_level_mcp.py`

- [ ] **Step 1: 创建示例文件**

创建 `examples/multi_level_mcp.py`：

```python
"""多级 MCP 挂载示例：Team 级 + Worker 级 + TeamRef 覆盖。

展示 SP2 三层 MCP 挂载能力：
- Team.mcp_servers：全队共享的 MCP 服务
- Agent.mcp_servers：Worker 专属的 MCP 服务
- TeamRef.mcp_overrides：引用 sub-Team 时追加的 MCP 服务
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef

# —— 子 Team：测试小队 ——
TEST_SUBTEAM = Team(
    name="test_subteam",
    description="测试小队（自身无 MCP，由父 Team 通过 mcp_overrides 追加）",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员，用 test_runner 工具跑测试。",
            tools=["mcp:test:test_run"],
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)

# —— 主 Team：三层 MCP 挂载 ——
MULTI_LEVEL_MCP_TEAM = Team(
    name="multi_level_mcp",
    description="三层 MCP 挂载示例",
    # Team 级 MCP：全队共享
    mcp_servers=[MCPServer(name="shared", command="shared-mcp")],
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO，派活给 coder 和 qa 小队。",
        children=[
            # Worker 级 MCP：coder 专属的 git 服务
            Agent(
                name="coder", role="worker",
                system_prompt="你是代码工程师，用 git 工具操作代码。",
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
                tools=["mcp:git:git_status", "mcp:git:git_commit"],
            ),
            # TeamRef 级 MCP 覆盖：引用测试小队时追加 test 服务
            TeamRef(
                name="test_subteam", alias="qa",
                mcp_overrides=[MCPServer(name="test", command="test-mcp")],
            ),
        ],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)
```

- [ ] **Step 2: 验证可导入**

Run: `python -c "from examples.multi_level_mcp import MULTI_LEVEL_MCP_TEAM, TEST_SUBTEAM; print(MULTI_LEVEL_MCP_TEAM.root.name); print(TEST_SUBTEAM.root.name)"`
Expected: 输出 `ceo` 和 `test_lead`

- [ ] **Step 3: 提交**

```bash
git add examples/multi_level_mcp.py
git commit -m "feat(examples): 多级 MCP 挂载示例"
```

---

### Task 7: 全套测试验证

- [ ] **Step 1: 运行全套测试**

Run: `pytest tests/ -v`
Expected: 全部 pass（264 baseline + SP2 新增 ≈ 270+）

- [ ] **Step 2: 运行 SP2 专项测试**

Run: `pytest tests/domain/test_agent.py tests/domain/test_library.py tests/api/test_serializer.py tests/runtime/test_graph.py tests/integration/test_multi_level_mcp.py -v`
Expected: 全部 pass

- [ ] **Step 3: 确认工作树干净**

Run: `git status`
Expected: nothing to commit, working tree clean
