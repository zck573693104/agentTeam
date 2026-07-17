# SP1 Agent 层级核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Leader/Worker 重构为统一 Agent + role 字段，支持多级 supervisor 链、Team 嵌套、专家 Agent 库，保留向后兼容。

**Architecture:** 递归 Agent 图——Agent dataclass 用 role 字段区分 supervisor/worker，supervisor 的 children 可递归包含 Agent 或 TeamRef。TeamCompiler 递归编译为 LangGraph 嵌套子图。AgentLibrary 提供 $ref 引用机制。保留 Leader/Worker 作为兼容层。

**Tech Stack:** Python 3.10+ dataclasses, LangGraph StateGraph, LangChain BaseChatModel, pytest

**Spec:** [docs/superpowers/specs/2026-07-18-sp1-agent-hierarchy-core-design.md](file:///d/project/agentTeam/docs/superpowers/specs/2026-07-18-sp1-agent-hierarchy-core-design.md)

---

## File Structure

**新建文件：**
- `agentteam/domain/agent.py` — Agent + TeamRef dataclass
- `agentteam/domain/library.py` — AgentLibrary 专家库
- `examples/multi_level_team.py` — 多级层级示例
- `tests/domain/test_agent.py` — Agent 单元测试
- `tests/domain/test_library.py` — AgentLibrary 单元测试
- `tests/integration/test_multi_level.py` — 多级层级集成测试
- `tests/integration/test_cross_level_approval.py` — 跨层审批集成测试
- `tests/integration/test_legacy_compat.py` — 向后兼容集成测试

**修改文件：**
- `agentteam/domain/team.py` — Team 加 root + from_legacy + property，Leader 加 to_agent
- `agentteam/domain/worker.py` — Worker 加 to_agent
- `agentteam/api/serializer.py` — team_from_dict/team_to_dict 双轨
- `agentteam/runtime/state.py` — TeamState 加 path 字段
- `agentteam/runtime/nodes.py` — 节点工厂接受 Agent
- `agentteam/runtime/graph.py` — TeamCompiler 递归编译
- `agentteam/api/routes/runs.py` — 注入 library 与 team_registry
- `agentteam/api/server.py` — 初始化 AgentLibrary
- `agentteam/cli.py` — 新增 register-team / list-teams / register-library 命令

---

## Commit 1：领域模型（不破坏现有功能）

### Task 1: 创建 Agent + TeamRef dataclass

**Files:**
- Create: `agentteam/domain/agent.py`
- Test: `tests/domain/test_agent.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/domain/test_agent.py`：

```python
"""Agent / TeamRef dataclass 单元测试。"""
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


def test_team_ref_basic():
    ref = TeamRef(name="dev_subteam")
    assert ref.name == "dev_subteam"
    assert ref.alias is None


def test_team_ref_with_alias():
    ref = TeamRef(name="dev_subteam", alias="qa")
    assert ref.alias == "qa"


def test_agent_worker_defaults():
    a = Agent(name="coder", role="worker")
    assert a.name == "coder"
    assert a.role == "worker"
    assert a.system_prompt == ""
    assert a.model is None
    assert a.children == []
    assert a.approval_policy is None
    assert a.tools == []
    assert a.max_iterations == 10
    assert a.ref is None


def test_agent_supervisor_with_children():
    child = Agent(name="w1", role="worker", tools=["read_file"])
    parent = Agent(
        name="lead", role="supervisor",
        system_prompt="你是主管",
        children=[child],
    )
    assert parent.role == "supervisor"
    assert len(parent.children) == 1
    assert parent.children[0].name == "w1"


def test_agent_with_team_ref_child():
    ref = TeamRef(name="sub_team", alias="qa")
    parent = Agent(name="lead", role="supervisor", children=[ref])
    assert isinstance(parent.children[0], TeamRef)
    assert parent.children[0].alias == "qa"


def test_agent_with_ref_and_overrides():
    a = Agent(
        name="eng", role="worker",
        ref="library:code_engineer",
        system_prompt="override prompt",
        max_iterations=5,
    )
    assert a.ref == "library:code_engineer"
    assert a.system_prompt == "override prompt"
    assert a.max_iterations == 5


def test_agent_supervisor_with_approval_policy():
    ap = ApprovalPolicy(level="step")
    a = Agent(name="lead", role="supervisor", children=[
        Agent(name="w", role="worker")
    ], approval_policy=ap)
    assert a.approval_policy is ap


def test_agent_with_model():
    m = ModelRef("qwen", "qwen-max")
    a = Agent(name="w", role="worker", model=m)
    assert a.model is m
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_agent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.domain.agent'`

- [ ] **Step 3: 实现 Agent + TeamRef**

创建 `agentteam/domain/agent.py`：

```python
"""统一 Agent 节点 + TeamRef 引用。"""
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
    name: str
    alias: str | None = None


@dataclass
class Agent:
    """统一智能体节点。

    - role="supervisor"：派活给 children，跑 plan→children→review 循环
    - role="worker"：叶子节点，跑 ReAct 工具循环

    约束（编译期校验，见 TeamCompiler._validate）：
    - supervisor 必须有 children，tools 必须为空
    - worker 必须无 children，可有 tools
    - ref 与 children 可同时存在：ref 指向库时作为模板，调用处 children 覆盖模板 children
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

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_agent.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add agentteam/domain/agent.py tests/domain/test_agent.py
git commit -m "feat(domain): 新增 Agent + TeamRef dataclass"
```

---

### Task 2: Worker 加 to_agent() 转换方法

**Files:**
- Modify: `agentteam/domain/worker.py`
- Test: `tests/domain/test_worker.py` (新增；当前不存在)

- [ ] **Step 1: 写失败测试**

创建 `tests/domain/test_worker.py`：

```python
"""Worker 兼容层 to_agent() 测试。"""
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def test_worker_to_agent_basic():
    w = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师",
    )
    a = w.to_agent()
    assert a.name == "coder"
    assert a.role == "worker"
    assert a.system_prompt == "你是代码工程师"
    assert a.tools == []
    assert a.max_iterations == 10
    assert a.children == []
    assert a.ref is None


def test_worker_to_agent_preserves_all_fields():
    m = ModelRef("qwen", "qwen-max")
    ap = ApprovalPolicy(level="tool", targets=["write_file"])
    w = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师", model=m,
        tools=["read_file", "write_file"],
        approval_policy=ap, max_iterations=5,
    )
    a = w.to_agent()
    assert a.model is m
    assert a.tools == ["read_file", "write_file"]
    assert a.approval_policy is ap
    assert a.max_iterations == 5
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_worker.py -v`
Expected: FAIL with `AttributeError: 'Worker' object has no attribute 'to_agent'`

- [ ] **Step 3: 实现 to_agent()**

修改 `agentteam/domain/worker.py`，在 Worker 类末尾添加方法：

```python
from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


@dataclass
class Worker:
    """角色说明书：定义一个 Worker 的职责、模型、工具与审批策略。

    保留作为兼容层；新代码用 Agent(role="worker")。
    """
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_worker.py -v`
Expected: 2 passed

- [ ] **Step 5: 运行 worker 既有测试确认无回归**

Run: `pytest tests/domain/test_worker.py tests/domain/test_team.py -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/worker.py tests/domain/test_worker.py
git commit -m "feat(domain): Worker 加 to_agent() 兼容层"
```

---

### Task 3: Leader 加 to_agent() 转换方法

**Files:**
- Modify: `agentteam/domain/team.py` (Leader 类)
- Test: `tests/domain/test_team.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/domain/test_team.py` 末尾追加：

```python
def test_leader_to_agent_basic():
    from agentteam.domain.agent import Agent
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    a = leader.to_agent()
    assert isinstance(a, Agent)
    assert a.role == "supervisor"
    assert a.system_prompt == "你是主管"
    assert a.children == []
    assert a.approval_policy is None


def test_leader_to_agent_with_children_and_policy():
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    ap = ApprovalPolicy(level="step")
    leader = Leader(name="lead", system_prompt="你是主管", approval_policy=ap)
    child = Agent(name="w", role="worker")
    a = leader.to_agent(children=[child])
    assert a.role == "supervisor"
    assert a.approval_policy is ap
    assert len(a.children) == 1
    assert a.children[0].name == "w"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_team.py::test_leader_to_agent_basic -v`
Expected: FAIL with `AttributeError: 'Leader' object has no attribute 'to_agent'`

- [ ] **Step 3: 给 Leader 加 to_agent()**

修改 `agentteam/domain/team.py` 的 Leader 类：

```python
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

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_team.py -v`
Expected: 全部 pass（含新增 2 个 + 既有 5 个）

- [ ] **Step 5: 提交**

```bash
git add agentteam/domain/team.py tests/domain/test_team.py
git commit -m "feat(domain): Leader 加 to_agent() 兼容层"
```

---

### Task 4: Team 加 root + from_legacy + property

**Files:**
- Modify: `agentteam/domain/team.py` (Team 类重构)
- Test: `tests/domain/test_team.py` (新增用例)

注意：现有 `Team` 用 `leader` + `workers` 字段。重构后 `Team` 同时支持两种构造方式：旧方式 `Team(leader=..., workers=...)` 仍可工作（内部转 root），新方式 `Team(root=...)` 直接用 Agent 树。

- [ ] **Step 1: 写失败测试**

在 `tests/domain/test_team.py` 末尾追加：

```python
def test_team_root_construction():
    """新 schema：直接用 root agent 构造 Team。"""
    from agentteam.domain.agent import Agent
    root = Agent(
        name="lead", role="supervisor", system_prompt="你是主管",
        children=[Agent(name="w1", role="worker")],
    )
    team = Team(
        name="t", description="d", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.root is root
    # 兼容 property：leader/workers 从 root 反推
    assert team.leader.name == "lead"
    assert len(team.workers) == 1
    assert team.workers[0].name == "w1"


def test_team_from_legacy():
    """旧 leader+workers 配置经 from_legacy 转 root。"""
    from agentteam.domain.agent import Agent
    leader = Leader(name="boss", system_prompt="你是主管")
    workers = [
        Worker(name="coder", role="代码工程师", description="", system_prompt=""),
        Worker(name="tester", role="测试员", description="", system_prompt=""),
    ]
    team = Team.from_legacy(
        name="dev", description="d", leader=leader, workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.root.role == "supervisor"
    assert team.root.name == "boss"
    assert len(team.root.children) == 2
    assert all(isinstance(c, Agent) for c in team.root.children)
    # property 反推一致
    assert team.leader.name == "boss"
    assert [w.name for w in team.workers] == ["coder", "tester"]


def test_team_legacy_construction_still_works():
    """旧 Team(leader=..., workers=...) 构造方式仍可用（不通过 from_legacy）。"""
    leader = Leader(system_prompt="你是主管")
    coder = Worker(name="coder", role="代码工程师", description="", system_prompt="")
    team = Team(
        name="dev", description="d", leader=leader, workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # 旧字段访问
    assert team.leader is leader
    assert len(team.workers) == 1
    # root 也能访问
    assert team.root.role == "supervisor"
    assert team.root.name == "leader"


def test_team_property_workers_filters_non_worker_children():
    """property workers 仅返回 role=worker 的 children，跳过 supervisor/TeamRef。"""
    from agentteam.domain.agent import Agent, TeamRef
    root = Agent(
        name="lead", role="supervisor",
        children=[
            Agent(name="w1", role="worker"),
            Agent(name="sub", role="supervisor", children=[
                Agent(name="w2", role="worker")
            ]),
            TeamRef(name="other_team"),
        ],
    )
    team = Team(
        name="t", description="d", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # workers property 只返回 w1（worker 角色的直接 child）
    assert [w.name for w in team.workers] == ["w1"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_team.py::test_team_root_construction -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'root'`

- [ ] **Step 3: 重构 Team 类**

替换 `agentteam/domain/team.py` 的 Team 类（保留 Leader 类不变）：

```python
@dataclass
class Team:
    """Team：顶层容器，root agent + 默认模型 + 团队级 MCP。

    两种构造方式：
    1. 新方式：Team(root=agent, ...)  —— 直接用 Agent 树
    2. 旧方式：Team(leader=..., workers=...)  —— 兼容，内部转 root

    向后兼容 property：
    - team.leader: 从 root 反推 Leader 对象
    - team.workers: 从 root.children 中筛 worker Agent 反推 Worker 列表
    """
    name: str
    description: str
    default_model: ModelRef
    root: Agent | None = None
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
    # 旧字段（仅旧构造方式使用）
    leader: Leader | None = None
    workers: list[Worker] = field(default_factory=list)

    def __post_init__(self):
        """统一两种构造方式：若 root 为空，从 leader+workers 转换。"""
        if self.root is None:
            if self.leader is None:
                raise ValueError("Team requires either root or leader+workers")
            self.root = self.leader.to_agent(
                children=[w.to_agent() for w in self.workers]
            )

    @property
    def leader(self) -> Leader:
        """从 root 反推 Leader。"""
        return Leader(
            name=self.root.name,
            role="主管",
            system_prompt=self.root.system_prompt,
            model=self.root.model,
            approval_policy=self.root.approval_policy,
        )

    @leader.setter
    def leader(self, value: Leader | None):
        """旧构造方式 setter：存到 _leader_legacy，__post_init__ 时用。"""
        object.__setattr__(self, "_leader_legacy", value)

    @property
    def workers(self) -> list[Worker]:
        """从 root.children 反推 Worker 列表（仅 role=worker 的直接 child）。"""
        result = []
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
    def workers(self, value: list[Worker]):
        """旧构造方式 setter：存到 _workers_legacy，__post_init__ 时用。"""
        object.__setattr__(self, "_workers_legacy", list(value))

    @classmethod
    def from_legacy(cls, *, name, description, leader, workers, default_model,
                    skills=None, mcp_servers=None) -> "Team":
        """旧 leader+workers 配置显式转新 root+children。"""
        root = leader.to_agent(children=[w.to_agent() for w in workers])
        return cls(
            name=name, description=description, root=root,
            default_model=default_model,
            skills=list(skills) if skills else [],
            mcp_servers=list(mcp_servers) if mcp_servers else [],
        )
```

注意 dataclass 的 leader/workers 字段需用 `field(default=None)` / `field(default_factory=list)`，且要在 `root` 之后。完整文件结构（Leader + Team）需要重新组织。

完整 `agentteam/domain/team.py` 内容：

```python
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
        return Agent(
            name=self.name, role="supervisor",
            system_prompt=self.system_prompt, model=self.model,
            children=list(children) if children else [],
            approval_policy=self.approval_policy,
        )


@dataclass
class Team:
    """Team：顶层容器，root agent + 默认模型 + 团队级 MCP。

    两种构造方式：
    1. 新方式：Team(root=agent, ...)  —— 直接用 Agent 树
    2. 旧方式：Team(leader=..., workers=...)  —— 兼容，__post_init__ 转 root

    property leader/workers 从 root 反推。
    """
    name: str
    description: str
    default_model: ModelRef
    root: Agent | None = None
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
    _legacy_leader: Leader | None = field(default=None, repr=False, compare=False)
    _legacy_workers: list[Worker] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self):
        if self.root is None:
            leader = self._legacy_leader
            if leader is None:
                raise ValueError("Team requires either root or leader+workers")
            self.root = leader.to_agent(
                children=[w.to_agent() for w in self._legacy_workers]
            )

    @property
    def leader(self) -> Leader:
        return Leader(
            name=self.root.name, role="主管",
            system_prompt=self.root.system_prompt,
            model=self.root.model,
            approval_policy=self.root.approval_policy,
        )

    @leader.setter
    def leader(self, value: Leader | None):
        self._legacy_leader = value

    @property
    def workers(self) -> list[Worker]:
        result = []
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
    def workers(self, value: list[Worker]):
        self._legacy_workers = list(value)

    @classmethod
    def from_legacy(cls, *, name, description, leader, workers, default_model,
                    skills=None, mcp_servers=None) -> "Team":
        root = leader.to_agent(children=[w.to_agent() for w in workers])
        return cls(
            name=name, description=description, root=root,
            default_model=default_model,
            skills=list(skills) if skills else [],
            mcp_servers=list(mcp_servers) if mcp_servers else [],
        )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_team.py -v`
Expected: 全部 pass（含新增 4 个 + 既有 5 个）

- [ ] **Step 5: 运行全套 domain 测试确认无回归**

Run: `pytest tests/domain/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/team.py tests/domain/test_team.py
git commit -m "feat(domain): Team 加 root + from_legacy + property 兼容层"
```

---

### Task 5: 创建 AgentLibrary 专家库

**Files:**
- Create: `agentteam/domain/library.py`
- Test: `tests/domain/test_library.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/domain/test_library.py`：

```python
"""AgentLibrary 专家库单元测试。"""
import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelRef


def test_register_and_get():
    lib = AgentLibrary()
    a = Agent(name="coder", role="worker", system_prompt="code")
    lib.register(a)
    assert lib.get("coder") is a
    assert lib.get("nonexistent") is None


def test_register_duplicate_raises():
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker"))
    with pytest.raises(ValueError, match="already in library"):
        lib.register(Agent(name="coder", role="worker"))


def test_resolve_no_ref_returns_copy_with_resolved_children():
    """无 ref 时返回等价 Agent，children 递归 resolve。"""
    lib = AgentLibrary()
    a = Agent(
        name="lead", role="supervisor",
        children=[Agent(name="w", role="worker")],
    )
    resolved = lib.resolve(a)
    assert resolved.name == "lead"
    assert resolved.role == "supervisor"
    assert resolved.ref is None
    assert len(resolved.children) == 1
    assert resolved.children[0].name == "w"
    # 深拷贝验证：修改 resolved 不影响原对象
    resolved.children[0].name = "changed"
    assert a.children[0].name == "w"


def test_resolve_with_ref_deep_copies_template():
    """ref 指向库时深拷贝库定义。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt",
        tools=["read_file"], max_iterations=10,
    ))
    a = Agent(name="eng", role="worker", ref="library:code_engineer")
    resolved = lib.resolve(a)
    assert resolved.name == "eng"  # name 用调用处的
    assert resolved.system_prompt == "template prompt"
    assert resolved.tools == ["read_file"]
    assert resolved.max_iterations == 10
    assert resolved.ref is None


def test_resolve_with_ref_overrides_non_empty_fields():
    """调用处非空字段覆盖模板。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt",
        tools=["read_file"], max_iterations=10,
    ))
    m = ModelRef("qwen", "qwen-max")
    ap = ApprovalPolicy(level="tool", targets=["write_file"])
    a = Agent(
        name="eng", role="worker", ref="library:code_engineer",
        system_prompt="override prompt",
        model=m, tools=["write_file"], max_iterations=5,
        approval_policy=ap,
    )
    resolved = lib.resolve(a)
    assert resolved.system_prompt == "override prompt"
    assert resolved.model is m
    assert resolved.tools == ["write_file"]
    assert resolved.max_iterations == 5
    assert resolved.approval_policy is ap


def test_resolve_with_ref_overrides_children():
    """调用处 children 覆盖模板 children。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="team_lead", role="supervisor",
        system_prompt="template lead",
        children=[Agent(name="template_child", role="worker")],
    ))
    override_child = Agent(name="override_child", role="worker")
    a = Agent(
        name="lead", role="supervisor", ref="library:team_lead",
        children=[override_child],
    )
    resolved = lib.resolve(a)
    assert len(resolved.children) == 1
    assert resolved.children[0].name == "override_child"


def test_resolve_recursive_children():
    """库中 Agent 的 children 若也带 ref，递归 resolve。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="junior_eng", role="worker",
        system_prompt="junior template",
    ))
    lib.register(Agent(
        name="senior_eng", role="supervisor",
        system_prompt="senior template",
        children=[Agent(name="j", role="worker", ref="library:junior_eng")],
    ))
    a = Agent(name="s", role="supervisor", ref="library:senior_eng")
    resolved = lib.resolve(a)
    assert resolved.name == "s"
    assert resolved.role == "supervisor"
    assert len(resolved.children) == 1
    child = resolved.children[0]
    assert child.name == "j"  # name 用调用处（模板中是 "j"）
    assert child.system_prompt == "junior template"
    assert child.ref is None  # 递归解析后 ref 置空


def test_resolve_invalid_scheme_raises():
    lib = AgentLibrary()
    a = Agent(name="x", role="worker", ref="http://example.com/agent")
    with pytest.raises(ValueError, match="Unsupported ref scheme"):
        lib.resolve(a)


def test_resolve_unknown_library_agent_raises():
    lib = AgentLibrary()
    a = Agent(name="x", role="worker", ref="library:nonexistent")
    with pytest.raises(KeyError, match="not found in library"):
        lib.resolve(a)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/domain/test_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.domain.library'`

- [ ] **Step 3: 实现 AgentLibrary**

创建 `agentteam/domain/library.py`：

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/domain/test_library.py -v`
Expected: 9 passed

- [ ] **Step 5: 运行全套 domain 测试确认无回归**

Run: `pytest tests/domain/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/library.py tests/domain/test_library.py
git commit -m "feat(domain): 新增 AgentLibrary 专家库，支持 \$ref 引用复用"
```

---

## Commit 2：serializer 双轨

### Task 6: serializer 支持新旧 schema

**Files:**
- Modify: `agentteam/api/serializer.py`
- Test: `tests/api/test_serializer.py` (新增用例)

- [ ] **Step 1: 读现有 serializer 测试**

Run: `cat tests/api/test_serializer.py | head -60`

了解现有测试结构（不应破坏）。

- [ ] **Step 2: 写失败测试**

在 `tests/api/test_serializer.py` 末尾追加：

```python
def test_team_from_dict_new_schema():
    """新 schema：dict 含 root 字段。"""
    from agentteam.api.serializer import team_from_dict
    data = {
        "name": "t",
        "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "system_prompt": "你是主管",
            "children": [
                {"name": "w1", "role": "worker", "tools": ["read_file"]},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": ["read_file"],
        "mcp_servers": [],
    }
    team = team_from_dict(data)
    assert team.name == "t"
    assert team.root.role == "supervisor"
    assert team.root.name == "lead"
    assert len(team.root.children) == 1
    assert team.root.children[0].name == "w1"
    assert team.root.children[0].tools == ["read_file"]


def test_team_from_dict_new_schema_with_teamref():
    """新 schema：children 中含 TeamRef。"""
    from agentteam.api.serializer import team_from_dict
    data = {
        "name": "t", "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "children": [
                {"_type": "TeamRef", "name": "sub_team", "alias": "qa"},
                {"name": "w", "role": "worker"},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    from agentteam.domain.agent import TeamRef
    assert isinstance(team.root.children[0], TeamRef)
    assert team.root.children[0].name == "sub_team"
    assert team.root.children[0].alias == "qa"


def test_team_from_dict_new_schema_with_ref():
    """新 schema：Agent 含 ref 字段。"""
    from agentteam.api.serializer import team_from_dict
    data = {
        "name": "t", "description": "d",
        "root": {
            "name": "lead", "role": "supervisor",
            "children": [
                {"name": "eng", "role": "worker", "ref": "library:code_engineer"},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    assert team.root.children[0].ref == "library:code_engineer"


def test_team_to_dict_new_schema_roundtrip():
    """新 schema 序列化/反序列化往返。"""
    from agentteam.api.serializer import team_from_dict, team_to_dict
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="d",
        root=Agent(
            name="lead", role="supervisor",
            children=[
                Agent(name="w", role="worker", tools=["read_file"]),
                TeamRef(name="sub", alias="qa"),
            ],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    d = team_to_dict(team)
    assert "root" in d
    assert d["root"]["name"] == "lead"
    assert d["root"]["children"][0]["name"] == "w"
    assert d["root"]["children"][1]["_type"] == "TeamRef"
    # 往返
    team2 = team_from_dict(d)
    assert team2.root.name == "lead"
    assert len(team2.root.children) == 2


def test_team_from_dict_legacy_schema_still_works():
    """旧 schema（leader+workers）仍可解析。"""
    from agentteam.api.serializer import team_from_dict
    data = {
        "name": "dev_team",
        "description": "研发小队",
        "leader": {
            "name": "tech_lead", "role": "技术主管",
            "system_prompt": "你是主管",
            "model": {"provider": "qwen", "name": "qwen-max"},
        },
        "workers": [
            {"name": "coder", "role": "代码工程师", "description": "",
             "system_prompt": "你是代码工程师"},
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }
    team = team_from_dict(data)
    assert team.root.role == "supervisor"
    assert team.root.name == "tech_lead"
    assert len(team.root.children) == 1
    assert team.root.children[0].name == "coder"
    # 兼容 property
    assert team.leader.name == "tech_lead"
    assert team.workers[0].name == "coder"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `pytest tests/api/test_serializer.py::test_team_from_dict_new_schema -v`
Expected: FAIL with `TypeError` 或 `KeyError`（旧 serializer 不认 `root`）

- [ ] **Step 4: 修改 serializer**

替换 `agentteam/api/serializer.py` 全文：

```python
"""Team dataclass 与 JSON dict 之间的双向转换。

支持新旧 schema：
- 新 schema：dict 含 root（Agent 树）
- 旧 schema：dict 含 leader + workers（自动转 root）
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def _model_ref_from_dict(d: dict | None) -> ModelRef | None:
    if d is None:
        return None
    return ModelRef(
        provider=d["provider"],
        name=d["name"],
        temperature=d.get("temperature", 0.7),
        streaming=d.get("streaming", True),
    )


def _approval_policy_from_dict(d: dict | None) -> ApprovalPolicy | None:
    if d is None:
        return None
    return ApprovalPolicy(
        level=d["level"],
        targets=d.get("targets"),
        timeout_seconds=d.get("timeout_seconds"),
    )


def _leader_from_dict(d: dict) -> Leader:
    return Leader(
        name=d.get("name", "leader"),
        role=d.get("role", "主管"),
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
    )


def _worker_from_dict(d: dict) -> Worker:
    return Worker(
        name=d["name"],
        role=d["role"],
        description=d.get("description", ""),
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        tools=d.get("tools", []),
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
        max_iterations=d.get("max_iterations", 10),
    )


def _mcp_server_from_dict(d: dict) -> MCPServer:
    return MCPServer(
        name=d["name"],
        command=d["command"],
        args=d.get("args", []),
        env=d.get("env", {}),
        transport=d.get("transport", "stdio"),
        url=d.get("url"),
    )


def _agent_from_dict(d: dict) -> Agent:
    children: list[Agent | TeamRef] = []
    for c in d.get("children", []):
        if c.get("_type") == "TeamRef":
            children.append(TeamRef(name=c["name"], alias=c.get("alias")))
        else:
            children.append(_agent_from_dict(c))
    return Agent(
        name=d["name"],
        role=d["role"],
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        children=children,
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
        tools=d.get("tools", []),
        max_iterations=d.get("max_iterations", 10),
        ref=d.get("ref"),
    )


def _agent_to_dict(agent: Agent) -> dict:
    children = []
    for c in agent.children:
        if isinstance(c, TeamRef):
            children.append({"_type": "TeamRef", "name": c.name, "alias": c.alias})
        else:
            children.append(_agent_to_dict(c))
    return {
        "name": agent.name,
        "role": agent.role,
        "system_prompt": agent.system_prompt,
        "model": asdict(agent.model) if agent.model else None,
        "children": children,
        "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        "tools": list(agent.tools),
        "max_iterations": agent.max_iterations,
        "ref": agent.ref,
    }


def team_to_dict(team: Team) -> dict[str, Any]:
    """Team → JSON-serializable dict（新 schema：含 root 字段）。"""
    return {
        "name": team.name,
        "description": team.description,
        "root": _agent_to_dict(team.root),
        "default_model": asdict(team.default_model),
        "skills": list(team.skills),
        "mcp_servers": [asdict(s) for s in team.mcp_servers],
    }


def team_from_dict(data: dict[str, Any]) -> Team:
    """dict → Team，支持新旧 schema。

    - 新 schema：data 含 'root' 字段
    - 旧 schema：data 含 'leader' + 'workers' 字段，自动转 root
    """
    if "root" in data:
        return Team(
            name=data["name"],
            description=data["description"],
            root=_agent_from_dict(data["root"]),
            default_model=_model_ref_from_dict(data["default_model"]),  # type: ignore[arg-type]
            skills=data.get("skills", []),
            mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
        )
    # 旧 schema
    leader = _leader_from_dict(data["leader"])
    workers = [_worker_from_dict(w) for w in data["workers"]]
    return Team.from_legacy(
        name=data["name"],
        description=data["description"],
        leader=leader,
        workers=workers,
        default_model=_model_ref_from_dict(data["default_model"]),  # type: ignore[arg-type]
        skills=data.get("skills", []),
        mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
    )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `pytest tests/api/test_serializer.py -v`
Expected: 全部 pass（含新增 5 个 + 既有用例）

- [ ] **Step 6: 运行 API 与集成测试确认无回归**

Run: `pytest tests/api/ tests/integration/ -v`
Expected: 全部 pass

- [ ] **Step 7: 提交**

```bash
git add agentteam/api/serializer.py tests/api/test_serializer.py
git commit -m "feat(api): serializer 双轨支持新旧 schema"
```

---

## Commit 3：TeamCompiler 递归编译

### Task 7: TeamState 加 path 字段

**Files:**
- Modify: `agentteam/runtime/state.py`
- Test: `tests/runtime/test_state.py` (新增；当前不存在)

- [ ] **Step 1: 写失败测试**

创建 `tests/runtime/test_state.py`：

```python
"""TeamState schema 单元测试。"""
from agentteam.runtime.state import TeamState, WorkerState, is_rejected


def test_team_state_has_path_field():
    """TeamState 应包含 path 字段用于跨层追踪。"""
    # TypedDict 在运行时是 dict，可直接构造
    state: TeamState = {
        "messages": [], "task": "t", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": "r1",
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }
    assert state["path"] == "team:t"


def test_team_state_path_field_in_annotations():
    """path 字段应在 TeamState 的 __annotations__ 中。"""
    assert "path" in TeamState.__annotations__


def test_is_rejected_with_path():
    """is_rejected 不受 path 影响。"""
    state = {"pending_approval": {"approved": False}, "path": "team:t"}
    assert is_rejected(state) is True

    state2 = {"pending_approval": {"approved": True}, "path": "team:t"}
    assert is_rejected(state2) is False

    state3 = {"pending_approval": None, "path": "team:t"}
    assert is_rejected(state3) is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/runtime/test_state.py::test_team_state_has_path_field -v`
Expected: FAIL（构造 dict 不会失败，但 `test_team_state_path_field_in_annotations` 会失败）

实际第一个测试会通过（dict 构造无校验），第二个会失败。所以预期：

Run: `pytest tests/runtime/test_state.py -v`
Expected: `test_team_state_path_field_in_annotations` FAIL

- [ ] **Step 3: 给 TeamState 加 path 字段**

修改 `agentteam/runtime/state.py`，在 TeamState 末尾加 `path` 字段：

```python
class TeamState(TypedDict):
    """Team 执行图的全局状态。"""

    messages: Annotated[list, add_messages]
    task: str
    plan: list[Step]
    current_step: int
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    audit_events: Annotated[list, operator.add]
    run_id: str
    pending_approval: dict | None
    total_tokens: Annotated[int, operator.add]
    # 新增：跨层执行路径追踪，如 "team:dev.ceo.cto"
    path: str
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/runtime/test_state.py -v`
Expected: 3 passed

- [ ] **Step 5: 运行 runtime 既有测试确认无回归**

Run: `pytest tests/runtime/ -v`
Expected: 全部 pass（path 是新字段，旧测试不传不会报错——TypedDict 不强制）

- [ ] **Step 6: 提交**

```bash
git add agentteam/runtime/state.py tests/runtime/test_state.py
git commit -m "feat(runtime): TeamState 加 path 字段用于跨层追踪"
```

---

### Task 8: 节点工厂接受 Agent 类型

**Files:**
- Modify: `agentteam/runtime/nodes.py`
- Test: `tests/runtime/test_graph.py` (确保既有测试仍通过)

注意：`make_leader_plan_node` / `make_leader_review_node` 当前签名是 `(leader: Leader, ...)`，`make_worker_subgraph` 是 `(worker: Worker, ...)`。改为 `(agent: Agent, ...)`，内部访问 `agent.name` / `agent.system_prompt` 等同名字段。

- [ ] **Step 1: 检查现有节点工厂代码**

Run: `head -100 agentteam/runtime/nodes.py`

确认工厂内部对 `leader.name` / `leader.system_prompt` / `leader.approval_policy` 的访问，这些字段在 Agent 上都有同名属性。

- [ ] **Step 2: 修改类型注解**

修改 `agentteam/runtime/nodes.py`：

1. 顶部 import 改为：
```python
from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter
```
（移除 `from agentteam.domain.team import Leader` 和 `from agentteam.domain.worker import Worker`，若 nodes.py 内有这些 import）

2. `make_leader_plan_node(leader: Leader, llm, trace_writer=None)` 改为 `make_leader_plan_node(agent: Agent, llm, trace_writer=None)`，函数体内 `leader.name` → `agent.name`，`leader.system_prompt` → `agent.system_prompt`，`leader.name` → `agent.name`（review 函数同理）。

3. `make_worker_subgraph(worker: Worker, llm, tools, trace_writer=None, audit_repo=None)` 改为 `make_worker_subgraph(agent: Agent, llm, tools, trace_writer=None, audit_repo=None)`，函数体内 `worker.name` → `agent.name`，`worker.system_prompt` → `agent.system_prompt`，`worker.approval_policy` → `agent.approval_policy`，`worker.max_iterations` → `agent.max_iterations`。

4. `make_init_worker` / `make_agent_step` / `make_finalize` / `make_tool_step` 内部参数名 `worker` 改为 `agent`，访问 `worker.name` 等改为 `agent.name`。

完整替换 `agentteam/runtime/nodes.py` 中所有节点工厂签名与函数体（仅类型与变量名变更，逻辑不变）。具体替换如下（节选关键函数签名）：

```python
def make_leader_plan_node(
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None,
):
    def leader_plan(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        task = state["task"]
        messages = [
            SystemMessage(content=agent.system_prompt),
            HumanMessage(content=f"请把以下任务拆解成可执行的步骤计划，每步指派一个 worker：\n\n{task}"),
        ]
        structured = llm.with_structured_output(Plan)
        plan_obj = structured.invoke(messages)
        plan = [
            {"worker": s.worker, "instruction": s.instruction, "status": "pending"}
            for s in plan_obj.steps
        ]
        if trace_writer:
            trace_writer.emit(run_id, "leader_plan", agent.name, {"steps": len(plan)})
        return {
            "plan": plan, "current_step": 0,
            "messages": [AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=agent.name)],
            "audit_events": [{"event_type": "leader_plan", "actor": agent.name}],
        }
    return leader_plan


def make_leader_review_node(
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None,
):
    def leader_review(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        current = state["current_step"]
        plan = list(state["plan"])
        plan[current] = {**plan[current], "status": "done"}
        worker_name = plan[current]["worker"]
        outputs = state.get("worker_outputs", {})
        review_response = llm.invoke([
            SystemMessage(content=agent.system_prompt),
            HumanMessage(content=(
                f"Worker {worker_name} 完成了步骤 {current}，"
                f"产出：{outputs.get(worker_name, '')}。请简要点评。"
            )),
        ])
        if trace_writer:
            trace_writer.emit(run_id, "leader_review", agent.name)
        usage = getattr(review_response, "usage_metadata", None)
        tokens = usage.get("total_tokens", 0) if usage else 0
        return {
            "plan": plan, "current_step": current + 1,
            "messages": [AIMessage(content=f"[Leader] {review_response.content}", name=agent.name)],
            "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
            "total_tokens": tokens,
        }
    return leader_review


def make_worker_subgraph(
    agent: Agent, llm: BaseChatModel, tools: list[BaseTool],
    trace_writer: TraceWriter | None = None, audit_repo=None,
):
    # ... 内部所有 worker.name / worker.system_prompt / worker.approval_policy / worker.max_iterations
    # 改为 agent.name / agent.system_prompt / agent.approval_policy / agent.max_iterations
    ...
```

注意：`make_init_worker` / `make_agent_step` / `make_finalize` / `make_tool_step` 的参数 `worker: Worker` 都改为 `agent: Agent`，内部访问相应改名。

- [ ] **Step 3: 运行既有 graph 测试确认无回归**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: 全部 pass（类型注解与变量名变更，访问语义不变；旧测试用 Leader/Worker 构造，Leader/Worker 有同名字段，但因工厂现在接受 Agent 类型，需要测试代码传入 Agent）

如果失败：旧测试 `_make_team()` 用 Leader/Worker 构造 Team，经 `Team.__post_init__` 转换后 `team.root` 是 Agent。但旧测试调用 `compiler.compile(team)` 时，编译器内部用 `team.leader` / `team.workers`（仍是 Leader/Worker 对象）。需要看 Task 9 的编译器是否兼容。

**重要**：此 Task 仅改 nodes.py 的类型注解，编译器仍用旧 graph.py。Leader/Worker 对象因有同名字段（name/system_prompt/model/approval_policy/tools/max_iterations），节点工厂内部访问不会报错。但如果工厂用 `isinstance(agent, Agent)` 之类则不行——我们的实现只访问字段，不做 isinstance，所以应该通过。

- [ ] **Step 4: 运行全套 runtime 测试**

Run: `pytest tests/runtime/ -v`
Expected: 全部 pass

- [ ] **Step 5: 提交**

```bash
git add agentteam/runtime/nodes.py
git commit -m "refactor(runtime): 节点工厂接受 Agent 类型（变量名 worker→agent）"
```

---

### Task 9: TeamCompiler 递归编译

**Files:**
- Modify: `agentteam/runtime/graph.py` (重写)
- Test: `tests/runtime/test_graph.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/runtime/test_graph.py` 末尾追加：

```python
def test_compile_recursive_three_level_chain(fake_llm):
    """3 级 supervisor 链编译成功，node_names 包含各层节点。"""
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="3 level",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                children=[Agent(name="eng", role="worker", system_prompt="eng")],
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    # ceo 的 child 是 cto（agent_cto 节点，内部含子图）
    assert "agent_cto" in node_names


def test_compile_with_team_ref(fake_llm):
    """Team 嵌套：父 Team 引用子 Team 作为 child。"""
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            children=[Agent(name="w", role="worker")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(sub_team)
    main_team = Team(
        name="main", description="main",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            children=[TeamRef(name="sub", alias="qa")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(main_team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "subteam_qa" in node_names


def test_compile_team_ref_not_registered_raises(fake_llm):
    """TeamRef 指向未注册 Team → KeyError。"""
    import pytest
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[TeamRef(name="nonexistent")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(KeyError, match="Team not registered"):
        compiler.compile(team)


def test_compile_max_depth_exceeded_raises(fake_llm):
    """depth > MAX_DEPTH → ValueError。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    compiler.MAX_DEPTH = 3  # 测试用调小

    # 构造 4 级链
    leaf = Agent(name="w", role="worker")
    l3 = Agent(name="l3", role="supervisor", children=[leaf])
    l2 = Agent(name="l2", role="supervisor", children=[l3])
    l1 = Agent(name="l1", role="supervisor", children=[l2])
    root = Agent(name="root", role="supervisor", children=[l1])
    team = Team(
        name="t", description="deep", root=root,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="Max depth exceeded"):
        compiler.compile(team)


def test_compile_circular_team_ref_raises(fake_llm):
    """循环 TeamRef：A 引用 B，B 引用 A → ValueError。"""
    import pytest
    from agentteam.domain.agent import Agent, TeamRef
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    # 通过 register_team 注册两个互相引用的 Team
    # 注意：实际定义时不能直接互相引用（构造时即报错），
    # 通过 TeamRef 名称引用，运行时编译期检测循环
    team_a = Team(
        name="team_a", description="a",
        root=Agent(name="la", role="supervisor",
                   children=[TeamRef(name="team_b", alias="b")]),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    team_b = Team(
        name="team_b", description="b",
        root=Agent(name="lb", role="supervisor",
                   children=[TeamRef(name="team_a", alias="a")]),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    compiler.register_team(team_a)
    compiler.register_team(team_b)
    with pytest.raises(ValueError, match="Circular team reference"):
        compiler.compile(team_a)


def test_compile_supervisor_with_tools_raises(fake_llm):
    """supervisor 不能有 tools。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(name="w", role="worker")],
            tools=["read_file"],  # 非法
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="supervisor cannot have tools"):
        compiler.compile(team)


def test_compile_worker_with_children_raises(fake_llm):
    """worker 不能有 children。"""
    import pytest
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(
                name="w", role="worker",
                children=[Agent(name="x", role="worker")],  # 非法
            )],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="worker cannot have children"):
        compiler.compile(team)


def test_compile_with_library_ref(fake_llm):
    """专家库引用：Agent(ref=...) 经 resolve 后编译。"""
    from agentteam.domain.agent import Agent
    from agentteam.domain.library import AgentLibrary
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template", tools=["read_file"], max_iterations=5,
    ))
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry(), library=lib)
    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(name="eng", role="worker", ref="library:code_engineer")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(team)
    assert graph is not None  # 编译成功
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/runtime/test_graph.py -v -k "recursive or team_ref or max_depth or circular or supervisor_with_tools or worker_with_children or library_ref"`
Expected: FAIL（旧 TeamCompiler 不支持 root 构造）

- [ ] **Step 3: 重写 TeamCompiler**

替换 `agentteam/runtime/graph.py` 全文：

```python
"""TeamCompiler：递归编译 Agent 树为 LangGraph StateGraph。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_subgraph,
)
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import TraceWriter
from agentteam.tools.registry import ToolRegistry


def route_from_plan(state: TeamState) -> str:
    """旧模块级函数：仅用于旧测试兼容。
    
    新代码用 make_route_from_plan(child_targets) 工厂。
    """
    plan = state.get("plan", [])
    if not plan:
        return END
    return f"worker_{plan[0]['worker']}"


def route_from_review(state: TeamState) -> str:
    """旧模块级函数：仅用于旧测试兼容。"""
    current = state.get("current_step", 0)
    plan = state.get("plan", [])
    if current >= len(plan):
        return END
    return f"worker_{plan[current]['worker']}"


def route_to_worker(state: TeamState) -> str:
    """统一路由：拒绝→END，无更多步骤→END，否则→下一步 worker。"""
    if is_rejected(state):
        return END
    return route_from_review(state)


def make_route_from_plan(child_targets: dict[str, str]):
    """创建路由函数：plan[0].worker → child_targets[name]。"""
    def route(state: TeamState) -> str:
        plan = state.get("plan", [])
        if not plan:
            return END
        return child_targets[plan[0]["worker"]]
    return route


def make_route_from_review(child_targets: dict[str, str]):
    """创建路由函数：current_step → child_targets[name]。"""
    def route(state: TeamState) -> str:
        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current >= len(plan):
            return END
        return child_targets[plan[current]["worker"]]
    return route


def make_route_to_worker(child_targets: dict[str, str]):
    """创建路由函数：拒绝→END，否则→make_route_from_review。"""
    inner = make_route_from_review(child_targets)
    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return inner(state)
    return route


def make_route_after_worker_gate(worker_node_name: str):
    """创建 worker_gate 之后的路由函数：拒绝→END，否则→worker。"""
    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return worker_node_name
    return route


class TeamCompiler:
    """把 Team 配置（Agent 树）递归编译成可执行的 LangGraph StateGraph。"""

    MAX_DEPTH = 8

    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        library: AgentLibrary | None = None,
    ):
        self._mp = model_provider
        self._tr = tool_registry
        self._lib = library or AgentLibrary()
        self._team_registry: dict[str, Team] = {}

    def register_team(self, team: Team) -> None:
        """注册可被 TeamRef 引用的 Team。"""
        self._team_registry[team.name] = team

    def register_library(self, library: AgentLibrary) -> None:
        self._lib = library

    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        # 加载 team 级 MCP（沿用现状）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)
        # 校验 root
        if team.root.role != "supervisor":
            raise ValueError("Team.root must be supervisor")
        # 递归编译 root
        return self._compile_agent(
            team.root, team.default_model, checkpointer,
            trace_writer, audit_repo,
            depth=0, path=f"team:{team.name}",
        )

    def _compile_agent(
        self, agent: Agent, default_model, checkpointer,
        trace_writer, audit_repo, depth, path,
    ):
        # 1. 解析 ref（深拷贝库定义，保留覆盖）
        agent = self._lib.resolve(agent)
        # 2. 校验
        self._validate(agent, depth, path)
        # 3. 按 role 分派
        if agent.role == "worker":
            return self._compile_worker(agent, default_model, trace_writer, audit_repo)
        return self._compile_supervisor(
            agent, default_model, checkpointer, trace_writer, audit_repo,
            depth, path,
        )

    def _validate(self, agent: Agent, depth: int, path: str) -> None:
        if depth > self.MAX_DEPTH:
            raise ValueError(f"Max depth exceeded: >{self.MAX_DEPTH} at {path}")
        if agent.role == "supervisor":
            if not agent.children:
                raise ValueError(f"supervisor must have children: {agent.name}")
            if agent.tools:
                raise ValueError(f"supervisor cannot have tools: {agent.name}")
        elif agent.role == "worker":
            if agent.children:
                raise ValueError(f"worker cannot have children: {agent.name}")
        else:
            raise ValueError(f"Unknown role: {agent.role}")

    def _compile_supervisor(
        self, agent: Agent, default_model, checkpointer,
        trace_writer, audit_repo, depth, path,
    ):
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
                    raise ValueError(
                        f"Circular team reference: {path}.{alias}"
                    )
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

                # worker 级审批 gate
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

        # 路由目标映射
        physical_targets: dict[str, str] = {}
        for logical in child_targets:
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
            graph.add_conditional_edges(
                "step_gate",
                make_route_to_worker(physical_targets),
                physical_targets,
            )
        else:
            graph.add_conditional_edges(
                "leader_plan",
                make_route_from_plan(physical_targets),
                physical_targets,
            )

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
            graph.add_conditional_edges(
                "leader_review",
                make_route_from_review(physical_targets),
                physical_targets,
            )

        return graph.compile(checkpointer=checkpointer)

    def _compile_worker(
        self, agent: Agent, default_model, trace_writer, audit_repo,
    ):
        """worker 沿用 make_worker_subgraph。"""
        llm = self._mp.get_llm(agent.model or default_model)
        tools = self._tr.get_tools(agent.tools) if agent.tools else []
        return make_worker_subgraph(agent, llm, tools, trace_writer, audit_repo)
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `pytest tests/runtime/test_graph.py -v -k "recursive or team_ref or max_depth or circular or supervisor_with_tools or worker_with_children or library_ref"`
Expected: 全部 pass

- [ ] **Step 5: 运行既有 graph 测试确认无回归**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: 全部 pass

**如果旧测试失败**：旧测试用 `Team(leader=..., workers=...)` 构造，新 Team 经 `__post_init__` 转 root。编译器内部用 `team.root` 访问。但旧测试的 `route_from_plan(state) == "worker_coder"` 期望返回 `"worker_coder"`，新 `make_route_from_plan(child_targets)` 返回 `child_targets["coder"] = "agent_coder"`。

**修复**：旧测试用模块级 `route_from_plan`（保留旧签名返回 `f"worker_{name}"`），新编译器用工厂函数。但旧测试 `test_route_from_plan_returns_first_worker` 直接调模块级函数，不通过编译器。所以旧测试中调用模块级 `route_from_plan` 的测试仍 pass。

但 `test_team_compiler_produces_runnable_graph` 等测试通过 `compiler.compile(team)` 编译后检查 `node_names` 包含 `"worker_coder"`，而新编译器产生 `"agent_coder"`。**这是预期的破坏性变更**。

**修复策略**：旧测试用 `Team(leader=..., workers=...)` 构造的团队，编译后 node 名应为 `"worker_coder"` 而非 `"agent_coder"`。两种选择：

a) 改旧测试期望（违反"不动旧测试"原则）
b) 编译器对 worker Agent 的节点名用 `"worker_{name}"` 而非 `"agent_{name}"`，对 supervisor Agent 用 `"agent_{name}"`，对 TeamRef 用 `"subteam_{alias}"`

选 b）。修改 `_compile_supervisor` 中：

```python
# 原：node_name = f"agent_{child.name}"
# 改为：
if child.role == "worker":
    node_name = f"worker_{child.name}"
else:
    node_name = f"agent_{child.name}"
```

并在编译 children 时，普通 Agent 分支根据 role 决定 node_name。

- [ ] **Step 6: 调整 _compile_supervisor 的 node_name**

修改 `_compile_supervisor` 中普通 Agent 分支：

```python
            else:
                sub_graph = self._compile_agent(
                    child, default_model, checkpointer, trace_writer, audit_repo,
                    depth=depth + 1, path=f"{path}.{child.name}",
                )
                # worker 用 worker_{name} 保持与旧测试兼容；supervisor 用 agent_{name}
                if child.role == "worker":
                    node_name = f"worker_{child.name}"
                else:
                    node_name = f"agent_{child.name}"
                graph.add_node(node_name, sub_graph)
                child_targets[child.name] = node_name
```

- [ ] **Step 7: 重新运行既有 graph 测试**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: 全部 pass

- [ ] **Step 8: 运行全套 runtime + integration 测试**

Run: `pytest tests/runtime/ tests/integration/ -v`
Expected: 全部 pass

- [ ] **Step 9: 提交**

```bash
git add agentteam/runtime/graph.py tests/runtime/test_graph.py
git commit -m "feat(runtime): TeamCompiler 递归编译支持多级层级与 Team 嵌套"
```

---

## Commit 4：跨层审批 + CLI + 示例

### Task 10: routes/runs.py 注入 library 与 team_registry

**Files:**
- Modify: `agentteam/api/routes/runs.py`
- Modify: `agentteam/api/server.py`
- Test: `tests/api/test_api_runs.py` (确保既有测试通过)

- [ ] **Step 1: 修改 server.py 初始化 AgentLibrary**

修改 `agentteam/api/server.py` 的 `create_app` 函数，新增 `agent_library` 参数与初始化：

```python
def create_app(
    db_path: str = "data/agentteam.db",
    model_provider: ModelProvider | None = None,
    tool_registry: ToolRegistry | None = None,
    agent_library: AgentLibrary | None = None,
    web_dist: Path | None | object = _DEFAULT,
) -> FastAPI:
    app = FastAPI(title="AgentTeam")

    conn = init_db(db_path)
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary()

    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    assert saver.lock is conn_lock
    saver.setup()

    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, mp, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver, agent_library=lib,
        )
    )
    app.include_router(dashboard_router(run_repo, audit_repo))

    # 挂载前端
    if web_dist is _DEFAULT:
        web_dist_path: Path | None = _DEFAULT_WEB_DIST
    elif web_dist is None:
        web_dist_path = None
    else:
        web_dist_path = web_dist

    if web_dist_path is not None and web_dist_path.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist_path), html=True), name="web")

    return app
```

并在 import 区添加：
```python
from agentteam.domain.library import AgentLibrary
```

- [ ] **Step 2: 修改 runs_router 接受 agent_library**

修改 `agentteam/api/routes/runs.py` 的 `runs_router` 签名与 `create_run` 内部：

```python
def runs_router(
    run_manager: RunManager,
    team_store: TeamStore,
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    run_repo: RunRepo,
    audit_repo: AuditRepo,
    event_bus: EventBus,
    checkpointer=None,
    agent_library: AgentLibrary | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/runs", tags=["runs"])
    lib = agent_library or AgentLibrary()

    @router.post("")
    def create_run(req: CreateRunRequest):
        team = team_store.get(req.team_name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{req.team_name}' not found")

        run_id = run_repo.create_run(team.name, req.task)

        trace_writer = BroadcastTraceWriter(audit_repo, event_bus)
        compiler = TeamCompiler(model_provider, tool_registry, library=lib)
        # 注册所有已知 Team 到 compiler._team_registry，使 TeamRef 可解析
        for name, t in team_store.list_all().items():
            compiler.register_team(t)
        try:
            graph = compiler.compile(
                team, checkpointer=checkpointer,
                trace_writer=trace_writer, audit_repo=audit_repo,
            )
        except Exception as e:
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(run_id, "error", "system", {"error": str(e)})
            event_bus.publish(run_id, {
                "id": eid, "event_type": "error",
                "run_id": run_id, "payload": {"error": str(e)},
            })
            raise HTTPException(status_code=400, detail=f"Compile failed: {e}")

        config = {"configurable": {"thread_id": run_id}}
        run_manager.start_run(run_id, graph, config, req.task)
        return {"run_id": run_id}

    # ... 其他端点不变
```

并在 import 区添加：
```python
from agentteam.domain.library import AgentLibrary
```

- [ ] **Step 3: 检查 TeamStore 是否有 list_all 方法**

Run: `grep -n "def " agentteam/api/store.py`

如果没有 `list_all`，需要添加。

- [ ] **Step 4: 给 TeamStore 加 list_all 方法（如不存在）**

修改 `agentteam/api/store.py`，添加：

```python
def list_all(self) -> dict[str, Team]:
    """返回所有已注册 Team 的副本（name → Team）。"""
    return dict(self._teams)
```

- [ ] **Step 5: 运行 API 测试确认无回归**

Run: `pytest tests/api/ -v`
Expected: 全部 pass

- [ ] **Step 6: 提交**

```bash
git add agentteam/api/server.py agentteam/api/routes/runs.py agentteam/api/store.py
git commit -m "feat(api): runs_router 注入 AgentLibrary 与 team_registry"
```

---

### Task 11: 多级层级集成测试

**Files:**
- Create: `tests/integration/test_multi_level.py`

- [ ] **Step 1: 写 3 级链 E2E 测试**

创建 `tests/integration/test_multi_level.py`：

```python
"""多级层级 + Team 嵌套 + 专家库集成测试。"""
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_e2e_three_level_chain():
    """3 级 supervisor 链：CEO → CTO → eng，全部跑通。"""
    # CEO LLM：拆 1 步给 cto
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[PlanStep(worker="cto", instruction="做技术")])])
    ceo_llm.set_invoke_responses([AIMessage(content="cto 干得不错")])

    # CTO LLM：拆 1 步给 eng
    cto_llm = FakeLLM()
    cto_llm.set_structured_responses([Plan(steps=[PlanStep(worker="eng", instruction="写代码")])])
    cto_llm.set_invoke_responses([AIMessage(content="eng 干得不错")])

    # eng LLM：直接给答案
    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="print('hello')")])

    provider = FakeModelProvider({
        "ceo-model": ceo_llm, "cto-model": cto_llm, "eng-model": eng_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="3 level",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                model=ModelRef("qwen", "cto-model"),
                children=[Agent(
                    name="eng", role="worker", system_prompt="eng",
                    model=ModelRef("qwen", "eng-model"),
                )],
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "3level"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该已完成"
    # eng 的产出
    assert state.values["worker_outputs"].get("eng") == "print('hello')"
    # 3 个 leader_plan 事件（CEO + CTO + 0 个 root review 不算）
    leader_plans = [e for e in state.values["audit_events"]
                    if e["event_type"] == "leader_plan"]
    assert len(leader_plans) == 2  # CEO 和 CTO 各 1 次


def test_e2e_team_nesting():
    """Team 嵌套：父 Team 引用子 Team，子 Team 内部独立编排。"""
    # 父 leader LLM：拆 1 步给 qa（子 Team 的 alias）
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[PlanStep(worker="qa", instruction="测试")])])
    parent_llm.set_invoke_responses([AIMessage(content="qa 完成")])

    # 子 leader LLM：拆 1 步给 tester
    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[PlanStep(worker="tester", instruction="写测试")])])
    sub_llm.set_invoke_responses([AIMessage(content="tester 完成")])

    # tester LLM
    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([AIMessage(content="assert True")])

    provider = FakeModelProvider({
        "parent-model": parent_llm,
        "sub-model": sub_llm,
        "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="main",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            model=ModelRef("qwen", "parent-model"),
            children=[TeamRef(name="sub", alias="qa")],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "nesting"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    # tester 的产出（嵌套子图中产出冒泡到父图）
    assert state.values["worker_outputs"].get("tester") == "assert True"


def test_e2e_library_ref():
    """专家库引用：Agent(ref=...) 被派活，库中 system_prompt 生效。"""
    # CEO LLM：拆 1 步给 eng
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[PlanStep(worker="eng", instruction="写代码")])])
    ceo_llm.set_invoke_responses([AIMessage(content="ok")])

    # eng LLM
    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="code done")])

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt for code engineer",
        max_iterations=5,
    ))

    provider = FakeModelProvider({"ceo-model": ceo_llm, "eng-model": eng_llm})
    compiler = TeamCompiler(provider, ToolRegistry(), library=lib)
    team = Team(
        name="t", description="t",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="eng", role="worker",
                model=ModelRef("qwen", "eng-model"),
                ref="library:code_engineer",
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "lib"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"].get("eng") == "code done"


def test_e2e_mixed_all_features():
    """混合：3 级链 + Team 嵌套 + 专家库。"""
    # CEO LLM：拆 1 步给 cto
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="cto", instruction="做技术"),
    ])])
    ceo_llm.set_invoke_responses([AIMessage(content="cto done")])

    # CTO LLM：拆 2 步给 eng（库引用）和 qa（子 Team）
    cto_llm = FakeLLM()
    cto_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="eng", instruction="写代码"),
        PlanStep(worker="qa", instruction="测试"),
    ])])
    cto_llm.set_invoke_responses([
        AIMessage(content="eng done"),
        AIMessage(content="qa done"),
    ])

    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="code")])

    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="tester", instruction="写测试"),
    ])])
    sub_llm.set_invoke_responses([AIMessage(content="sub done")])

    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([AIMessage(content="assert True")])

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="eng template", max_iterations=5,
    ))

    provider = FakeModelProvider({
        "ceo-model": ceo_llm, "cto-model": cto_llm,
        "eng-model": eng_llm, "sub-model": sub_llm, "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry(), library=lib)

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="all features",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                model=ModelRef("qwen", "cto-model"),
                children=[
                    Agent(
                        name="eng", role="worker",
                        model=ModelRef("qwen", "eng-model"),
                        ref="library:code_engineer",
                    ),
                    TeamRef(name="sub", alias="qa"),
                ],
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "mixed"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    # 两个 worker 都有产出
    assert state.values["worker_outputs"].get("eng") == "code"
    assert state.values["worker_outputs"].get("tester") == "assert True"
```

- [ ] **Step 2: 运行测试验证**

Run: `pytest tests/integration/test_multi_level.py -v`
Expected: 4 passed

如果失败：检查 FakeLLM 的 invoke_responses 设置是否覆盖了所有 invoke 调用（leader_plan 用 structured，leader_review 用 invoke，worker 用 invoke）。

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_multi_level.py
git commit -m "test(integration): 多级层级 + Team 嵌套 + 专家库 E2E"
```

---

### Task 12: 跨层审批集成测试

**Files:**
- Create: `tests/integration/test_cross_level_approval.py`

- [ ] **Step 1: 写跨层审批测试**

创建 `tests/integration/test_cross_level_approval.py`：

```python
"""跨层审批集成测试。"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_cross_level_step_and_tool_approval(fake_trace_writer, tmp_path):
    """父层 step 级审批 + 子 worker tool 级审批，分别 interrupt 与 resume。"""
    # 父 leader LLM：拆 1 步给 child
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="child", instruction="do work"),
    ])])
    parent_llm.set_invoke_responses([AIMessage(content="child done")])

    # child worker LLM：先调工具，再给答案
    child_llm = FakeLLM()
    child_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "write_file", "args": {"content": "x"},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="work done"),
    ])

    # write_file 工具
    target = tmp_path / "out.txt"
    def write_file(content: str) -> str:
        target.write_text(content, encoding="utf-8")
        return "written"
    tool = StructuredTool.from_function(
        name="write_file", description="write", func=write_file,
    )
    reg = ToolRegistry()
    reg.register(tool)

    provider = FakeModelProvider({
        "parent-model": parent_llm, "child-model": child_llm,
    })
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t", description="cross level",
        root=Agent(
            name="parent", role="supervisor", system_prompt="parent",
            model=ModelRef("qwen", "parent-model"),
            approval_policy=ApprovalPolicy(level="step"),  # 父层 step 级
            children=[Agent(
                name="child", role="worker", system_prompt="child",
                model=ModelRef("qwen", "child-model"),
                tools=["write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer,
    )
    config = {"configurable": {"thread_id": "cross"}}

    # 第一次 invoke：应在父 step_gate 处 interrupt
    graph.invoke(_initial_state(), config)
    state = graph.get_state(config)
    assert state.next, "应在父层 step_gate 处 interrupt"

    # Resume 父层审批：批准
    graph.invoke(Command(resume={"approved": True, "decider": "user"}), config)
    state = graph.get_state(config)
    assert state.next, "应在子层 tool_step 处 interrupt"

    # Resume 子层审批：批准
    graph.invoke(Command(resume={"approved": True, "decider": "user"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证工具被执行
    assert target.read_text(encoding="utf-8") == "x"

    # 验证两层审批事件都有
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    approval_requests = [i for i, t in enumerate(event_types) if t == "approval_requested"]
    approval_decideds = [i for i, t in enumerate(event_types) if t == "approval_decided"]
    assert len(approval_requests) == 2  # 父 step + 子 tool
    assert len(approval_decideds) == 2

    # 验证 worker 产出
    assert state.values["worker_outputs"].get("child") == "work done"


def test_cross_level_step_rejection_terminates(fake_trace_writer):
    """父层 step 级审批拒绝 → 图终止，子层不执行。"""
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="child", instruction="do work"),
    ])])
    # 不会用到 review，因为拒绝后终止

    child_llm = FakeLLM()
    child_llm.set_invoke_responses([AIMessage(content="should not run")])

    provider = FakeModelProvider({
        "parent-model": parent_llm, "child-model": child_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="reject",
        root=Agent(
            name="parent", role="supervisor", system_prompt="parent",
            model=ModelRef("qwen", "parent-model"),
            approval_policy=ApprovalPolicy(level="step"),
            children=[Agent(
                name="child", role="worker", system_prompt="child",
                model=ModelRef("qwen", "child-model"),
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer,
    )
    config = {"configurable": {"thread_id": "reject"}}

    # 第一次 invoke：interrupt
    graph.invoke(_initial_state(), config)
    state = graph.get_state(config)
    assert state.next

    # Resume：拒绝
    graph.invoke(Command(resume={"approved": False, "decider": "user"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应已终止"

    # worker 未执行
    assert "child" not in state.values.get("worker_outputs", {})
```

- [ ] **Step 2: 运行测试验证**

Run: `pytest tests/integration/test_cross_level_approval.py -v`
Expected: 2 passed

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_cross_level_approval.py
git commit -m "test(integration): 跨层审批 E2E（父 step + 子 tool）"
```

---

### Task 13: 向后兼容集成测试

**Files:**
- Create: `tests/integration/test_legacy_compat.py`

- [ ] **Step 1: 写兼容性测试**

创建 `tests/integration/test_legacy_compat.py`：

```python
"""向后兼容集成测试：旧 schema 与 dev_team.py 仍可工作。"""
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agentteam.api.serializer import team_from_dict, team_to_dict
from agentteam.domain.agent import Agent
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def test_dev_team_legacy_dict_parses_to_root():
    """examples/dev_team.py 的 DEV_TEAM dict 仍可解析为 Team.root。"""
    from examples.dev_team import DEV_TEAM
    team = team_from_dict(DEV_TEAM)
    assert team.root.role == "supervisor"
    assert team.root.name == "tech_lead"
    # 4 个 worker
    assert len(team.root.children) == 4
    assert all(isinstance(c, Agent) for c in team.root.children)
    assert all(c.role == "worker" for c in team.root.children)
    # 兼容 property
    assert team.leader.name == "tech_lead"
    assert len(team.workers) == 4
    worker_names = [w.name for w in team.workers]
    assert "analyst" in worker_names
    assert "coder" in worker_names
    assert "tester" in worker_names
    assert "reviewer" in worker_names


def test_team_from_legacy_roundtrip():
    """Team.from_legacy 构造的 Team，leader/workers property 反推一致。"""
    leader = Leader(name="boss", system_prompt="你是主管")
    workers = [
        Worker(name="coder", role="代码工程师", description="",
               system_prompt="你是代码工程师"),
        Worker(name="tester", role="测试员", description="",
               system_prompt="你是测试员"),
    ]
    team = Team.from_legacy(
        name="dev", description="d", leader=leader, workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.leader.name == "boss"
    assert team.leader.system_prompt == "你是主管"
    assert [w.name for w in team.workers] == ["coder", "tester"]
    # root 也是 supervisor
    assert team.root.role == "supervisor"
    assert team.root.name == "boss"


def test_legacy_team_compiles_and_runs():
    """旧 leader+workers 构造的 Team 可编译并运行。"""
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    coder = Worker(
        name="coder", role="代码工程师", description="",
        system_prompt="你是代码工程师", model=ModelRef("qwen", "worker-model"),
    )
    team = Team(
        name="dev", description="d", leader=leader, workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    # leader LLM：拆 1 步 + 1 次 review
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="写代码"),
    ])])
    leader_llm.set_invoke_responses([AIMessage(content="ok")])
    # worker LLM
    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([AIMessage(content="print('hi')")])

    provider = FakeModelProvider({
        "leader-model": leader_llm, "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "legacy"}}
    initial = {
        "messages": [], "task": "写 hi", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": "r",
        "pending_approval": None, "total_tokens": 0, "path": "team:dev",
    }
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["coder"] == "print('hi')"


def test_legacy_serializer_roundtrip():
    """旧 schema dict → Team → team_to_dict → 重新 team_from_dict 仍工作。

    team_to_dict 输出新 schema（含 root），所以第二次 from_dict 走新路径。
    """
    from examples.dev_team import DEV_TEAM
    team1 = team_from_dict(DEV_TEAM)
    d = team_to_dict(team1)
    # d 现在是新 schema
    assert "root" in d
    team2 = team_from_dict(d)
    assert team2.root.name == team1.root.name
    assert len(team2.root.children) == len(team1.root.children)


def test_existing_e2e_tests_still_pass():
    """现有 e2e 测试套件不修改通过——通过运行 pytest 验证。"""
    import subprocess
    result = subprocess.run(
        ["pytest", "tests/integration/test_e2e_normal.py",
         "tests/integration/test_e2e_approval.py",
         "tests/integration/test_e2e_error.py", "-v"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Existing e2e tests failed:\n{result.stdout}\n{result.stderr}"
```

- [ ] **Step 2: 运行兼容性测试**

Run: `pytest tests/integration/test_legacy_compat.py -v`
Expected: 5 passed

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_legacy_compat.py
git commit -m "test(integration): 向后兼容测试（dev_team.py 与旧 schema 不动）"
```

---

### Task 14: 多级层级示例文件

**Files:**
- Create: `examples/multi_level_team.py`

- [ ] **Step 1: 创建示例**

创建 `examples/multi_level_team.py`：

```python
"""多级层级 + Team 嵌套 + 专家库示例团队定义。

验证 SP1 三大特性：
- 3 级 supervisor 链：CEO → CTO → eng
- Team 嵌套：CTO 下挂 qa_team（引用 test_subteam）
- 专家库引用：eng 用 $ref 引用 code_engineer 模板

用法:
    from examples.multi_level_team import MULTI_LEVEL_TEAM, TEST_TEAM, LIB
    # 或通过 CLI: agentteam register-team examples/multi_level_team.py
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef

# —— 专家库 ——
LIB = AgentLibrary()
LIB.register(Agent(
    name="code_engineer", role="worker",
    system_prompt="你是代码工程师，用 read_file/write_file 完成编码任务。",
    tools=["read_file", "write_file"], max_iterations=10,
))
LIB.register(Agent(
    name="tester", role="worker",
    system_prompt="你是测试员，使用 read_file/write_file 编写测试用例。",
    tools=["read_file", "write_file"], max_iterations=5,
))

# —— 子 Team：测试小队 ——
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

# —— 主 Team：3 级层级 + Team 嵌套 + 专家库引用 ——
MULTI_LEVEL_TEAM = Team(
    name="multi_level",
    description="3 级层级 + Team 嵌套 + 专家库",
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO，派活给技术副总裁 CTO。",
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

- [ ] **Step 2: 验证示例可导入**

Run: `python -c "from examples.multi_level_team import MULTI_LEVEL_TEAM, TEST_TEAM, LIB; print(MULTI_LEVEL_TEAM.root.name)"`
Expected: 输出 `ceo`

- [ ] **Step 3: 提交**

```bash
git add examples/multi_level_team.py
git commit -m "feat(examples): 新增多级层级 + Team 嵌套 + 专家库示例"
```

---

### Task 15: CLI 新增 register-team / list-teams / register-library

**Files:**
- Modify: `agentteam/cli.py`
- Test: `tests/test_cli.py` (新增用例)

- [ ] **Step 1: 写失败测试**

在 `tests/test_cli.py` 末尾追加：

```python
def test_register_team_command_calls_api(monkeypatch):
    """register-team 命令调用 POST /api/teams。"""
    import agentteam.cli as cli
    called = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"name": "t"}
    def fake_post(url, json=None, timeout=None):
        called["url"] = url
        called["json"] = json
        return FakeResp()
    monkeypatch.setattr(cli.requests, "post", fake_post)
    # 模拟 importlib 动态加载模块
    monkeypatch.setattr(cli, "_load_team_module", lambda path: {
        "name": "t", "description": "d",
        "root": {"name": "lead", "role": "supervisor", "children": []},
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    })
    rc = cli.main(["register-team", "some_file.py", "--api", "http://test"])
    assert rc == 0
    assert called["url"] == "http://test/api/teams"


def test_list_teams_command_calls_api(monkeypatch):
    """list-teams 命令调用 GET /api/teams。"""
    import agentteam.cli as cli
    class FakeResp:
        status_code = 200
        def json(self): return [{"name": "t1"}, {"name": "t2"}]
    def fake_get(url, timeout=None):
        return FakeResp()
    monkeypatch.setattr(cli.requests, "get", fake_get)
    rc = cli.main(["list-teams", "--api", "http://test"])
    assert rc == 0


def test_register_library_command_calls_api(monkeypatch):
    """register-library 命令调用 POST /api/library/agents。"""
    import agentteam.cli as cli
    called = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"ok": True}
    def fake_post(url, json=None, timeout=None):
        called["url"] = url
        called["json"] = json
        return FakeResp()
    monkeypatch.setattr(cli.requests, "post", fake_post)
    monkeypatch.setattr(cli, "_load_library_module", lambda path: [
        {"name": "coder", "role": "worker", "system_prompt": "code"},
    ])
    rc = cli.main(["register-library", "lib.py", "--api", "http://test"])
    assert rc == 0
    assert called["url"] == "http://test/api/library/agents"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_cli.py -v -k "register_team or list_teams or register_library"`
Expected: FAIL（CLI 未实现这些命令）

- [ ] **Step 3: 实现 CLI 命令**

替换 `agentteam/cli.py`：

```python
"""AgentTeam CLI 入口。

命令:
    agentteam register-dev-team [--api URL]      注册研发小队到 API 服务
    agentteam register-team FILE [--api URL]     注册任意 Team 配置文件
    agentteam list-teams [--api URL]             列出已注册团队
    agentteam register-library FILE [--api URL]  注册专家库
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import requests

from examples.dev_team import DEV_TEAM


def _load_team_module(path: str) -> dict:
    """从 Python 文件加载 Team dict 配置。

    文件应定义 MODULE_LEVEL_TEAM 或 MULTI_LEVEL_TEAM 或 DEV_TEAM 变量，
    或第一个 Team/MULTI_LEVEL_TEAM 字典变量。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Team config file not found: {path}")
    spec = importlib.util.spec_from_file_location("team_module", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 优先级：MULTI_LEVEL_TEAM > MODULE_LEVEL_TEAM > TEAM > DEV_TEAM
    for name in ("MULTI_LEVEL_TEAM", "MODULE_LEVEL_TEAM", "TEAM", "DEV_TEAM"):
        if hasattr(mod, name):
            from agentteam.api.serializer import team_to_dict
            from agentteam.domain.team import Team
            val = getattr(mod, name)
            if isinstance(val, dict):
                return val
            if isinstance(val, Team):
                return team_to_dict(val)
    raise AttributeError(f"No team variable found in {path}")


def _load_library_module(path: str) -> list[dict]:
    """从 Python 文件加载 AgentLibrary 中的 agents 列表。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Library file not found: {path}")
    spec = importlib.util.spec_from_file_location("lib_module", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "LIB"):
        raise AttributeError(f"No LIB variable found in {path}")
    lib = getattr(mod, "LIB")
    from dataclasses import asdict
    from agentteam.domain.agent import Agent
    result = []
    for agent in lib.agents.values():
        d = {
            "name": agent.name, "role": agent.role,
            "system_prompt": agent.system_prompt,
            "tools": list(agent.tools),
            "max_iterations": agent.max_iterations,
            "children": [], "ref": None,
            "model": asdict(agent.model) if agent.model else None,
            "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        }
        result.append(d)
    return result


def register_dev_team(api: str = "http://localhost:8000") -> int:
    try:
        resp = requests.post(f"{api}/api/teams", json=DEV_TEAM, timeout=10)
        if resp.status_code < 400:
            data = resp.json() if resp.text else {}
            print(f"已注册团队: {data.get('name', 'dev_team')}")
            return 0
        err = resp.json() if resp.text else {}
        print(f"错误: {err.get('detail', resp.text)}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def register_team(file_path: str, api: str = "http://localhost:8000") -> int:
    try:
        team_dict = _load_team_module(file_path)
    except Exception as e:
        print(f"错误: 加载配置文件失败: {e}")
        return 1
    try:
        resp = requests.post(f"{api}/api/teams", json=team_dict, timeout=10)
        if resp.status_code < 400:
            data = resp.json() if resp.text else {}
            print(f"已注册团队: {data.get('name', 'unknown')}")
            return 0
        err = resp.json() if resp.text else {}
        print(f"错误: {err.get('detail', resp.text)}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def list_teams(api: str = "http://localhost:8000") -> int:
    try:
        resp = requests.get(f"{api}/api/teams", timeout=10)
        if resp.status_code < 400:
            teams = resp.json() if resp.text else []
            if not teams:
                print("(空)")
                return 0
            for t in teams:
                name = t.get("name", "?")
                desc = t.get("description", "")
                print(f"  {name}  {desc}")
            return 0
        err = resp.json() if resp.text else {}
        print(f"错误: {err.get('detail', resp.text)}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def register_library(file_path: str, api: str = "http://localhost:8000") -> int:
    try:
        agents = _load_library_module(file_path)
    except Exception as e:
        print(f"错误: 加载库文件失败: {e}")
        return 1
    try:
        for agent in agents:
            resp = requests.post(f"{api}/api/library/agents", json=agent, timeout=10)
            if resp.status_code >= 400:
                err = resp.json() if resp.text else {}
                print(f"错误: 注册 {agent.get('name')} 失败: {err.get('detail', resp.text)}")
                return 1
        print(f"已注册 {len(agents)} 个专家 Agent")
        return 0
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentteam", description="AgentTeam CLI")
    sub = parser.add_subparsers(dest="command")

    p_dev = sub.add_parser("register-dev-team", help="注册研发小队到 API")
    p_dev.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_team = sub.add_parser("register-team", help="注册任意 Team 配置文件")
    p_team.add_argument("file", help="Team 配置文件路径（.py）")
    p_team.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_list = sub.add_parser("list-teams", help="列出已注册团队")
    p_list.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_lib = sub.add_parser("register-library", help="注册专家库")
    p_lib.add_argument("file", help="库文件路径（.py，需定义 LIB 变量）")
    p_lib.add_argument("--api", default="http://localhost:8000", help="API 地址")

    args = parser.parse_args(argv)

    if args.command == "register-dev-team":
        return register_dev_team(args.api)
    elif args.command == "register-team":
        return register_team(args.file, args.api)
    elif args.command == "list-teams":
        return list_teams(args.api)
    elif args.command == "register-library":
        return register_library(args.file, args.api)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 运行 CLI 测试验证**

Run: `pytest tests/test_cli.py -v`
Expected: 全部 pass（含新增 3 个 + 既有用例）

- [ ] **Step 5: 提交**

```bash
git add agentteam/cli.py tests/test_cli.py
git commit -m "feat(cli): 新增 register-team / list-teams / register-library 命令"
```

---

### Task 16: 添加 library API 端点

**Files:**
- Modify: `agentteam/api/routes/teams.py` (或新增 library.py)
- Modify: `agentteam/api/server.py`

注意：CLI `register-library` 调用 `POST /api/library/agents`，需新增此端点。

- [ ] **Step 1: 读现有 teams.py**

Run: `cat agentteam/api/routes/teams.py`

了解现有 teams 路由结构。

- [ ] **Step 2: 新增 library 路由**

创建 `agentteam/api/routes/library.py`：

```python
"""GET/POST /api/library/agents 端点：专家 Agent 库管理。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


class AgentDict(BaseModel):
    name: str
    role: str
    system_prompt: str = ""
    tools: list[str] = []
    max_iterations: int = 10
    model: dict | None = None
    approval_policy: dict | None = None


def library_router(library: AgentLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/library", tags=["library"])

    @router.get("/agents")
    def list_agents():
        return [
            {"name": a.name, "role": a.role, "system_prompt": a.system_prompt,
             "tools": list(a.tools), "max_iterations": a.max_iterations}
            for a in library.agents.values()
        ]

    @router.post("/agents")
    def register_agent(agent: AgentDict):
        from agentteam.domain.approval import ApprovalPolicy
        from agentteam.models.provider import ModelRef
        existing = library.get(agent.name)
        if existing is not None:
            raise HTTPException(status_code=400, detail=f"Agent already exists: {agent.name}")
        model = None
        if agent.model:
            model = ModelRef(
                provider=agent.model["provider"],
                name=agent.model["name"],
                temperature=agent.model.get("temperature", 0.7),
                streaming=agent.model.get("streaming", True),
            )
        ap = None
        if agent.approval_policy:
            ap = ApprovalPolicy(
                level=agent.approval_policy["level"],
                targets=agent.approval_policy.get("targets"),
                timeout_seconds=agent.approval_policy.get("timeout_seconds"),
            )
        a = Agent(
            name=agent.name, role=agent.role,
            system_prompt=agent.system_prompt,
            tools=list(agent.tools), max_iterations=agent.max_iterations,
            model=model, approval_policy=ap,
        )
        library.register(a)
        return {"name": a.name}

    return router
```

- [ ] **Step 3: 在 server.py 注册 library_router**

修改 `agentteam/api/server.py`：

```python
from agentteam.api.routes.library import library_router
# ...
    app.include_router(library_router(lib))
```

- [ ] **Step 4: 写 library 端点测试**

创建 `tests/api/test_api_library.py`：

```python
"""Library API 端点测试。"""
from fastapi.testclient import TestClient

from agentteam.api.server import create_app
from agentteam.domain.library import AgentLibrary


def test_register_and_list_agents():
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    # 注册一个 agent
    resp = client.post("/api/library/agents", json={
        "name": "coder", "role": "worker",
        "system_prompt": "code", "tools": ["read_file"], "max_iterations": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "coder"

    # 列表
    resp = client.get("/api/library/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert len(agents) == 1
    assert agents[0]["name"] == "coder"


def test_register_duplicate_agent_400():
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    client.post("/api/library/agents", json={"name": "x", "role": "worker"})
    resp = client.post("/api/library/agents", json={"name": "x", "role": "worker"})
    assert resp.status_code == 400
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/api/test_api_library.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add agentteam/api/routes/library.py agentteam/api/server.py tests/api/test_api_library.py
git commit -m "feat(api): 新增 /api/library/agents 端点管理专家库"
```

---

### Task 17: 全套测试验证

- [ ] **Step 1: 运行全套测试**

Run: `pytest -v`
Expected: 全部 pass（200+ 个测试，含新增约 40 个）

- [ ] **Step 2: 检查覆盖率**

Run: `pytest --cov=agentteam --cov-report=term-missing`
Expected: domain/ 与 runtime/graph.py 覆盖率不下降

- [ ] **Step 3: 跑 dev_team 端到端**

```bash
# 启动 API
uvicorn agentteam.api.server:create_app --factory &
# 注册 dev_team
agentteam register-dev-team
# 提交任务
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "dev_team", "task": "实现 hello world"}'
```
Expected: 返回 `{"run_id": "..."}`

- [ ] **Step 4: 跑 multi_level_team 端到端**

```bash
agentteam register-library examples/multi_level_team.py
agentteam register-team examples/multi_level_team.py
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "multi_level", "task": "开发 hello world"}'
```
Expected: 返回 `{"run_id": "..."}`

- [ ] **Step 5: 最终提交（如有修复）**

```bash
git add -A
git commit -m "test: SP1 全套测试通过，无回归"
```

---

## Self-Review

### 1. Spec coverage

| Spec 章节 | 实现 Task |
|---|---|
| §2.1 Agent + TeamRef dataclass | Task 1 |
| §2.2 Team 重构（root + from_legacy + property） | Task 4 |
| §2.3 Leader/Worker to_agent | Task 2, 3 |
| §2.4 AgentLibrary | Task 5 |
| §2.5 编译期校验规则 | Task 9（_validate 方法） |
| §3.1-3.2 TeamCompiler 递归编译 | Task 9 |
| §3.3 路由函数调整 | Task 9（make_route_from_plan 等） |
| §3.4 节点工厂接受 Agent | Task 8 |
| §4.1 TeamState path 字段 | Task 7 |
| §5 审批策略（各层独立） | Task 9（编译器保留 step_gate/worker_gate）+ Task 12（测试） |
| §6.1 dev_team.py 不动 | Task 13（兼容测试） |
| §6.2 serializer 双轨 | Task 6 |
| §6.3 TeamStore 不动 | （无变更） |
| §6.4 RunManager 不动 | （无变更） |
| §6.5 routes/runs.py 微调 | Task 10 |
| §7.1 dev_team.py 不动 | Task 13 |
| §7.2 multi_level_team.py 示例 | Task 14 |
| §7.3 CLI 命令 | Task 15 |
| §8 错误处理 | Task 9（编译期校验） |
| §9 测试策略 | Task 1, 5, 9, 11, 12, 13 |
| §10 4 个 commit | Commit 1 (Task 1-5), Commit 2 (Task 6), Commit 3 (Task 7-9), Commit 4 (Task 10-17) |

### 2. Placeholder scan

- 无 "TBD" / "TODO" / "fill in details"
- 每个步骤含完整代码或确切命令
- 每个测试用例含完整 assert

### 3. Type consistency

- `Agent` / `TeamRef` / `AgentLibrary` / `Team` 类型在所有 Task 中一致
- `make_leader_plan_node(agent: Agent, ...)` 在 Task 8 定义，Task 9 调用一致
- `TeamCompiler.__init__(model_provider, tool_registry, library=None)` 在 Task 9 定义，Task 10 调用一致
- `Team.from_legacy` 关键字参数在 Task 4 定义，Task 6 调用一致
- `team_to_dict` / `team_from_dict` 在 Task 6 定义，Task 13 调用一致

### 4. 已知边界情况

- Task 9 step 6 的 `node_name = f"worker_{name}"` vs `f"agent_{name}"` 兼容性处理已说明
- Task 8 节点工厂类型注解变更但 Leader/Worker 同名字段保证兼容
- Task 13 `test_existing_e2e_tests_still_pass` 通过子进程跑既有 e2e 测试做集成验证

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-18-sp1-agent-hierarchy-core.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
