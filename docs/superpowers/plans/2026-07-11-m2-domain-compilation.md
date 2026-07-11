# AgentTeam M2: 领域与编译 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 AgentTeam 的领域层（Team/Worker/Leader/ApprovalPolicy）与执行内核（TeamCompiler 把 Team 编译成 LangGraph StateGraph，含 leader_plan / worker ReAct / leader_review 节点），用 mock LLM 跑通端到端 run。

**Architecture:** 领域层用 dataclass 描述配置（概念与执行分离）；runtime 层用 LangGraph StateGraph 编排——leader_plan 用结构化输出拆任务、worker 节点内部跑 ReAct 循环、leader_review 推进步骤。节点是闭包工厂，编译时绑定 LLM 与工具，运行时只读写 TeamState。M2 不接 interrupt 审批门和 DB 轨迹落库（留 M3），但 state 预留 audit_events 字段。

**Tech Stack:** Python ≥3.10、langgraph（StateGraph/add_messages/MemorySaver）、langchain-core（messages/tools/BaseChatModel）、pydantic（结构化输出 schema）、pytest。

**Spec reference:** `docs/superpowers/specs/2026-07-11-agent-team-design.md`（§3 领域模型、§4 编译执行、§12 项目结构）

**前置条件：** M1 已完成（main 分支），langgraph 1.1.3 已安装。

**后续计划：** M3 审批与轨迹（interrupt 审批门 + AuditEvent 落库 + 断点续跑）、M4 MCP、M5 API+Web UI、M6 示例团队。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `agentteam/domain/__init__.py` | 领域层包，导出 Team/Worker/Leader/ApprovalPolicy |
| `agentteam/domain/approval.py` | `ApprovalPolicy`（frozen dataclass，三种粒度） |
| `agentteam/domain/worker.py` | `Worker`（角色说明书） |
| `agentteam/domain/team.py` | `Leader` + `Team` |
| `agentteam/runtime/__init__.py` | runtime 包 |
| `agentteam/runtime/state.py` | `Step`/`TeamState` TypedDict + `merge_dicts` reducer |
| `agentteam/runtime/nodes.py` | `PlanStep`/`Plan` schema + 三个节点工厂函数 |
| `agentteam/runtime/graph.py` | `route_from_plan`/`route_from_review` + `TeamCompiler` |
| `tests/conftest.py` | 追加 `FakeLLM` / `FakeModelProvider` / `fake_llm` fixture |
| `tests/domain/__init__.py` | 测试包 |
| `tests/domain/test_approval.py` | ApprovalPolicy 测试 |
| `tests/domain/test_worker.py` | Worker 测试 |
| `tests/domain/test_team.py` | Leader + Team 测试 |
| `tests/runtime/__init__.py` | 测试包 |
| `tests/runtime/test_state.py` | state reducer 测试 |
| `tests/runtime/test_nodes.py` | 三个节点单元测试 |
| `tests/runtime/test_graph.py` | TeamCompiler + 端到端集成测试 |

**设计决策（偏离 spec 的地方，已审定）：**
- `Worker.model` / `Leader.model` 为 `ModelRef | None = None`，None 时回退到 `team.default_model`——使 default_model 字段有实际用途，且配置更灵活。spec 写的是必填 ModelRef，这里做实用化改进。
- `Team.mcp_servers` 省略（M4 再加，YAGNI）。
- `leader_review` 节点用 LLM 生成点评消息，但路由是确定性的（按 current_step 推进），不靠 LLM 决定下一步——保证可测试。
- `audit_events` 在 state 里用 `operator.add` 累积，M2 只在内存累积简单 dict，M3 接 AuditRepo 落库。
- `pending_approval` 字段 M2 不加（M3 interrupt 时引入）。

---

## Task 1: ApprovalPolicy 领域模型

**Files:**
- Create: `agentteam/domain/approval.py`
- Create: `tests/domain/__init__.py`（空文件）
- Test: `tests/domain/test_approval.py`

- [ ] **Step 1: 写失败测试 `tests/domain/test_approval.py`**

```python
import pytest

from agentteam.domain.approval import ApprovalPolicy


def test_worker_level_policy():
    p = ApprovalPolicy(level="worker", targets=["coder"])
    assert p.level == "worker"
    assert p.targets == ["coder"]
    assert p.timeout_seconds is None


def test_step_level_policy_defaults():
    p = ApprovalPolicy(level="step")
    assert p.targets is None
    assert p.timeout_seconds is None


def test_tool_level_policy_with_timeout():
    p = ApprovalPolicy(level="tool", targets=["write_file"], timeout_seconds=300)
    assert p.level == "tool"
    assert p.timeout_seconds == 300


def test_approval_policy_is_frozen():
    p = ApprovalPolicy(level="tool", targets=["write_file"])
    with pytest.raises(Exception):
        p.level = "worker"  # type: ignore[misc]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/domain/test_approval.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.domain.approval`）

- [ ] **Step 3: 写实现 `agentteam/domain/approval.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ApprovalPolicy:
    """声明式审批策略，三种粒度。M2 仅定义数据结构，M3 接入 interrupt。"""

    level: Literal["worker", "tool", "step"]
    targets: list[str] | None = None
    timeout_seconds: int | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/domain/test_approval.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/domain/__init__.py agentteam/domain/approval.py tests/domain/__init__.py tests/domain/test_approval.py
git commit -m "feat(domain): add ApprovalPolicy dataclass"
```

> **注意：** `agentteam/domain/__init__.py` 在本 Task 创建为空文件，Task 3 会填入导出。

---

## Task 2: Worker 领域模型

**Files:**
- Create: `agentteam/domain/worker.py`
- Test: `tests/domain/test_worker.py`

- [ ] **Step 1: 写失败测试 `tests/domain/test_worker.py`**

```python
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def test_worker_defaults():
    w = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
    )
    assert w.model is None
    assert w.tools == []
    assert w.approval_policy is None
    assert w.max_iterations == 10


def test_worker_with_all_fields():
    w = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        model=ModelRef("qwen", "qwen-max"),
        tools=["read_file", "write_file"],
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        max_iterations=5,
    )
    assert w.model == ModelRef("qwen", "qwen-max")
    assert w.tools == ["read_file", "write_file"]
    assert w.approval_policy.level == "tool"
    assert w.max_iterations == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/domain/test_worker.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.domain.worker`）

- [ ] **Step 3: 写实现 `agentteam/domain/worker.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from agentteam.domain.approval import ApprovalPolicy
from agentteam.models.provider import ModelRef


@dataclass
class Worker:
    """角色说明书：定义一个 Worker 的职责、模型、工具与审批策略。"""

    name: str
    role: str
    description: str
    system_prompt: str
    model: ModelRef | None = None
    tools: list[str] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None
    max_iterations: int = 10
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/domain/test_worker.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/domain/worker.py tests/domain/test_worker.py
git commit -m "feat(domain): add Worker dataclass"
```

---

## Task 3: Leader + Team 领域模型

**Files:**
- Create: `agentteam/domain/team.py`
- Modify: `agentteam/domain/__init__.py`（填入导出）
- Test: `tests/domain/test_team.py`

- [ ] **Step 1: 写失败测试 `tests/domain/test_team.py`**

```python
from agentteam.domain import ApprovalPolicy, Leader, Team, Worker
from agentteam.models.provider import ModelRef


def test_leader_defaults():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    assert leader.name == "leader"
    assert leader.role == "主管"
    assert leader.approval_policy is None


def test_team_construction():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    coder = Worker(name="coder", role="代码工程师", description="写代码", system_prompt="你是代码工程师")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[coder],
        default_model=ModelRef("qwen", "qwen-max"),
    )
    assert team.name == "dev"
    assert team.leader is leader
    assert len(team.workers) == 1
    assert team.skills == []


def test_team_with_skills():
    leader = Leader(system_prompt="你是主管")
    team = Team(
        name="dev",
        description="开发小队",
        leader=leader,
        workers=[],
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file"],
    )
    assert team.skills == ["read_file", "write_file"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/domain/test_team.py -v`
Expected: FAIL（`ImportError: cannot import name 'Leader'`）

- [ ] **Step 3: 写实现 `agentteam/domain/team.py`**

```python
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
```

- [ ] **Step 4: 写 `agentteam/domain/__init__.py` 导出**

```python
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker

__all__ = ["ApprovalPolicy", "Leader", "Team", "Worker"]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/domain/ -v`
Expected: PASS（全部通过，含之前的 approval + worker 测试）

- [ ] **Step 6: Commit**

```bash
git add agentteam/domain/team.py agentteam/domain/__init__.py tests/domain/test_team.py
git commit -m "feat(domain): add Leader and Team dataclasses"
```

---

## Task 4: State schema（Step / TeamState）

**Files:**
- Create: `agentteam/runtime/__init__.py`（空文件）
- Create: `agentteam/runtime/state.py`
- Create: `tests/runtime/__init__.py`（空文件）
- Test: `tests/runtime/test_state.py`

- [ ] **Step 1: 写失败测试 `tests/runtime/test_state.py`**

```python
from agentteam.runtime.state import TeamState, Step, merge_dicts


def test_merge_dicts_disjoint():
    assert merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}


def test_merge_dicts_right_wins():
    assert merge_dicts({"a": "1"}, {"a": "2"}) == {"a": "2"}


def test_merge_dicts_empty():
    assert merge_dicts({}, {"a": "1"}) == {"a": "1"}
    assert merge_dicts({"a": "1"}, {}) == {"a": "1"}


def test_step_typeddict_accepts_fields():
    step: Step = {"worker": "coder", "instruction": "写代码", "status": "pending"}
    assert step["worker"] == "coder"
    assert step["status"] == "pending"


def test_team_state_typeddict_accepts_fields():
    state: TeamState = {
        "messages": [],
        "task": "开发 hello world",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
    }
    assert state["task"] == "开发 hello world"
    assert state["current_step"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/runtime/test_state.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.runtime.state`）

- [ ] **Step 3: 写实现 `agentteam/runtime/state.py`**

```python
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


def merge_dicts(left: dict, right: dict) -> dict:
    """合并两个 dict，right 覆盖 left 的同名键。"""
    return {**left, **right}


class Step(TypedDict):
    """计划中的一步。"""
    worker: str
    instruction: str
    status: str  # pending | running | done | failed


class TeamState(TypedDict):
    """Team 执行图的全局状态。"""
    messages: Annotated[list, add_messages]
    task: str
    plan: list[Step]
    current_step: int
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    audit_events: Annotated[list, operator.add]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/runtime/test_state.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/__init__.py agentteam/runtime/state.py tests/runtime/__init__.py tests/runtime/test_state.py
git commit -m "feat(runtime): add TeamState schema with reducers"
```

---

## Task 5: FakeLLM fixture + leader_plan 节点

**Files:**
- Modify: `tests/conftest.py`（追加 FakeLLM / FakeModelProvider / fake_llm fixture）
- Create: `agentteam/runtime/nodes.py`（PlanStep / Plan / make_leader_plan_node）
- Test: `tests/runtime/test_nodes.py`

- [ ] **Step 1: 追加 FakeLLM 到 `tests/conftest.py`**

在现有 `tests/conftest.py` 末尾追加（保留已有的 `tmp_db` fixture 不动）：

```python
class FakeLLM:
    """测试用假 LLM。

    invoke() 按顺序返回 invoke_responses 中的元素；
    with_structured_output().invoke() 按顺序返回 structured_responses 中的元素。
    """

    def __init__(self) -> None:
        self.invoke_responses: list = []
        self.structured_responses: list = []
        self._inv_idx = 0
        self._struct_idx = 0

    def set_invoke_responses(self, responses: list) -> None:
        self.invoke_responses = list(responses)
        self._inv_idx = 0

    def set_structured_responses(self, responses: list) -> None:
        self.structured_responses = list(responses)
        self._struct_idx = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class _Structured:
            def invoke(self, messages, **kw):
                r = parent.structured_responses[parent._struct_idx]
                parent._struct_idx += 1
                return r

        return _Structured()

    def invoke(self, messages, **kwargs):
        r = self.invoke_responses[self._inv_idx]
        self._inv_idx += 1
        return r


class FakeModelProvider:
    """测试用模型提供者，按 model name 映射到不同 FakeLLM。"""

    def __init__(self, llm_by_model_name: dict[str, FakeLLM]) -> None:
        self._map = llm_by_model_name

    def get_llm(self, ref):
        return self._map[ref.name]


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
```

- [ ] **Step 2: 写失败测试 `tests/runtime/test_nodes.py`（leader_plan 部分）**

```python
from agentteam.domain.team import Leader
from agentteam.models.provider import ModelRef
from agentteam.runtime.nodes import Plan, PlanStep, make_leader_plan_node


def test_leader_plan_produces_plan(fake_llm):
    plan = Plan(steps=[
        PlanStep(worker="coder", instruction="写代码"),
        PlanStep(worker="tester", instruction="写测试"),
    ])
    fake_llm.set_structured_responses([plan])

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    node = make_leader_plan_node(leader, fake_llm)

    state = {"task": "开发 hello world", "messages": []}
    result = node(state)

    assert len(result["plan"]) == 2
    assert result["plan"][0]["worker"] == "coder"
    assert result["plan"][0]["instruction"] == "写代码"
    assert result["plan"][0]["status"] == "pending"
    assert result["current_step"] == 0
    assert len(result["messages"]) == 1
    assert len(result["audit_events"]) == 1


def test_leader_plan_empty_plan(fake_llm):
    fake_llm.set_structured_responses([Plan(steps=[])])

    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    result = node({"task": "啥也不用做", "messages": []})
    assert result["plan"] == []
    assert result["current_step"] == 0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/runtime/test_nodes.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.runtime.nodes`）

- [ ] **Step 4: 写实现 `agentteam/runtime/nodes.py`（leader_plan 部分）**

```python
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agentteam.domain.team import Leader
from agentteam.runtime.state import TeamState


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。"""

    worker: str = Field(description="执行此步的 worker name")
    instruction: str = Field(description="子任务描述")


class Plan(BaseModel):
    """Leader 拆解出的执行计划。"""

    steps: list[PlanStep] = Field(description="按顺序执行的步骤列表")


def make_leader_plan_node(leader: Leader, llm: BaseChatModel):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。"""

    def leader_plan(state: TeamState) -> dict:
        task = state["task"]
        messages = [
            SystemMessage(content=leader.system_prompt),
            HumanMessage(
                content=f"请把以下任务拆解成可执行的步骤计划，每步指派一个 worker：\n\n{task}"
            ),
        ]
        structured = llm.with_structured_output(Plan)
        plan_obj = structured.invoke(messages)
        plan = [
            {"worker": s.worker, "instruction": s.instruction, "status": "pending"}
            for s in plan_obj.steps
        ]
        return {
            "plan": plan,
            "current_step": 0,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": leader.name}],
        }

    return leader_plan
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/runtime/test_nodes.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add leader_plan node with FakeLLM test fixture"
```

---

## Task 6: worker ReAct 节点

**Files:**
- Modify: `agentteam/runtime/nodes.py`（追加 make_worker_node）
- Modify: `tests/runtime/test_nodes.py`（追加 worker 测试）

- [ ] **Step 1: 追加失败测试到 `tests/runtime/test_nodes.py`**

在文件末尾追加：

```python
from langchain_core.messages import AIMessage

from agentteam.domain.worker import Worker
from agentteam.runtime.nodes import make_worker_node


def test_worker_node_direct_answer(fake_llm):
    fake_llm.set_invoke_responses([AIMessage(content="hello world")])

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
    )
    node = make_worker_node(worker, fake_llm, [])

    state = {
        "plan": [{"worker": "coder", "instruction": "写 hello", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    assert result["worker_outputs"] == {"coder": "hello world"}
    assert len(result["messages"]) == 1
    assert "coder" in result["messages"][0].content


def test_worker_node_react_with_tool(fake_llm, tmp_path):
    from agentteam.tools.skills.file_ops import write_file

    target = tmp_path / "out.txt"
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "write_file",
                "args": {"path": str(target), "content": "hi"},
                "id": "tc1",
            }],
        ),
        AIMessage(content="已写入文件"),
    ])

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        tools=["write_file"],
    )
    node = make_worker_node(worker, fake_llm, [write_file])

    state = {
        "plan": [{"worker": "coder", "instruction": "写文件", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    assert target.read_text(encoding="utf-8") == "hi"
    assert result["worker_outputs"] == {"coder": "已写入文件"}


def test_worker_node_respects_max_iterations(fake_llm):
    # LLM 始终返回 tool_call，永不给最终答案
    fake_llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "read_file",
            "args": {"path": "x"},
            "id": f"tc{i}",
        }]) for i in range(3)
    ])

    from agentteam.tools.skills.file_ops import read_file

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        tools=["read_file"],
        max_iterations=3,
    )
    node = make_worker_node(worker, fake_llm, [read_file])

    state = {
        "plan": [{"worker": "coder", "instruction": "读文件", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    # 达到 max_iterations 后强制结束，worker_outputs 仍有值
    assert "coder" in result["worker_outputs"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/runtime/test_nodes.py::test_worker_node_direct_answer -v`
Expected: FAIL（`ImportError: cannot import name 'make_worker_node'`）

- [ ] **Step 3: 追加实现到 `agentteam/runtime/nodes.py`**

在文件末尾追加（并在文件顶部 import 区追加 `ToolMessage` 和 `BaseTool`、`Worker`）：

顶部 import 追加：
```python
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from agentteam.domain.worker import Worker
```

文件末尾追加：
```python
def make_worker_node(worker: Worker, llm: BaseChatModel, tools: list[BaseTool]):
    """创建 worker 节点：内部跑 ReAct 循环（LLM 调工具直到给出最终答案）。"""

    def worker_react(state: TeamState) -> dict:
        step = state["plan"][state["current_step"]]
        instruction = step["instruction"]
        tool_map = {t.name: t for t in tools}
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        messages = [
            SystemMessage(content=worker.system_prompt),
            HumanMessage(content=instruction),
        ]
        final_answer = ""
        for _ in range(worker.max_iterations):
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                final_answer = response.content
                break
            for tc in tool_calls:
                tool = tool_map.get(tc["name"])
                if tool is None:
                    result = f"工具 {tc['name']} 不存在"
                else:
                    try:
                        result = tool.invoke(tc["args"])
                    except Exception as e:
                        result = f"工具执行出错：{type(e).__name__}: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        else:
            final_answer = getattr(messages[-1], "content", "") if messages else ""
        return {
            "worker_outputs": {worker.name: final_answer},
            "messages": [
                AIMessage(content=f"[{worker.name}] {final_answer}", name=worker.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": worker.name}],
        }

    return worker_react
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/runtime/test_nodes.py -v`
Expected: PASS（全部通过，含之前 leader_plan 2 个 + worker 3 个）

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add worker ReAct node"
```

---

## Task 7: leader_review 节点

**Files:**
- Modify: `agentteam/runtime/nodes.py`（追加 make_leader_review_node）
- Modify: `tests/runtime/test_nodes.py`（追加 review 测试）

- [ ] **Step 1: 追加失败测试到 `tests/runtime/test_nodes.py`**

在文件末尾追加：

```python
from agentteam.runtime.nodes import make_leader_review_node


def test_leader_review_marks_step_done_and_advances(fake_llm):
    fake_llm.set_invoke_responses([AIMessage(content="做得好")])

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    node = make_leader_review_node(leader, fake_llm)

    state = {
        "task": "开发",
        "messages": [],
        "plan": [
            {"worker": "coder", "instruction": "写代码", "status": "running"},
            {"worker": "tester", "instruction": "写测试", "status": "pending"},
        ],
        "current_step": 0,
        "worker_outputs": {"coder": "print('hi')"},
        "audit_events": [],
    }
    result = node(state)

    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "pending"
    assert result["current_step"] == 1
    assert len(result["messages"]) == 1
    assert len(result["audit_events"]) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/runtime/test_nodes.py::test_leader_review_marks_step_done_and_advances -v`
Expected: FAIL（`ImportError: cannot import name 'make_leader_review_node'`）

- [ ] **Step 3: 追加实现到 `agentteam/runtime/nodes.py`**

在文件末尾追加：

```python
def make_leader_review_node(leader: Leader, llm: BaseChatModel):
    """创建 leader_review 节点：点评 worker 产出，标记步骤完成，推进 current_step。"""

    def leader_review(state: TeamState) -> dict:
        current = state["current_step"]
        plan = list(state["plan"])
        plan[current] = {**plan[current], "status": "done"}
        worker_name = plan[current]["worker"]
        outputs = state.get("worker_outputs", {})
        review_response = llm.invoke([
            SystemMessage(content=leader.system_prompt),
            HumanMessage(
                content=(
                    f"Worker {worker_name} 完成了步骤 {current}，"
                    f"产出：{outputs.get(worker_name, '')}。请简要点评。"
                )
            ),
        ])
        return {
            "plan": plan,
            "current_step": current + 1,
            "messages": [
                AIMessage(content=f"[Leader] {review_response.content}", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_review", "actor": leader.name}],
        }

    return leader_review
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/runtime/test_nodes.py -v`
Expected: PASS（全部通过，含 leader_plan 2 + worker 3 + review 1）

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): add leader_review node"
```

---

## Task 8: TeamCompiler + 路由函数

**Files:**
- Create: `agentteam/runtime/graph.py`
- Test: `tests/runtime/test_graph.py`（编译结构测试）

- [ ] **Step 1: 写失败测试 `tests/runtime/test_graph.py`（编译结构部分）**

```python
from langgraph.graph import END

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler, route_from_plan, route_from_review
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _make_team():
    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "leader-model"))
    coder = Worker(
        name="coder", role="代码工程师", description="写代码",
        system_prompt="你是代码工程师", model=ModelRef("qwen", "worker-model"),
    )
    tester = Worker(
        name="tester", role="测试员", description="写测试",
        system_prompt="你是测试员", model=ModelRef("qwen", "worker-model"),
    )
    return Team(
        name="dev", description="开发小队", leader=leader,
        workers=[coder, tester], default_model=ModelRef("qwen", "qwen-max"),
    )


def test_route_from_plan_returns_first_worker():
    state = {"plan": [{"worker": "coder", "instruction": "x", "status": "pending"}]}
    assert route_from_plan(state) == "worker_coder"


def test_route_from_plan_empty_plan_ends():
    assert route_from_plan({"plan": []}) == END


def test_route_from_review_continues():
    state = {
        "plan": [
            {"worker": "coder", "instruction": "x", "status": "done"},
            {"worker": "tester", "instruction": "y", "status": "pending"},
        ],
        "current_step": 1,
    }
    assert route_from_review(state) == "worker_tester"


def test_route_from_review_ends_when_done():
    state = {
        "plan": [{"worker": "coder", "instruction": "x", "status": "done"}],
        "current_step": 1,
    }
    assert route_from_review(state) == END


def test_team_compiler_produces_runnable_graph():
    team = _make_team()
    leader_llm = FakeLLM()
    worker_llm = FakeLLM()
    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team)

    # 编译产物应有 nodes 属性
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    assert "worker_coder" in node_names
    assert "worker_tester" in node_names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.runtime.graph`）

- [ ] **Step 3: 写实现 `agentteam/runtime/graph.py`**

```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState
from agentteam.tools.registry import ToolRegistry


def route_from_plan(state: TeamState) -> str:
    """leader_plan 之后，路由到第一步的 worker；空计划直接结束。"""
    plan = state.get("plan", [])
    if not plan:
        return END
    return f"worker_{plan[0]['worker']}"


def route_from_review(state: TeamState) -> str:
    """leader_review 之后，若还有步骤路由到下一个 worker，否则结束。"""
    current = state.get("current_step", 0)
    plan = state.get("plan", [])
    if current >= len(plan):
        return END
    return f"worker_{plan[current]['worker']}"


class TeamCompiler:
    """把 Team 配置编译成可执行的 LangGraph StateGraph。"""

    def __init__(self, model_provider: ModelProvider, tool_registry: ToolRegistry):
        self._mp = model_provider
        self._tr = tool_registry

    def compile(self, team: Team, checkpointer=None):
        graph = StateGraph(TeamState)

        leader_model = team.leader.model or team.default_model
        leader_llm = self._mp.get_llm(leader_model)
        graph.add_node("leader_plan", make_leader_plan_node(team.leader, leader_llm))
        graph.add_node("leader_review", make_leader_review_node(team.leader, leader_llm))

        for worker in team.workers:
            worker_model = worker.model or team.default_model
            llm = self._mp.get_llm(worker_model)
            tools = self._tr.get_tools(worker.tools) if worker.tools else []
            graph.add_node(
                f"worker_{worker.name}", make_worker_node(worker, llm, tools)
            )

        graph.add_edge(START, "leader_plan")

        worker_targets = {f"worker_{w.name}": f"worker_{w.name}" for w in team.workers}
        worker_targets[END] = END
        graph.add_conditional_edges("leader_plan", route_from_plan, worker_targets)

        for worker in team.workers:
            graph.add_edge(f"worker_{worker.name}", "leader_review")

        graph.add_conditional_edges("leader_review", route_from_review, worker_targets)

        return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/graph.py tests/runtime/test_graph.py
git commit -m "feat(runtime): add TeamCompiler with routing"
```

---

## Task 9: 端到端集成测试

**Files:**
- Modify: `tests/runtime/test_graph.py`（追加端到端测试）

- [ ] **Step 1: 追加端到端测试到 `tests/runtime/test_graph.py`**

在文件末尾追加：

```python
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agentteam.runtime.nodes import Plan, PlanStep


def test_end_to_end_run_two_steps():
    """Leader 拆 2 步 → coder 执行 → review → tester 执行 → review → 结束。"""
    team = _make_team()

    # Leader LLM：1 次结构化输出（拆计划）+ 2 次 invoke（点评）
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="写 hello world"),
        PlanStep(worker="tester", instruction="写测试"),
    ])])
    leader_llm.set_invoke_responses([
        AIMessage(content="coder 干得不错"),
        AIMessage(content="tester 测试到位，全部完成"),
    ])

    # Worker LLM：coder 1 次 + tester 1 次（都直接给最终答案，不调工具）
    worker_llm = FakeLLM()
    worker_llm.set_invoke_responses([
        AIMessage(content="print('hello world')"),
        AIMessage(content="assert True"),
    ])

    provider = FakeModelProvider({
        "leader-model": leader_llm,
        "worker-model": worker_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "开发 hello world"},
        config={"configurable": {"thread_id": "t1"}},
    )

    # 计划 2 步全部完成
    assert len(result["plan"]) == 2
    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "done"

    # 两个 worker 都有产出
    assert result["worker_outputs"]["coder"] == "print('hello world')"
    assert result["worker_outputs"]["tester"] == "assert True"

    # current_step 推进到末尾
    assert result["current_step"] == 2

    # 消息历史包含 leader_plan + coder + review + tester + review
    assert len(result["messages"]) >= 5

    # audit_events 累积：leader_plan + worker_end×2 + leader_review×2 = 5
    assert len(result["audit_events"]) == 5


def test_end_to_end_empty_plan_ends_immediately():
    """Leader 拆出空计划 → 立即结束。"""
    team = _make_team()

    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[])])

    provider = FakeModelProvider({"leader-model": leader_llm, "worker-model": FakeLLM()})
    compiler = TeamCompiler(provider, ToolRegistry())
    graph = compiler.compile(team, checkpointer=MemorySaver())

    result = graph.invoke(
        {"task": "啥也不用做"},
        config={"configurable": {"thread_id": "t2"}},
    )

    assert result["plan"] == []
    assert result["worker_outputs"] == {}
```

- [ ] **Step 2: 跑测试确认通过**

Run: `pytest tests/runtime/test_graph.py -v`
Expected: PASS（全部通过，含结构测试 5 + 集成测试 2）

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_graph.py
git commit -m "test(runtime): add end-to-end integration tests"
```

---

## Task 10: pyproject 依赖 + README 收尾

**Files:**
- Modify: `pyproject.toml`（dependencies 追加 langgraph）
- Modify: `README.md`（M2 状态勾选 + 模块说明）

- [ ] **Step 1: 跑全量测试**

Run: `pytest -v`
Expected: 全部 PASS（M1 的 37 + M2 新增约 20 = 约 57 个测试）

- [ ] **Step 2: 修改 `pyproject.toml` 追加 langgraph 依赖**

把 `dependencies` 改为：

```toml
dependencies = [
    "langchain-core>=0.3",
    "langgraph>=0.2",
    "pydantic>=2",
]
```

- [ ] **Step 3: 修改 `README.md` 更新 M2 状态**

把 `## 模块` 区块改为（新增 domain + runtime）：

```markdown
## 模块

- `agentteam.models` —— 多供应商模型抽象（Qwen/OpenAI/Anthropic/Ollama）
- `agentteam.tools` —— ToolRegistry + 原生技能（read_file/write_file/list_dir）
- `agentteam.storage` —— SQLite 持久化（runs / run_events / approvals）
- `agentteam.domain` —— 领域模型（Team/Worker/Leader/ApprovalPolicy）
- `agentteam.runtime` —— TeamCompiler + LangGraph StateGraph 编译执行
```

把 `## 状态` 区块的 M2 行改为：

```markdown
- [x] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml README.md
git commit -m "docs: mark M2 complete, add langgraph dependency"
```

- [ ] **Step 5: 验证最终状态**

Run: `pytest -q && git log --oneline -12`
Expected: 全测试通过，约 10 个 M2 commit

---

## 完成标准

M2 完成时应满足：
1. `pytest -q` 全绿（M1 的 37 + M2 新增 ≈ 20 = ≈ 57 个测试）
2. `Team`/`Worker`/`Leader`/`ApprovalPolicy` 领域模型可构造、字段正确
3. `TeamCompiler.compile(team)` 产出含 `leader_plan`/`worker_{name}`/`leader_review` 节点的 StateGraph
4. 端到端集成测试：mock LLM 驱动 Leader 拆计划 → Worker ReAct 执行 → Leader 审核推进 → 全部完成
5. worker ReAct 循环能调工具并产出最终答案，达到 max_iterations 时强制结束
6. 代码全部提交到 git，commit 粒度清晰
