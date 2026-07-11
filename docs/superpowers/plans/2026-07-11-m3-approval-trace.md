# M3: 审批与轨迹 (Approval & Trace) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add human-in-the-loop approval (worker-level + step-level via LangGraph `interrupt()`), structured AuditEvent trace persistence (TraceWriter → SQLite `run_events`), and SqliteSaver checkpoint-based resume to the agent team runtime.

**Architecture:** TraceWriter protocol injected into node factories at compile time; approval gate nodes conditionally inserted into the StateGraph based on `ApprovalPolicy`; `interrupt()` pauses execution at gate nodes, `Command(resume=...)` continues it. SqliteSaver provides checkpoint persistence for resume across sessions. Tool-level approval is deferred to M4 (requires worker ReAct sub-graph refactor to avoid re-execution of side-effecting tools).

**Tech Stack:** LangGraph 1.1.3 (`interrupt`, `Command`, `StateGraph`, `MemorySaver`), `langgraph-checkpoint-sqlite` 3.1.0 (`SqliteSaver`), existing M1 storage layer (`RunRepo`, `AuditRepo`).

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `agentteam/runtime/trace.py` | Create | TraceWriter protocol + SqliteTraceWriter + FakeTraceWriter |
| `agentteam/runtime/approval.py` | Create | `make_step_gate` + `make_worker_gate` node factories |
| `agentteam/runtime/state.py` | Modify | Add `run_id`, `pending_approval` to TeamState; add `is_rejected` helper |
| `agentteam/runtime/nodes.py` | Modify | Add `trace_writer` param; emit events at key points |
| `agentteam/runtime/graph.py` | Modify | Conditional gate nodes; updated routing; `trace_writer`/`audit_repo` params |
| `agentteam/runtime/__init__.py` | Modify | Convenience exports (I-4 tech debt) |
| `tests/conftest.py` | Modify | Add `fake_trace_writer` fixture |
| `tests/runtime/test_trace.py` | Create | TraceWriter tests |
| `tests/runtime/test_approval.py` | Create | Approval gate tests |
| `tests/runtime/test_state.py` | Modify | `run_id` + `pending_approval` tests |
| `tests/runtime/test_nodes.py` | Modify | Trace event emission tests; I-2 tech debt fix |
| `tests/runtime/test_graph.py` | Modify | Gate routing + E2E interrupt/resume tests |
| `pyproject.toml` | Modify | Add `langgraph-checkpoint-sqlite` |
| `README.md` | Modify | Mark M3 complete |

---

## Key Design Decisions

1. **Gates are conditional**: Step gate and worker gates are only added to the graph if the corresponding `ApprovalPolicy` exists. No policy → no gate → identical to M2 behavior.

2. **All side effects AFTER `interrupt()`**: Because `interrupt()` re-executes the node on resume, all DB writes (approval records, trace events) are placed AFTER the `interrupt()` call. On first execution, `interrupt()` pauses before any side effect. On resume, `interrupt()` returns the decision and side effects run exactly once.

3. **Unified routing via `route_to_worker`**: After step_gate, a single routing function handles both post-plan and post-review cases by checking `current_step` (which is 0 after plan, incremented after review). This works because `route_from_review(state)` after `leader_plan` (where `current_step=0`) gives the same result as `route_from_plan(state)`.

4. **Rejection → END**: For M3, any rejection routes to END (terminate run). Re-planning is deferred to M5 (API layer).

5. **TraceWriter/AuditRepo optional**: Both default to `None`. When `None`, nodes skip DB operations. This enables pure in-memory unit tests without DB setup.

6. **`run_id` in TeamState**: Set in the initial state when invoking the graph. All nodes read it from state. In production (M5), the API layer creates a run via `RunRepo.create_run()` and sets `run_id`.

---

## Task 1: TraceWriter Protocol and Implementations

**Files:**
- Create: `agentteam/runtime/trace.py`
- Test: `tests/runtime/test_trace.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_trace.py
"""TraceWriter 协议与实现的测试。"""
from __future__ import annotations

from agentteam.runtime.trace import FakeTraceWriter, SqliteTraceWriter
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo


def test_fake_trace_writer_collects_events():
    """FakeTraceWriter 按顺序收集事件到列表。"""
    tw = FakeTraceWriter()
    tw.emit("run1", "run_start", "system")
    tw.emit("run1", "leader_plan", "leader", {"steps": 3})
    tw.emit("run1", "worker_end", "w1", duration_ms=150, tokens=42)

    assert len(tw.events) == 3
    assert tw.events[0]["event_type"] == "run_start"
    assert tw.events[0]["run_id"] == "run1"
    assert tw.events[1]["payload"] == {"steps": 3}
    assert tw.events[2]["duration_ms"] == 150
    assert tw.events[2]["tokens"] == 42


def test_fake_trace_writer_starts_empty():
    """FakeTraceWriter 初始无事件。"""
    tw = FakeTraceWriter()
    assert tw.events == []


def test_sqlite_trace_writer_writes_to_db(tmp_db):
    """SqliteTraceWriter 将事件写入 run_events 表。"""
    run_repo = RunRepo(tmp_db)
    run_id = run_repo.create_run("team1", "task1")

    audit_repo = AuditRepo(tmp_db)
    tw = SqliteTraceWriter(audit_repo)
    tw.emit(run_id, "run_start", "system", {"key": "value"})

    events = audit_repo.list_events(run_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "run_start"
    assert events[0]["actor"] == "system"
    import json
    assert json.loads(events[0]["payload"]) == {"key": "value"}


def test_sqlite_trace_writer_multiple_events(tmp_db):
    """SqliteTraceWriter 按顺序写入多个事件。"""
    run_repo = RunRepo(tmp_db)
    run_id = run_repo.create_run("team1", "task1")

    audit_repo = AuditRepo(tmp_db)
    tw = SqliteTraceWriter(audit_repo)
    tw.emit(run_id, "run_start", "system")
    tw.emit(run_id, "leader_plan", "leader")
    tw.emit(run_id, "run_end", "system")

    events = audit_repo.list_events(run_id)
    assert len(events) == 3
    assert events[0]["event_type"] == "run_start"
    assert events[2]["event_type"] == "run_end"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/runtime/test_trace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.runtime.trace'`

- [ ] **Step 3: Write the implementation**

```python
# agentteam/runtime/trace.py
"""执行轨迹写入器：将 AuditEvent 写入持久化存储。"""
from __future__ import annotations

from typing import Any, Protocol

from agentteam.storage.audit import AuditRepo


class TraceWriter(Protocol):
    """执行轨迹写入器协议。节点通过它 emit 审计事件。"""

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        """写入一条审计事件。"""
        ...


class SqliteTraceWriter:
    """将 AuditEvent 写入 SQLite run_events 表。"""

    def __init__(self, audit_repo: AuditRepo) -> None:
        self._repo = audit_repo

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        self._repo.add_event(run_id, event_type, actor, payload, duration_ms, tokens)


class FakeTraceWriter:
    """测试用轨迹写入器，收集事件到内存列表。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> None:
        self.events.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "actor": actor,
                "payload": payload,
                "duration_ms": duration_ms,
                "tokens": tokens,
            }
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_trace.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/trace.py tests/runtime/test_trace.py
git commit -m "feat(runtime): add TraceWriter protocol with SQLite and Fake implementations"
```

---

## Task 2: TeamState Additions

**Files:**
- Modify: `agentteam/runtime/state.py`
- Modify: `tests/runtime/test_state.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_state.py`:

```python
def test_state_supports_run_id():
    """TeamState 包含 run_id 字段。"""
    state: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": None,
    }
    assert state["run_id"] == "run-001"


def test_state_supports_pending_approval():
    """TeamState 包含 pending_approval 字段，可为 None 或 dict。"""
    state_none: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": None,
    }
    assert state_none["pending_approval"] is None

    state_rejected: TeamState = {
        "messages": [],
        "task": "test",
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": "run-001",
        "pending_approval": {"gate": "step", "approved": False},
    }
    assert state_rejected["pending_approval"]["approved"] is False


def test_is_rejected_helper():
    """is_rejected 正确判断审批拒绝状态。"""
    from agentteam.runtime.state import is_rejected

    assert is_rejected({"pending_approval": None}) is False
    assert is_rejected({"pending_approval": {"approved": True}}) is False
    assert is_rejected({"pending_approval": {"approved": False}}) is True
    assert is_rejected({}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/runtime/test_state.py::test_state_supports_run_id -v`
Expected: FAIL with `KeyError: 'run_id'` or `AttributeError: module ... has no attribute 'is_rejected'`

- [ ] **Step 3: Write the implementation**

Modify `agentteam/runtime/state.py` — add `run_id`, `pending_approval` to `TeamState`, and add `is_rejected` helper function:

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
    run_id: str
    pending_approval: dict | None


def is_rejected(state: dict) -> bool:
    """检查状态中是否有被拒绝的审批。"""
    pending = state.get("pending_approval")
    return pending is not None and not pending.get("approved", True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_state.py -v`
Expected: PASS (all tests, including existing ones)

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/state.py tests/runtime/test_state.py
git commit -m "feat(runtime): add run_id and pending_approval to TeamState, add is_rejected helper"
```

---

## Task 3: Wire TraceWriter into Existing Nodes

**Files:**
- Modify: `tests/conftest.py` (add `fake_trace_writer` fixture)
- Modify: `agentteam/runtime/nodes.py` (add `trace_writer` param, emit events)
- Modify: `tests/runtime/test_nodes.py` (add trace event tests)

- [ ] **Step 1: Add `fake_trace_writer` fixture to conftest.py**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def fake_trace_writer():
    from agentteam.runtime.trace import FakeTraceWriter
    return FakeTraceWriter()
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/runtime/test_nodes.py`:

```python
def test_leader_plan_emits_trace_event(fake_llm, fake_trace_writer):
    """leader_plan 节点 emit leader_plan 轨迹事件。"""
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_plan_node(leader, fake_llm, trace_writer=fake_trace_writer)
    state = {"task": "test task", "run_id": "run-1"}
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "leader_plan"
    assert fake_trace_writer.events[0]["actor"] == "leader"
    assert fake_trace_writer.events[0]["run_id"] == "run-1"


def test_worker_node_emits_start_and_end_events(fake_llm, fake_trace_writer):
    """worker 节点 emit worker_start 和 worker_end 轨迹事件。"""
    fake_llm.set_invoke_responses([AIMessage(content="done")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_worker_node(worker, fake_llm, [], trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "run-1",
    }
    node(state)
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "worker_start" in event_types
    assert "worker_end" in event_types


def test_leader_review_emits_trace_event(fake_llm, fake_trace_writer):
    """leader_review 节点 emit leader_review 轨迹事件。"""
    fake_llm.set_invoke_responses([AIMessage(content="good job")])
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_review_node(leader, fake_llm, trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "done"}],
        "current_step": 0,
        "worker_outputs": {"w1": "done"},
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "leader_review"


def test_node_without_trace_writer_works(fake_llm):
    """不传 trace_writer 时节点正常工作（向后兼容）。"""
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_plan_node(leader, fake_llm)
    result = node({"task": "test", "run_id": "run-1"})
    assert "plan" in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/runtime/test_nodes.py::test_leader_plan_emits_trace_event -v`
Expected: FAIL — `make_leader_plan_node` doesn't accept `trace_writer` param yet.

- [ ] **Step 4: Write the implementation**

Replace `agentteam/runtime/nodes.py` with the updated version (adds `trace_writer=None` param to all three factories and emits events):

```python
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentteam.domain.team import Leader
from agentteam.domain.worker import Worker
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。"""

    worker: str = Field(description="执行此步的 worker name")
    instruction: str = Field(description="子任务描述")


class Plan(BaseModel):
    """Leader 拆解出的执行计划。"""

    steps: list[PlanStep] = Field(description="按顺序执行的步骤列表")


def make_leader_plan_node(
    leader: Leader, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。"""

    def leader_plan(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
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
        if trace_writer:
            trace_writer.emit(run_id, "leader_plan", leader.name, {"steps": len(plan)})
        return {
            "plan": plan,
            "current_step": 0,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": leader.name}],
        }

    return leader_plan


def make_worker_node(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
):
    """创建 worker 节点：内部跑 ReAct 循环（LLM 调工具直到给出最终答案）。"""

    def worker_react(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        if trace_writer:
            trace_writer.emit(run_id, "worker_start", worker.name)

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

        if trace_writer:
            trace_writer.emit(
                run_id, "worker_end", worker.name, {"answer_length": len(final_answer)}
            )
        return {
            "worker_outputs": {worker.name: final_answer},
            "messages": [
                AIMessage(content=f"[{worker.name}] {final_answer}", name=worker.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": worker.name}],
        }

    return worker_react


def make_leader_review_node(
    leader: Leader, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_review 节点：点评 worker 产出，标记步骤完成，推进 current_step。"""

    def leader_review(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        current = state["current_step"]
        plan = list(state["plan"])
        plan[current] = {**plan[current], "status": "done"}
        worker_name = plan[current]["worker"]
        outputs = state.get("worker_outputs", {})
        review_response = llm.invoke(
            [
                SystemMessage(content=leader.system_prompt),
                HumanMessage(
                    content=(
                        f"Worker {worker_name} 完成了步骤 {current}，"
                        f"产出：{outputs.get(worker_name, '')}。请简要点评。"
                    )
                ),
            ]
        )
        if trace_writer:
            trace_writer.emit(run_id, "leader_review", leader.name)
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_nodes.py -v`
Expected: PASS (all tests, including existing M2 tests)

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py agentteam/runtime/nodes.py tests/runtime/test_nodes.py
git commit -m "feat(runtime): wire TraceWriter into leader_plan, worker, and leader_review nodes"
```

---

## Task 4: Approval Gate Nodes

**Files:**
- Create: `agentteam/runtime/approval.py`
- Create: `tests/runtime/test_approval.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_approval.py
"""审批门节点的测试。"""
from __future__ import annotations

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.trace import FakeTraceWriter


# --- Step Gate: No-op cases (testable without graph) ---


def test_step_gate_no_policy_returns_empty():
    """无策略时 step_gate 是 no-op。"""
    gate = make_step_gate(None)
    result = gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
    assert result == {}


def test_step_gate_wrong_level_returns_empty():
    """策略 level 不是 step 时 no-op。"""
    policy = ApprovalPolicy(level="worker")
    gate = make_step_gate(policy)
    result = gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
    assert result == {}


def test_step_gate_no_more_steps_returns_empty():
    """无更多步骤时 step_gate no-op。"""
    policy = ApprovalPolicy(level="step")
    gate = make_step_gate(policy)
    result = gate({"run_id": "r1", "current_step": 3, "plan": [{"worker": "w1"}]})
    assert result == {}


# --- Worker Gate: No-op cases ---


def test_worker_gate_no_policy_returns_empty():
    """无策略时 worker_gate 是 no-op。"""
    gate = make_worker_gate("w1", None)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_wrong_level_returns_empty():
    """策略 level 不是 worker 时 no-op。"""
    policy = ApprovalPolicy(level="step")
    gate = make_worker_gate("w1", policy)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_target_not_in_list_returns_empty():
    """worker 不在 targets 列表中时 no-op。"""
    policy = ApprovalPolicy(level="worker", targets=["other_worker"])
    gate = make_worker_gate("w1", policy)
    result = gate({"run_id": "r1"})
    assert result == {}


def test_worker_gate_target_in_list_proceeds():
    """worker 在 targets 列表中时不 no-op（会尝试 interrupt）。
    在图外调用 interrupt 会抛 RuntimeError，验证它确实进入了审批逻辑。"""
    policy = ApprovalPolicy(level="worker", targets=["w1"])
    gate = make_worker_gate("w1", policy)
    try:
        gate({"run_id": "r1"})
        assert False, "应该抛出 RuntimeError（interrupt 在图外调用）"
    except RuntimeError:
        pass  # 预期行为：说明 interrupt 被调用了


# --- Step Gate: enters interrupt ---


def test_step_gate_enters_interrupt():
    """有 step 策略且有步骤时，step_gate 调用 interrupt。"""
    policy = ApprovalPolicy(level="step")
    gate = make_step_gate(policy)
    try:
        gate({"run_id": "r1", "current_step": 0, "plan": [{"worker": "w1"}]})
        assert False, "应该抛出 RuntimeError（interrupt 在图外调用）"
    except RuntimeError:
        pass  # 预期行为
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/runtime/test_approval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.runtime.approval'`

- [ ] **Step 3: Write the implementation**

```python
# agentteam/runtime/approval.py
"""审批门节点：worker 级和 step 级审批，使用 LangGraph interrupt() 实现。"""
from __future__ import annotations

from langgraph.types import interrupt

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


def _should_approve(policy: ApprovalPolicy, target: str | None = None) -> bool:
    """检查审批策略是否适用于当前目标。"""
    if policy.targets is None:
        return True
    return target in policy.targets


def make_step_gate(
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 step 级审批门。

    每步执行前触发 interrupt()。无策略或无更多步骤时为 no-op。
    所有 DB 副作用放在 interrupt() 之后，避免 resume 时重复执行。
    """

    def step_gate(state: TeamState) -> dict:
        if policy is None or policy.level != "step":
            return {}

        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current >= len(plan):
            return {}

        run_id = state.get("run_id", "")

        # interrupt() 在首次执行时暂停图；resume 时返回决策值
        decision = interrupt(
            {
                "gate": "step",
                "step": current,
                "message": f"Step {current} 需要审批",
            }
        )

        # 以下代码仅在 resume 时执行（一次）
        approved = decision.get("approved", False)
        decider = decision.get("decider", "unknown")

        if audit_repo is not None:
            approval_id = audit_repo.add_approval(run_id)
            audit_repo.decide_approval(
                approval_id, "approved" if approved else "rejected", decider
            )

        if trace_writer is not None:
            trace_writer.emit(
                run_id,
                "approval_requested",
                "system",
                {"gate": "step", "step": current},
            )
            trace_writer.emit(
                run_id,
                "approval_decided",
                decider,
                {"gate": "step", "approved": approved},
            )

        if not approved:
            return {"pending_approval": {"gate": "step", "approved": False}}
        return {"pending_approval": None}

    return step_gate


def make_worker_gate(
    worker_name: str,
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 worker 级审批门。

    worker 执行前触发 interrupt()。无策略或 worker 不在 targets 中时为 no-op。
    所有 DB 副作用放在 interrupt() 之后，避免 resume 时重复执行。
    """

    def worker_gate(state: TeamState) -> dict:
        if policy is None or policy.level != "worker":
            return {}
        if not _should_approve(policy, worker_name):
            return {}

        run_id = state.get("run_id", "")

        decision = interrupt(
            {
                "gate": "worker",
                "worker": worker_name,
                "message": f"Worker {worker_name} 需要审批",
            }
        )

        approved = decision.get("approved", False)
        decider = decision.get("decider", "unknown")

        if audit_repo is not None:
            approval_id = audit_repo.add_approval(run_id)
            audit_repo.decide_approval(
                approval_id, "approved" if approved else "rejected", decider
            )

        if trace_writer is not None:
            trace_writer.emit(
                run_id,
                "approval_requested",
                "system",
                {"gate": "worker", "worker": worker_name},
            )
            trace_writer.emit(
                run_id,
                "approval_decided",
                decider,
                {"gate": "worker", "approved": approved},
            )

        if not approved:
            return {"pending_approval": {"gate": "worker", "approved": False}}
        return {"pending_approval": None}

    return worker_gate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_approval.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/approval.py tests/runtime/test_approval.py
git commit -m "feat(runtime): add step-level and worker-level approval gate nodes"
```

---

## Task 5: TeamCompiler — Conditional Gates, Routing, and Rejection

**Files:**
- Modify: `agentteam/runtime/graph.py`
- Modify: `tests/runtime/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_graph.py`:

```python
def test_compile_with_step_policy_adds_step_gate(fake_llm):
    """有 step 级策略时，编译出的图包含 step_gate 节点。"""
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test", approval_policy=ApprovalPolicy(level="step")),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "step_gate" in node_names


def test_compile_without_policy_has_no_gates(fake_llm):
    """无策略时，编译出的图不含任何 gate 节点（与 M2 一致）。"""
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "step_gate" not in node_names
    assert not any(n.startswith("worker_gate") for n in node_names)


def test_compile_with_worker_policy_adds_worker_gate(fake_llm):
    """有 worker 级策略时，编译出的图包含 worker_gate 节点。"""
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[
            Worker(
                name="w1",
                role="r",
                description="",
                system_prompt="test",
                approval_policy=ApprovalPolicy(level="worker"),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "worker_gate_w1" in node_names


def test_compile_accepts_trace_writer_and_audit_repo(fake_llm, fake_trace_writer, tmp_db):
    """compile 接受 trace_writer 和 audit_repo 参数。"""
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.storage.audit import AuditRepo
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    audit_repo = AuditRepo(tmp_db)
    graph = compiler.compile(
        team, trace_writer=fake_trace_writer, audit_repo=audit_repo
    )
    assert graph is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/runtime/test_graph.py::test_compile_with_step_policy_adds_step_gate -v`
Expected: FAIL — `TeamCompiler.compile` doesn't add gate nodes yet.

- [ ] **Step 3: Write the implementation**

Replace `agentteam/runtime/graph.py` with:

```python
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import TraceWriter
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


def route_to_worker(state: TeamState) -> str:
    """统一路由：拒绝→END，无更多步骤→END，否则→下一步 worker。"""
    if is_rejected(state):
        return END
    return route_from_review(state)


def make_route_after_worker_gate(worker_node_name: str):
    """创建 worker_gate 之后的路由函数：拒绝→END，否则→worker。"""

    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return worker_node_name

    return route


class TeamCompiler:
    """把 Team 配置编译成可执行的 LangGraph StateGraph。"""

    def __init__(self, model_provider: ModelProvider, tool_registry: ToolRegistry):
        self._mp = model_provider
        self._tr = tool_registry

    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        graph = StateGraph(TeamState)

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

        # Worker 节点 + worker gate
        worker_gates: dict[str, bool] = {}
        for worker in team.workers:
            worker_model = worker.model or team.default_model
            llm = self._mp.get_llm(worker_model)
            tools = self._tr.get_tools(worker.tools) if worker.tools else []
            graph.add_node(
                f"worker_{worker.name}",
                make_worker_node(worker, llm, tools, trace_writer),
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_graph.py -v`
Expected: PASS (all tests, including existing M2 tests)

- [ ] **Step 5: Commit**

```bash
git add agentteam/runtime/graph.py tests/runtime/test_graph.py
git commit -m "feat(runtime): add conditional approval gates and rejection routing to TeamCompiler"
```

---

## Task 6: E2E Interrupt/Resume Integration Tests

**Files:**
- Modify: `tests/runtime/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_graph.py`:

```python
def _make_initial_state(task="test task", run_id="run-e2e"):
    """构建 E2E 测试用的初始状态。"""
    return {
        "messages": [],
        "task": task,
        "plan": [],
        "current_step": 0,
        "worker_outputs": {},
        "audit_events": [],
        "run_id": run_id,
        "pending_approval": None,
    }


def test_e2e_step_approval_interrupt_resume(fake_llm, fake_trace_writer):
    """E2E：step 级审批 → interrupt → resume → 完成。"""
    from langchain_core.messages import AIMessage
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

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="task done"), AIMessage(content="good job")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-step"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 step_gate 处 interrupt
    result = graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在 step_gate 处暂停"

    # Resume：批准
    result = graph.invoke(
        Command(resume={"approved": True, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证 worker 产出
    values = state.values
    assert "w1" in values.get("worker_outputs", {})

    # 验证轨迹事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "leader_plan" in event_types
    assert "approval_requested" in event_types
    assert "approval_decided" in event_types
    assert "worker_start" in event_types
    assert "worker_end" in event_types


def test_e2e_worker_approval_interrupt_resume(fake_llm, fake_trace_writer):
    """E2E：worker 级审批 → interrupt → resume → 完成。"""
    from langchain_core.messages import AIMessage
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

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="task done"), AIMessage(content="good job")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[
            Worker(
                name="w1", role="r", description="", system_prompt="test",
                approval_policy=ApprovalPolicy(level="worker"),
            )
        ],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-worker"}}
    initial = _make_initial_state()

    # 第一次 invoke：应在 worker_gate 处 interrupt
    result = graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "图应该在 worker_gate 处暂停"

    # Resume：批准
    result = graph.invoke(
        Command(resume={"approved": True, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    values = state.values
    assert "w1" in values.get("worker_outputs", {})


def test_e2e_step_approval_rejection_terminates(fake_llm, fake_trace_writer):
    """E2E：step 级审批被拒绝 → 图终止。"""
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

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-reject"}}
    initial = _make_initial_state()

    # 第一次 invoke：interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next

    # Resume：拒绝
    graph.invoke(
        Command(resume={"approved": False, "decider": "tester"}), config
    )
    state = graph.get_state(config)
    assert not state.next, "图应该已终止"

    # worker 不应该执行
    values = state.values
    assert "w1" not in values.get("worker_outputs", {})


def test_e2e_no_policy_runs_without_interrupt(fake_llm, fake_trace_writer):
    """E2E：无审批策略时，图直接运行完成（无 interrupt）。"""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.runtime.nodes import Plan, PlanStep
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeModelProvider

    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer
    )

    config = {"configurable": {"thread_id": "e2e-nopolicy"}}
    result = graph.invoke(_make_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该直接完成"
    assert "w1" in state.values.get("worker_outputs", {})

    # 无审批事件
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "approval_requested" not in event_types
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_graph.py -v -k "e2e"`
Expected: PASS (4 tests). These tests verify the complete interrupt/resume cycle.

Note: These tests may pass immediately if Task 5 was implemented correctly. If they fail, debug the graph structure and routing.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_graph.py
git commit -m "test(runtime): add E2E interrupt/resume tests for step and worker approval"
```

---

## Task 7: SqliteSaver Integration Test

**Files:**
- Create: `tests/runtime/test_sqlite_saver.py`

- [ ] **Step 1: Write the test**

```python
# tests/runtime/test_sqlite_saver.py
"""SqliteSaver checkpoint 持久化集成测试。"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.runtime.trace import FakeTraceWriter
from agentteam.storage.db import init_db
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def test_sqlite_saver_persists_checkpoint_across_invocations(tmp_path):
    """SqliteSaver 在 invoke 之间持久化 checkpoint，支持断点续跑。"""
    from agentteam.domain.approval import ApprovalPolicy

    db_path = tmp_path / "test_checkpoint.db"
    conn = init_db(db_path)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    tw = FakeTraceWriter()
    graph = compiler.compile(team, checkpointer=checkpointer, trace_writer=tw)

    config = {"configurable": {"thread_id": "sqlite-test"}}
    initial = {
        "messages": [], "task": "test", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [],
        "run_id": "run-sqlite", "pending_approval": None,
    }

    # 第一次 invoke：interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "应在 step_gate 处暂停"

    # 关闭旧连接，用新连接创建新 checkpointer（模拟跨会话恢复）
    conn.close()
    conn2 = init_db(db_path)
    checkpointer2 = SqliteSaver(conn2)
    checkpointer2.setup()

    # 用新 checkpointer 重新编译图
    graph2 = compiler.compile(team, checkpointer=checkpointer2, trace_writer=tw)

    # Resume：应该能从 checkpoint 恢复
    graph2.invoke(Command(resume={"approved": True, "decider": "tester"}), config)
    state = graph2.get_state(config)
    assert not state.next, "图应该已完成"

    values = state.values
    assert "w1" in values.get("worker_outputs", {})

    conn2.close()


def test_sqlite_saver_no_interrupt_completes(tmp_path):
    """无审批策略时 SqliteSaver 图直接完成。"""
    db_path = tmp_path / "test_nointerrupt.db"
    conn = init_db(db_path)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "sqlite-nointerrupt"}}
    initial = {
        "messages": [], "task": "test", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [],
        "run_id": "run-sqlite2", "pending_approval": None,
    }

    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert not state.next
    assert "w1" in state.values.get("worker_outputs", {})

    conn.close()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/runtime/test_sqlite_saver.py -v`
Expected: PASS (2 tests). If the cross-session resume test fails, debug the SqliteSaver setup.

- [ ] **Step 3: Commit**

```bash
git add tests/runtime/test_sqlite_saver.py
git commit -m "test(runtime): add SqliteSaver checkpoint persistence integration tests"
```

---

## Task 8: Tech Debt, Dependencies, and README

**Files:**
- Modify: `tests/runtime/test_nodes.py` (I-2: strengthen `max_iterations` test)
- Modify: `agentteam/runtime/__init__.py` (I-4: add convenience exports)
- Modify: `pyproject.toml` (add `langgraph-checkpoint-sqlite`)
- Modify: `README.md` (mark M3 complete)

- [ ] **Step 1: Strengthen max_iterations test (I-2)**

Find the existing `test_worker_node_respects_max_iterations` test in `tests/runtime/test_nodes.py` and replace it with a stronger version that verifies the LLM was called exactly `max_iterations` times:

```python
def test_worker_node_respects_max_iterations(fake_llm):
    """max_iterations 到达时强制结束，LLM 被调用恰好 max_iterations 次。"""
    # 每次都返回 tool_calls，永不给出最终答案
    tool_call_response = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}],
    )
    fake_llm.set_invoke_responses([tool_call_response] * 100)
    worker = Worker(
        name="w1", role="r", description="", system_prompt="test", max_iterations=3
    )
    node = make_worker_node(worker, fake_llm, [])
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "r1",
    }
    result = node(state)
    # LLM 应该被调用恰好 3 次（max_iterations=3）
    assert fake_llm._inv_idx == 3
    # 最终答案应该是非 None（强制结束时取最后一条消息）
    assert result["worker_outputs"]["w1"] is not None
```

- [ ] **Step 2: Add runtime convenience exports (I-4)**

Replace `agentteam/runtime/__init__.py` with:

```python
"""agentteam.runtime — 执行内核（TeamCompiler, nodes, state, trace, approval）。"""

from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import FakeTraceWriter, SqliteTraceWriter, TraceWriter

__all__ = [
    "FakeTraceWriter",
    "SqliteTraceWriter",
    "TeamCompiler",
    "TeamState",
    "TraceWriter",
    "is_rejected",
    "make_leader_plan_node",
    "make_leader_review_node",
    "make_step_gate",
    "make_worker_gate",
    "make_worker_node",
]
```

- [ ] **Step 3: Add langgraph-checkpoint-sqlite to pyproject.toml**

In `pyproject.toml`, find the `dependencies` list and add `"langgraph-checkpoint-sqlite>=3.0"`:

```toml
dependencies = [
    "langchain>=0.3",
    "langgraph>=0.2",
    "langgraph-checkpoint-sqlite>=3.0",
    # ... existing deps ...
]
```

- [ ] **Step 4: Update README.md**

Find the milestones section in `README.md` and update M3 to checked:

```markdown
- [x] **M3 审批与轨迹**：interrupt 审批（worker/step 级）、AuditEvent 轨迹落库、SqliteSaver 断点续跑
```

Also add M3 modules to the module overview:

```markdown
### runtime/ — 执行内核
- `state.py` — TeamState 状态 schema（含 run_id、pending_approval）
- `nodes.py` — leader_plan / worker ReAct / leader_review 节点工厂
- `graph.py` — TeamCompiler（Team → StateGraph 编译，含审批门）
- `trace.py` — TraceWriter 协议（SQLite / Fake 实现）
- `approval.py` — 审批门节点（step 级 / worker 级，interrupt 实现）
```

- [ ] **Step 5: Run ALL tests to verify nothing broke**

Run: `python -m pytest -q`
Expected: PASS (all tests — M1 37 + M2 27 + M3 new tests)

- [ ] **Step 6: Commit**

```bash
git add tests/runtime/test_nodes.py agentteam/runtime/__init__.py pyproject.toml README.md
git commit -m "docs: mark M3 complete, add langgraph-checkpoint-sqlite dep, fix M2 tech debt"
```

---

## Self-Review Checklist

After all tasks are complete, verify:

1. **Spec coverage**: All M3-scoped spec sections implemented?
   - §7.1 SqliteSaver checkpoint → Task 7 ✓
   - §7.2 AuditEvent → run_events → Task 1+3 ✓
   - §8.1 interrupt + Command resume → Task 4+6 ✓
   - §8.2 worker 级 + step 级审批 → Task 4+5 ✓
   - §4.2 pending_approval in TeamState → Task 2 ✓

2. **Deferred items documented**: Tool-level approval deferred to M4 ✓

3. **M2 tech debt addressed**: I-2 (Task 8) ✓, I-3 (Task 3) ✓, I-4 (Task 8) ✓. I-1 deferred to M4.

4. **No placeholders**: All code is complete, no TBD/TODO ✓

5. **Type consistency**: `TraceWriter` protocol matches across trace.py, nodes.py, approval.py, graph.py ✓
