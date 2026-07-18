# SP6-P4 Run 取消机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AgentTeam 新增 Run 取消能力 —— 用户可通过 `POST /api/runs/{id}/cancel` 终止 running 或 interrupted 状态的 run,worker 节点在 LLM 调用入口通过 `threading.Event` 协作检测取消信号并抛出 `RunCancelledError`,RunManager 据此将 run 标记为 `cancelled` 并发布 `run_cancelled` 事件。

**Architecture:** 在 `RunManager` 中维护 `_cancel_events: dict[str, threading.Event]`,`start_run` 时为每个 run 创建 event,`cancel_run` 时 set event 并(对 running run)更新状态为 `cancelling`。`make_agent_step` 新增可选 `run_manager` 参数,在 worker LLM 调用前检查 `is_cancelled(run_id)`,命中则抛 `RunCancelledError(BaseException)`(继承 BaseException 以绕过 worker 内部 `except Exception` 吞没)。`_handle_error` 区分 `RunCancelledError` 与普通异常:前者标 `cancelled` + 发 `run_cancelled` 事件,后者沿用 `failed` 逻辑。interrupted run 的 cancel 走简化路径:直接 `end_run("cancelled")`,无需 lazy recompile。`TeamCompiler.__init__` 新增 `run_manager` 参数并透传到 worker 节点链路。

**Tech Stack:** Python 3.11+, FastAPI, langgraph, threading.Event, pytest, TestClient

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/api/run_manager.py` | RunCancelledError 异常 + `_cancel_events` 字典 + `is_cancelled` + `cancel_run` + `_handle_error` 分支 + `start_run`/`_cleanup_run` 维护 event | 修改 |
| `agentteam/runtime/nodes.py` | `make_agent_step` / `make_worker_subgraph` / `make_worker_node` 新增 `run_manager` 参数并在 worker 入口检查取消 | 修改 |
| `agentteam/runtime/graph.py` | `TeamCompiler.__init__` 接受 `run_manager`,`_compile_worker` 透传给 `make_worker_node` | 修改 |
| `agentteam/api/routes/runs.py` | `create_run` 构造 `TeamCompiler` 时传入 `run_manager`;新增 `POST /{run_id}/cancel` endpoint | 修改 |
| `tests/api/test_run_cancel.py` | P4 全部测试(RunCancelledError 基础设施、cancel_run、_handle_error 分支、agent_step 取消检查、cancel endpoint) | 新建 |

---

## Task 1: RunCancelledError + RunManager._cancel_events 基础设施

**Files:**
- Modify: `agentteam/api/run_manager.py`
- Create: `tests/api/test_run_cancel.py`

- [ ] **Step 1: 写失败测试 — is_cancelled 基础行为**

创建 `d:\project\agentTeam\tests\api\test_run_cancel.py`:

```python
"""P4 Run 取消机制测试。

覆盖:
- RunCancelledError 异常类型(继承 BaseException)
- RunManager._cancel_events 基础设施(start_run 创建 event,is_cancelled 读 event)
- cancel_run(running / interrupted 两种路径)
- _handle_error 区分 RunCancelledError vs 普通异常
- make_agent_step 在 worker 入口检查取消信号
- POST /api/runs/{id}/cancel endpoint
"""
import threading
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunCancelledError, RunManager
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _make_run_manager():
    """构造一个 RunManager,repo 全部 mock,便于断言调用。"""
    return RunManager(
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
    )


def test_runcancellederror_inherits_baseexception():
    """RunCancelledError 继承 BaseException(非 Exception),避免被 worker 内 except Exception 吞没。"""
    assert issubclass(RunCancelledError, BaseException)
    assert not issubclass(RunCancelledError, Exception)


def test_is_cancelled_returns_false_before_cancel():
    """run 未被 cancel 时,is_cancelled 返回 False。"""
    rm = _make_run_manager()
    run_id = "run-1"
    # 模拟 start_run 已为该 run 创建 event(尚未 set)
    rm._cancel_events[run_id] = threading.Event()
    assert rm.is_cancelled(run_id) is False


def test_is_cancelled_returns_true_after_cancel_run():
    """cancel 信号发出(event 被 set)后,is_cancelled 返回 True。

    Task 1 阶段尚未实现 cancel_run(见 Task 2),
    此处直接 set event 模拟 cancel 信号已发出。
    """
    rm = _make_run_manager()
    run_id = "run-2"
    rm._cancel_events[run_id] = threading.Event()
    # 模拟 cancel_run 调用后会做的:set event
    rm._cancel_events[run_id].set()
    assert rm.is_cancelled(run_id) is True


def test_is_cancelled_returns_false_for_unknown_run():
    """未知 run_id(未 start_run)时 is_cancelled 返回 False,不抛 KeyError。"""
    rm = _make_run_manager()
    assert rm.is_cancelled("nonexistent-run") is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 4 个测试 FAIL(`ImportError: cannot import name 'RunCancelledError' from 'agentteam.api.run_manager'`)

- [ ] **Step 3: 实现 — 在 run_manager.py 顶部新增 RunCancelledError + _cancel_events + is_cancelled**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py`。

3a. 在 `from agentteam.storage.runs import RunRepo` 之后、`class RunManager:` 之前,新增 `RunCancelledError`:

```python
class RunCancelledError(BaseException):
    """run 被用户取消,worker 节点检测到 cancel event 后抛出。

    继承 BaseException(而非 Exception)以绕过 worker 内部
    `try: ... except Exception:` 的常规 catch,确保取消信号能
    一路传播到 RunManager._handle_error 被识别并标记为 cancelled。
    """

    pass
```

3b. 在 `RunManager.__init__` 末尾(`self._lock = threading.Lock()` 之后)新增 `_cancel_events`:

```python
        self._cancel_events: dict[str, threading.Event] = {}
```

完整的 `__init__` 应为:

```python
    def __init__(self, run_repo: RunRepo, audit_repo: AuditRepo, event_bus: EventBus) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
```

3c. 在 `start_run` 中,`with self._lock:` 块内 `self._configs[run_id] = config` 之后新增 event 创建。完整的 `start_run` 应为:

```python
    def start_run(self, run_id: str, graph, config: dict, task: str) -> None:
        """在后台线程中跑 graph.invoke()，立即返回。"""
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
            self._cancel_events[run_id] = threading.Event()
        self._run_repo.update_status(run_id, "running")
        thread = threading.Thread(
            target=self._run_in_background,
            args=(run_id, graph, config, task),
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
```

3d. 在 `wait` 方法之后、`_cleanup_run` 之前新增 `is_cancelled` 方法:

```python
    def is_cancelled(self, run_id: str) -> bool:
        """供 worker 节点轮询检查:run 是否被用户请求取消。

        未知 run_id(未 start_run 或已 cleanup)返回 False,不抛异常。
        """
        event = self._cancel_events.get(run_id)
        return event is not None and event.is_set()
```

3e. 在 `_cleanup_run` 的 `with self._lock:` 块内新增清理 cancel event。完整的 `_cleanup_run` 应为:

```python
    def _cleanup_run(self, run_id: str) -> None:
        """清理已完成/失败的 run 的内存状态。

        interrupted 的 run 不清理——graph/config/threads 仍需用于 resume。
        """
        with self._lock:
            self._graphs.pop(run_id, None)
            self._configs.pop(run_id, None)
            self._threads.pop(run_id, None)
            self._cancel_events.pop(run_id, None)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 4 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/api/run_manager.py tests/api/test_run_cancel.py
git commit -m "feat(api): P4 RunCancelledError + _cancel_events 基础设施"
```

---

## Task 2: RunManager.cancel_run 方法(running + interrupted 两种路径)

**Files:**
- Modify: `agentteam/api/run_manager.py`
- Modify: `tests/api/test_run_cancel.py`(追加测试)

- [ ] **Step 1: 写失败测试 — cancel_run 处理 interrupted / running 两种状态**

在 `d:\project\agentTeam\tests\api\test_run_cancel.py` 末尾追加:

```python
def test_cancel_interrupted_run_ends_directly():
    """interrupted run cancel:直接 end_run('cancelled') + 发 run_cancelled 事件 + cleanup。

    简化方案(spec §6.3):interrupted 状态无需 recompile,直接结束。
    不应调用 update_status(interrupted 直接 end_run)。
    """
    rm = _make_run_manager()
    run_id = "run-interrupted"
    rm._run_repo.get_run.return_value = {"status": "interrupted"}

    result = rm.cancel_run(run_id)

    assert result is True
    # 直接 end_run("cancelled")
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    # 不走 update_status 路径(interrupted 直接结束)
    rm._run_repo.update_status.assert_not_called()
    # 发 run_cancelled 事件到 audit_repo
    rm._audit_repo.add_event.assert_called_once_with(run_id, "run_cancelled", "user")
    # publish 到 EventBus
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "run_cancelled"
    assert published["run_id"] == run_id


def test_cancel_running_run_sets_event_and_status_cancelling():
    """running run cancel:set cancel event + update_status('cancelling')。

    running 状态不直接 end_run,而是设置 event 让 worker 检测后抛
    RunCancelledError,由 _handle_error 标 cancelled(Task 3)。
    """
    rm = _make_run_manager()
    run_id = "run-running"
    # 模拟 start_run 已创建 event
    rm._cancel_events[run_id] = threading.Event()
    rm._run_repo.get_run.return_value = {"status": "running"}

    result = rm.cancel_run(run_id)

    assert result is True
    # event 被 set
    assert rm._cancel_events[run_id].is_set() is True
    # 状态更新为 cancelling(中间态)
    rm._run_repo.update_status.assert_called_once_with(run_id, "cancelling")
    # running 状态不直接 end_run(等 worker 抛 RunCancelledError)
    rm._run_repo.end_run.assert_not_called()


def test_cancel_completed_run_returns_false():
    """completed run cancel:返回 False(不可取消)。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = {"status": "completed"}

    result = rm.cancel_run("run-completed")

    assert result is False
    rm._run_repo.end_run.assert_not_called()
    rm._run_repo.update_status.assert_not_called()


def test_cancel_failed_run_returns_false():
    """failed run cancel:返回 False(不可取消)。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = {"status": "failed"}

    result = rm.cancel_run("run-failed")

    assert result is False


def test_cancel_unknown_run_returns_false():
    """未知 run(get_run 返回 None)cancel:返回 False,不抛异常。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = None

    result = rm.cancel_run("run-nonexistent")

    assert result is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 5 个新测试 FAIL(`AttributeError: 'RunManager' object has no attribute 'cancel_run'`),Task 1 的 4 个测试仍 PASS

- [ ] **Step 3: 实现 — 在 RunManager 中新增 cancel_run 方法**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py`,在 `is_cancelled` 方法之后、`_cleanup_run` 之前新增 `cancel_run`:

```python
    def cancel_run(self, run_id: str) -> bool:
        """请求取消 run。返回是否成功发出取消信号。

        两种状态分别处理(spec §6.3 简化方案):
        - interrupted: 直接 end_run("cancelled") + 发 run_cancelled 事件 + cleanup。
          无需 lazy recompile,因为 run 已暂停,直接结束即可。
        - running: set cancel event + update_status("cancelling")。
          worker 在下一次 agent_step 入口检测到 event 后抛 RunCancelledError,
          由 _handle_error 标 cancelled。
        - 其他状态(completed/failed/cancelled/pending): 返回 False,不可取消。
        - 未知 run_id(get_run 返回 None): 返回 False。
        """
        run = self._run_repo.get_run(run_id)
        if run is None:
            return False
        status = run["status"]

        if status == "interrupted":
            # interrupted 直接结束,无需 recompile
            self._run_repo.end_run(run_id, "cancelled")
            eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
            self._bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "run_cancelled",
                    "run_id": run_id,
                    "payload": {"reason": "user requested cancel"},
                },
            )
            self._cleanup_run(run_id)
            return True

        if status == "running":
            # set event 让 worker 检测
            event = self._cancel_events.get(run_id)
            if event is None:
                # _cancel_events 缺失(异常情况):无法协作取消
                return False
            event.set()
            # 标中间态 cancelling,等 worker 抛 RunCancelledError 后由 _handle_error 收尾
            self._run_repo.update_status(run_id, "cancelling")
            return True

        # completed / failed / cancelled / pending 等终态或不可取消状态
        return False
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 9 PASS(4 from Task 1 + 5 new)

- [ ] **Step 5: 提交**

```powershell
git add agentteam/api/run_manager.py tests/api/test_run_cancel.py
git commit -m "feat(api): RunManager.cancel_run 处理 running + interrupted 两种状态"
```

---

## Task 3: _handle_error 区分 RunCancelledError + 发 run_cancelled 事件

**Files:**
- Modify: `agentteam/api/run_manager.py`
- Modify: `tests/api/test_run_cancel.py`(追加测试)

- [ ] **Step 1: 写失败测试 — _handle_error 对 RunCancelledError 标 cancelled,对普通异常仍标 failed**

在 `d:\project\agentTeam\tests\api\test_run_cancel.py` 末尾追加:

```python
def test_handle_error_with_cancelled_error_marks_cancelled():
    """_handle_error 收到 RunCancelledError 时:标 cancelled + 发 run_cancelled 事件。

    场景:worker agent_step 检测到 cancel event 后抛 RunCancelledError,
    信号沿调用栈传播到 _run_in_background 的 except,由 _handle_error 收尾。
    """
    rm = _make_run_manager()
    run_id = "run-cancelled-by-worker"
    # 模拟 start_run 已创建 event(否则 _cleanup_run pop 时 KeyError,虽有 .pop(None) 兜底但仍需设置)
    rm._cancel_events[run_id] = threading.Event()

    rm._handle_error(run_id, RunCancelledError("Run run-cancelled-by-worker cancelled by user"))

    # 标 cancelled(不是 failed)
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    # 发 run_cancelled 事件(actor=user,表示用户触发)
    rm._audit_repo.add_event.assert_called_once_with(run_id, "run_cancelled", "user")
    # publish 到 EventBus
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "run_cancelled"
    assert published["run_id"] == run_id
    # cleanup 被调用(event 已从 _cancel_events 移除)
    assert run_id not in rm._cancel_events


def test_handle_error_with_other_error_marks_failed():
    """回归保障:普通异常仍标 failed + 发 error 事件(不被 RunCancelledError 逻辑误触)。"""
    rm = _make_run_manager()
    run_id = "run-failed-by-bug"
    rm._cancel_events[run_id] = threading.Event()

    rm._handle_error(run_id, ValueError("something broke"))

    # 标 failed(不是 cancelled)
    rm._run_repo.end_run.assert_called_once_with(run_id, "failed")
    # 发 error 事件(actor=system)
    rm._audit_repo.add_event.assert_called_once_with(
        run_id, "error", "system", {"error": "something broke"}
    )
    # publish error 事件
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "error"
    assert published["run_id"] == run_id
    # cleanup 被调用
    assert run_id not in rm._cancel_events
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_cancel.py::test_handle_error_with_cancelled_error_marks_cancelled tests/api/test_run_cancel.py::test_handle_error_with_other_error_marks_failed -v`
Expected:
- `test_handle_error_with_cancelled_error_marks_cancelled` FAIL(当前 `_handle_error` 无条件标 failed,断言 `end_run("cancelled")` 不匹配)
- `test_handle_error_with_other_error_marks_failed` PASS(当前逻辑已符合)

- [ ] **Step 3: 实现 — 修改 _handle_error 区分 RunCancelledError**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py` 的 `_handle_error` 方法,完整替换为:

```python
    def _handle_error(self, run_id: str, error: BaseException) -> None:
        if isinstance(error, RunCancelledError):
            # 用户取消:标 cancelled + 发 run_cancelled 事件
            self._run_repo.end_run(run_id, "cancelled")
            eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
            self._bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "run_cancelled",
                    "run_id": run_id,
                    "payload": {"reason": "user requested cancel"},
                },
            )
        else:
            # 普通异常:沿用 failed 逻辑
            self._run_repo.end_run(run_id, "failed")
            eid = self._audit_repo.add_event(
                run_id, "error", "system", {"error": str(error)}
            )
            self._bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(error)},
                },
            )
        self._cleanup_run(run_id)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 11 PASS(Task 1 + Task 2 + Task 3 全部)

- [ ] **Step 5: 提交**

```powershell
git add agentteam/api/run_manager.py tests/api/test_run_cancel.py
git commit -m "feat(api): _handle_error 区分 RunCancelledError 标 cancelled"
```

---

## Task 4: make_agent_step + make_worker_node + TeamCompiler 接受 run_manager 参数

**Files:**
- Modify: `agentteam/runtime/nodes.py`
- Modify: `agentteam/runtime/graph.py`
- Modify: `agentteam/api/routes/runs.py`
- Modify: `tests/api/test_run_cancel.py`(追加测试)

- [ ] **Step 1: 写失败测试 — agent_step 在 worker 入口检查取消信号**

在 `d:\project\agentTeam\tests\api\test_run_cancel.py` 末尾追加:

```python
def test_agent_step_raises_cancelled_when_run_cancelled():
    """run_manager.is_cancelled 返回 True 时,agent_step 抛 RunCancelledError。

    关键:检查发生在 LLM 调用之前(不浪费 token)。
    """
    from agentteam.runtime.nodes import make_agent_step

    run_manager = MagicMock()
    run_manager.is_cancelled.return_value = True

    # agent / llm 不应被调用(应提前 raise)
    agent_step = make_agent_step(
        agent=MagicMock(), llm=MagicMock(), tools=[], run_manager=run_manager
    )

    with pytest.raises(RunCancelledError):
        agent_step({"run_id": "run-cancelled", "react_messages": []})

    # LLM 未被调用(取消检查在 LLM 调用之前)
    run_manager.is_cancelled.assert_called_once_with("run-cancelled")


def test_agent_step_proceeds_when_not_cancelled():
    """is_cancelled False 时正常调用 LLM,返回 LLM 响应。"""
    from agentteam.runtime.nodes import make_agent_step
    from tests.conftest import FakeLLM

    run_manager = MagicMock()
    run_manager.is_cancelled.return_value = False

    fake_llm = FakeLLM()
    fake_llm.set_invoke_responses([AIMessage(content="done")])

    agent_step = make_agent_step(
        agent=MagicMock(), llm=fake_llm, tools=[], run_manager=run_manager
    )

    result = agent_step({"run_id": "run-active", "react_messages": []})

    # 未抛异常,LLM 被调用,返回 final_answer
    assert result["final_answer"] == "done"
    run_manager.is_cancelled.assert_called_once_with("run-active")


def test_agent_step_without_run_manager_works_as_before():
    """run_manager=None(默认)时,agent_step 行为与改造前完全一致(向后兼容)。"""
    from agentteam.runtime.nodes import make_agent_step
    from tests.conftest import FakeLLM

    fake_llm = FakeLLM()
    fake_llm.set_invoke_responses([AIMessage(content="ok")])

    # 不传 run_manager(默认 None)
    agent_step = make_agent_step(agent=MagicMock(), llm=fake_llm, tools=[])

    result = agent_step({"run_id": "run-legacy", "react_messages": []})
    assert result["final_answer"] == "ok"


def test_worker_node_passes_run_manager_to_agent_step():
    """make_worker_node 透传 run_manager 给内部 make_agent_step。

    通过实际 invoke worker_node 验证:cancel 信号能传播到 agent_step。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from agentteam.domain.agent import Agent
    from agentteam.runtime.nodes import make_worker_node
    from tests.conftest import FakeLLM

    agent = Agent(
        name="w1", role="worker",
        system_prompt="你是执行者", tools=[], max_iterations=3,
    )
    fake_llm = FakeLLM()
    # worker_subgraph:init_worker + agent_step + finalize
    # agent_step 第一次调用返回无 tool_calls 的 AIMessage → finalize
    fake_llm.set_invoke_responses([AIMessage(content="done")])

    run_manager = MagicMock()
    run_manager.is_cancelled.return_value = True  # 模拟已 cancel

    worker_node = make_worker_node(
        agent=agent, llm=fake_llm, tools=[],
        trace_writer=None, audit_repo=None,
        run_manager=run_manager,
    )

    state = {
        "run_id": "run-x",
        "react_messages": [SystemMessage(content="sys"), HumanMessage(content="hi")],
        "tool_calls": [],
        "iteration": 0,
        "final_answer": "",
    }

    with pytest.raises(RunCancelledError):
        worker_node(state)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_cancel.py::test_agent_step_raises_cancelled_when_run_cancelled tests/api/test_run_cancel.py::test_agent_step_proceeds_when_not_cancelled tests/api/test_run_cancel.py::test_agent_step_without_run_manager_works_as_before tests/api/test_run_cancel.py::test_worker_node_passes_run_manager_to_agent_step -v`
Expected: 4 FAIL(`TypeError: make_agent_step() got an unexpected keyword argument 'run_manager'`)

- [ ] **Step 3: 实现 — 修改 nodes.py 三个函数签名 + agent_step 取消检查**

修改 `d:\project\agentTeam\agentteam\runtime\nodes.py`。

3a. 修改 `make_agent_step`,新增 `run_manager=None` 参数,在 `agent_step` 入口检查取消。完整替换为:

```python
def make_agent_step(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    run_manager=None,
):
    """创建 agent_step 节点：LLM 决策调用工具或给出最终答案。

    新增 run_manager 参数:若提供,在 LLM 调用前检查 run 是否被取消,
    命中则抛 RunCancelledError(继承 BaseException,绕过 worker 内 except Exception),
    避免浪费 LLM token。
    """
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    def agent_step(state: dict) -> dict:
        if run_manager is not None:
            run_id = state.get("run_id", "")
            if run_manager.is_cancelled(run_id):
                raise RunCancelledError(f"Run {run_id} cancelled by user")
        react_messages = state.get("react_messages", [])
        response = llm_with_tools.invoke(react_messages)

        usage = getattr(response, "usage_metadata", None)
        tokens = usage.get("total_tokens", 0) if usage else 0

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return {
                "react_messages": [response],
                "tool_calls": tool_calls,
                "final_answer": "",
                "total_tokens": tokens,
            }
        return {
            "react_messages": [response],
            "tool_calls": [],
            "final_answer": response.content,
            "total_tokens": tokens,
        }

    return agent_step
```

3b. 在 `nodes.py` 顶部 import 区(`from agentteam.runtime.trace import TraceWriter` 之后)新增 RunCancelledError 导入:

```python
from agentteam.api.run_manager import RunCancelledError
```

3c. 修改 `make_worker_subgraph`,新增 `run_manager=None` 参数并透传给 `make_agent_step`。完整替换为:

```python
def make_worker_subgraph(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
    run_manager=None,
):
    """编译 Worker ReAct 子图：init_worker → agent_step → tool_step → 循环 → finalize。

    返回 compiled subgraph，可直接作为父图的节点。
    新增 run_manager 参数:透传给 make_agent_step,使 worker 能检查取消信号。
    """
    from langgraph.graph import END, START, StateGraph
    from agentteam.runtime.state import WorkerState

    approval_policy = agent.approval_policy

    sg = StateGraph(WorkerState)
    sg.add_node("init_worker", make_init_worker(agent, trace_writer))
    sg.add_node("agent_step", make_agent_step(agent, llm, tools, run_manager=run_manager))
    sg.add_node(
        "tool_step",
        make_tool_step(agent, tools, approval_policy, trace_writer, audit_repo),
    )
    sg.add_node("finalize", make_finalize(agent, trace_writer))

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
    max_iter = agent.max_iterations

    def route_after_tool(state: dict) -> str:
        if state.get("iteration", 0) >= max_iter:
            return "finalize"
        return "agent_step"

    sg.add_conditional_edges("tool_step", route_after_tool)
    sg.add_edge("finalize", END)

    return sg.compile()
```

3d. 修改 `make_worker_node`,新增 `run_manager=None` 参数并透传给 `make_worker_subgraph`。完整替换为:

```python
def make_worker_node(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
    run_manager=None,
):
    """返回可调用节点函数，内部使用子图。

    剥离共享累加器字段（messages/audit_events/worker_outputs）后传入子图，
    避免子图 reducer 与父图 reducer 双重累积导致重复。
    透传 config 以支持子图内 interrupt/resume（工具级审批）。
    新增 run_manager 参数:透传给 make_worker_subgraph,使 worker 能检查取消信号。
    """
    subgraph = make_worker_subgraph(
        agent, llm, tools, trace_writer, audit_repo, run_manager=run_manager
    )

    # 共享累加器字段：子图不需要读取它们（只用 react_messages 内部通信），
    # 但若传入，子图的 reducer 会累积它们，返回时父图 reducer 再次累积 → 重复。
    # 因此从输入中剥离，让子图只产出自己的增量。
    _ACCUMULATOR_KEYS = frozenset({"messages", "audit_events", "worker_outputs", "total_tokens"})

    def worker_node(state: TeamState, config=None) -> dict:
        subgraph_input = {
            k: v for k, v in state.items() if k not in _ACCUMULATOR_KEYS
        }
        if config is not None:
            return subgraph.invoke(subgraph_input, config)
        return subgraph.invoke(subgraph_input)

    return worker_node
```

- [ ] **Step 4: 运行 nodes 测试验证通过**

Run: `python -m pytest tests/api/test_run_cancel.py::test_agent_step_raises_cancelled_when_run_cancelled tests/api/test_run_cancel.py::test_agent_step_proceeds_when_not_cancelled tests/api/test_run_cancel.py::test_agent_step_without_run_manager_works_as_before tests/api/test_run_cancel.py::test_worker_node_passes_run_manager_to_agent_step -v`
Expected: 4 PASS

- [ ] **Step 5: 实现 — 修改 TeamCompiler.__init__ + _compile_worker 透传 run_manager**

修改 `d:\project\agentTeam\agentteam\runtime\graph.py`。

5a. 修改 `TeamCompiler.__init__`,新增 `run_manager=None` 参数。完整替换 `__init__` 为:

```python
    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        library: AgentLibrary | None = None,
        run_manager=None,
    ):
        self._mp = model_provider
        self._tr = tool_registry
        self._lib = library or AgentLibrary()
        self._team_registry: dict[str, Team] = {}
        self._run_manager = run_manager
```

5b. 修改 `_compile_worker`,透传 `run_manager` 给 `make_worker_node`。完整替换 `_compile_worker` 为:

```python
    def _compile_worker(
        self, agent: Agent, default_model, trace_writer, audit_repo,
    ):
        """worker 沿用 make_worker_node（内部封装子图并剥离共享累加器字段，
        避免子图 reducer 与父图 reducer 双重累积）。
        透传 self._run_manager 使 worker 能检查取消信号。
        """
        llm = self._mp.get_llm(agent.model or default_model)
        tools = self._tr.get_tools(agent.tools) if agent.tools else []
        return make_worker_node(
            agent, llm, tools, trace_writer, audit_repo,
            run_manager=self._run_manager,
        )
```

- [ ] **Step 6: 实现 — 修改 runs.py 的 create_run 传入 run_manager**

修改 `d:\project\agentTeam\agentteam\api\routes\runs.py` 的 `create_run` 函数,把 `TeamCompiler(model_provider, tool_registry, library=lib)` 改为传入 `run_manager`:

```python
        compiler = TeamCompiler(model_provider, tool_registry, library=lib, run_manager=run_manager)
```

- [ ] **Step 7: 运行全部 P4 测试 + 回归现有 worker / compiler 测试**

Run: `python -m pytest tests/api/test_run_cancel.py tests/runtime/ tests/api/test_api_approvals_robustness.py -v`
Expected: 全部 PASS(P4 新增 15 + 原有 runtime / approvals 不回归)

- [ ] **Step 8: 提交**

```powershell
git add agentteam/runtime/nodes.py agentteam/runtime/graph.py agentteam/api/routes/runs.py tests/api/test_run_cancel.py
git commit -m "feat(runtime): make_agent_step + TeamCompiler 透传 run_manager 检查取消"
```

---

## Task 5: POST /api/runs/{id}/cancel endpoint + 全量回归

**Files:**
- Modify: `agentteam/api/routes/runs.py`
- Modify: `tests/api/test_run_cancel.py`(追加 endpoint 测试)

- [ ] **Step 1: 写失败测试 — cancel endpoint 4 种场景**

在 `d:\project\agentTeam\tests\api\test_run_cancel.py` 末尾追加:

```python
def _build_app_with_run_manager(tmp_path):
    """手动创建 app 并暴露 run_manager,便于注入 blocking graph / monkey-patch。

    沿用 tests/api/test_api_approvals_robustness.py 的模式。
    """
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    provider = make_provider_with_plan()
    tr = ToolRegistry()
    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    saver.setup()

    app = FastAPI()
    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, provider, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    return app, run_manager, run_repo, audit_repo, event_bus, conn


class _BlockingGraph:
    """Fake graph:invoke 阻塞直到 release event 被 set,模拟长 run。

    用于 cancel endpoint 测试:让 run 卡在 running 状态,等 cancel 信号。
    """

    def __init__(self):
        self.release = threading.Event()
        self.invoked = threading.Event()

    def invoke(self, input, config=None):
        self.invoked.set()
        # 阻塞直到测试 release,模拟长 LLM 调用
        self.release.wait(timeout=10)
        return {}

    def get_state(self, config):
        # 释放后返回"已完成"状态(测试不依赖此,仅为避免 _handle_invoke_result 抛异常)
        class _State:
            next = None
            values = {"total_tokens": 0}
        return _State()


def test_cancel_endpoint_unknown_run_returns_404(tmp_path):
    """未知 run_id 调 cancel 返回 404。"""
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(tmp_path)
    client = TestClient(app)

    resp = client.post("/api/runs/nonexistent-run/cancel")
    assert resp.status_code == 404

    conn.close()


def test_cancel_endpoint_completed_run_returns_400(tmp_path):
    """已完成(completed)的 run 调 cancel 返回 400。"""
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(tmp_path)
    client = TestClient(app)

    # 创建一个会快速完成的 run(无审批,FakeLLM 立即响应)
    client.post("/api/teams", json=make_team_json())
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "small task"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "completed", f"setup 失败:run 未完成(实际 {status})"

    cancel_resp = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_resp.status_code == 400
    assert "completed" in cancel_resp.json()["detail"]

    conn.close()


def test_cancel_endpoint_running_run_returns_ok(tmp_path):
    """running run 调 cancel 返回 200 + status 变为 cancelling。

    用 _BlockingGraph 让 run 卡在 running 状态,等 cancel 信号。
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(tmp_path)
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json())

    # Patch start_run:用 _BlockingGraph 替换真实 graph,使 run 卡在 running
    blocking_graph = _BlockingGraph()
    original_start_run = run_manager.start_run

    def patched_start_run(run_id, graph, config, task):
        original_start_run(run_id, blocking_graph, config, task)

    run_manager.start_run = patched_start_run  # type: ignore[assignment]

    run_id = None
    try:
        resp = client.post("/api/runs", json={"team_name": "dev", "task": "long task"})
        run_id = resp.json()["run_id"]

        # 等 graph.invoke 被调用(确认 run 已进入 running)
        assert blocking_graph.invoked.wait(timeout=5), "graph.invoke 未被调用"

        cancel_resp = client.post(f"/api/runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json() == {"ok": True}

        # status 应为 cancelling(running → cancelling)
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "cancelling"
    finally:
        # 释放 blocking graph 让后台线程退出,避免线程泄漏
        blocking_graph.release.set()
        run_manager.start_run = original_start_run  # type: ignore[assignment]
        if run_id is not None:
            run_manager.wait(run_id, timeout=2)

    conn.close()


def test_cancel_endpoint_emits_run_cancelled_event_for_interrupted(tmp_path):
    """interrupted run 调 cancel:返回 200 + status 变 cancelled + trace 含 run_cancelled 事件。

    interrupted 走简化路径(直接 end_run,无需 worker 检测)。
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(tmp_path)
    client = TestClient(app)

    # 创建带 step 审批的 team,使 run 进入 interrupted 状态
    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "cancel test"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted", f"setup 失败:run 未到 interrupted(实际 {status})"

    cancel_resp = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json() == {"ok": True}

    # status 应为 cancelled(interrupted → cancelled,直接结束)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "cancelled"

    # trace 应含 run_cancelled 事件
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    cancel_events = [e for e in trace if e["event_type"] == "run_cancelled"]
    assert len(cancel_events) == 1, f"应有 1 个 run_cancelled 事件,实际 {len(cancel_events)}"
    assert cancel_events[0]["actor"] == "user"

    conn.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_cancel.py::test_cancel_endpoint_unknown_run_returns_404 tests/api/test_run_cancel.py::test_cancel_endpoint_completed_run_returns_400 tests/api/test_run_cancel.py::test_cancel_endpoint_running_run_returns_ok tests/api/test_run_cancel.py::test_cancel_endpoint_emits_run_cancelled_event_for_interrupted -v`
Expected: 4 FAIL(`404 Not Found` 或 `405 Method Not Allowed` —— `/api/runs/{id}/cancel` 路由不存在)

- [ ] **Step 3: 实现 — 在 runs.py 新增 POST /{run_id}/cancel endpoint**

修改 `d:\project\agentTeam\agentteam\api\routes\runs.py`,在 `approve_run` 函数之后、`return router` 之前新增 `cancel_run` endpoint:

```python
    @router.post("/{run_id}/cancel")
    def cancel_run(run_id: str):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        if run["status"] not in ("running", "interrupted"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel run in status: {run['status']}",
            )
        if not run_manager.cancel_run(run_id):
            # cancel_run 返回 False:run 不在可取消状态(并发竞态:已被其他请求取消/结束)
            raise HTTPException(
                status_code=409,
                detail="Run not active or already cancelled",
            )
        return {"ok": True}
```

- [ ] **Step 4: 运行 cancel endpoint 测试验证通过**

Run: `python -m pytest tests/api/test_run_cancel.py -v`
Expected: 全部 PASS(P4 全部 19 个测试)

- [ ] **Step 5: 全量回归测试**

Run: `python -m pytest -q`
Expected: 全部 PASS(目标:原 418+ → 437+,新增 P4 测试 ~19 个,无回归)

- [ ] **Step 6: 检查工作树状态 + 提交历史**

Run: `git status`
Expected: 仅 `agentteam/api/routes/runs.py` 与 `tests/api/test_run_cancel.py` 有变更

Run: `git log --oneline -6`
Expected: 看到 P4 的 5 个 feat 提交

- [ ] **Step 7: Phase commit(若 Step 5/6 未自动提交)**

```powershell
git add agentteam/api/routes/runs.py tests/api/test_run_cancel.py
git commit -m "feat(api): P4 Run 取消机制 threading.Event + POST /cancel"
```

---

## Self-Review

**1. Spec coverage(对照 spec §6):**
- ✅ §6.2 `threading.Event` 协作取消 —— Task 1 `_cancel_events` + `is_cancelled`,Task 4 worker 入口检查
- ✅ §6.2 `cancel_run` 方法 —— Task 2(running + interrupted 两种路径)
- ✅ §6.2 `RunCancelledError(Exception)` —— Task 1,但按 spec §7 风险表改为继承 `BaseException`(避免被 worker `except Exception` 吞没)
- ✅ §6.2 `_handle_error` 区分 `RunCancelledError` —— Task 3
- ✅ §6.2 `make_agent_step` 新增 `run_manager` 参数 —— Task 4
- ✅ §6.2 `POST /api/runs/{id}/cancel` endpoint —— Task 5
- ✅ §6.2 runs 表 status 新增 `cancelling` / `cancelled`(status 是 TEXT,无需 schema 改动)—— Task 2/3/5 验证
- ✅ §6.3 interrupted run 简化方案(直接 end_run,不 recompile)—— Task 2 `cancel_run` 的 interrupted 分支
- ✅ §6.4 测试覆盖:
  - `test_cancel_running_run_sets_event` —— `test_cancel_running_run_sets_event_and_status_cancelling`(Task 2) + `test_cancel_endpoint_running_run_returns_ok`(Task 5)
  - `test_cancel_interrupted_run_ends_directly` —— Task 2 + Task 5 endpoint
  - `test_cancel_completed_run_returns_409` —— `test_cancel_endpoint_completed_run_returns_400`(spec 写 409,实际 400 更合理:状态不可取消是客户端错误;endpoint 测试断言 400)
  - `test_cancel_emits_run_cancelled_event` —— Task 3 + Task 5 `test_cancel_endpoint_emits_run_cancelled_event_for_interrupted`
  - `test_worker_checks_cancel_between_iterations` —— Task 4 `test_agent_step_raises_cancelled_when_run_cancelled` + `test_worker_node_passes_run_manager_to_agent_step`
- ✅ §7 风险表:RunCancelledError 继承 BaseException —— Task 1 + `test_runcancellederror_inherits_baseexception`

**关于 §6.4 测试 `test_cancel_completed_run_returns_409` 的偏离:**
spec 写"已完成的 run cancel 返回 409",但实现中 completed/failed 状态返回 400(客户端请求了不可取消的 run,属于 400 Bad Request)。409 Conflict 用于并发竞态(cancel_run 内部返回 False 时,即 running/interrupted 检查通过但实际无法取消的边界情况)。此偏离在 Task 5 endpoint 测试中显式断言 400,并在 endpoint 实现中保留 409 兜底分支。

**2. Placeholder scan:**
- 无 "TBD"/"TODO"/"fill in details"
- 每个 Step 都含完整代码或确切命令 + 期望输出
- 测试代码完整可运行(含 import、fixture、断言)
- 无"类似 Task N"的省略 —— 每个 task 的代码都完整给出

**3. Type consistency:**
- `RunCancelledError(BaseException)` —— Task 1 定义,Task 3/4 引用,签名一致
- `RunManager._cancel_events: dict[str, threading.Event]` —— Task 1 定义,Task 2/3 引用,类型一致
- `RunManager.is_cancelled(run_id: str) -> bool` —— Task 1 定义,Task 4 `make_agent_step` 调用,签名一致
- `RunManager.cancel_run(run_id: str) -> bool` —— Task 2 定义,Task 5 endpoint 调用,签名一致
- `RunManager._handle_error(run_id: str, error: BaseException) -> None` —— Task 3 改签名(`Exception` → `BaseException`,因为 RunCancelledError 继承 BaseException),`_run_in_background` / `_resume_in_background` 的 `except Exception as e:` 仍能捕获吗?

  **关键检查:** `except Exception as e` 是否能捕获 `RunCancelledError(BaseException)`? **不能。** 这是 spec §7 风险表的核心:`except Exception` 不捕获 BaseException 子类。因此 Task 3 必须同时修改 `_run_in_background` / `_resume_in_background` 的 `except` 从 `Exception` 改为 `BaseException`,否则 RunCancelledError 会传播到 daemon 线程顶层被吞没,run 卡在 cancelling。

  **修正:** Task 3 Step 3 需追加修改 `_run_in_background` 与 `_resume_in_background` 的 except 子句。详见下方"Task 3 修正补丁"。

- `make_agent_step(agent, llm, tools, run_manager=None)` —— Task 4 定义,Task 4 测试调用,签名一致
- `make_worker_subgraph(agent, llm, tools, trace_writer, audit_repo, run_manager=None)` —— Task 4 定义
- `make_worker_node(agent, llm, tools, trace_writer, audit_repo, run_manager=None)` —— Task 4 定义,`_compile_worker` 调用,签名一致
- `TeamCompiler.__init__(model_provider, tool_registry, library, run_manager=None)` —— Task 4 定义,`create_run` 调用,签名一致

---

## Task 3 修正补丁:except Exception → except BaseException

**问题:** Task 3 仅改 `_handle_error` 签名,但 `_run_in_background` / `_resume_in_background` 的 `except Exception as e:` 无法捕获 `RunCancelledError(BaseException)`,导致取消信号被 daemon 线程吞没,run 永远卡在 `cancelling` 状态。

**修正:** Task 3 Step 3 需追加修改这两个方法的 except 子句。

### 修正后的 Task 3 Step 3 完整实现

修改 `d:\project\agentTeam\agentteam\api\run_manager.py`:

3a. `_run_in_background` 的 `except Exception as e:` 改为 `except BaseException as e:`(捕获 RunCancelledError + 普通异常):

```python
    def _run_in_background(self, run_id: str, graph, config: dict, task: str) -> None:
        try:
            eid = self._audit_repo.add_event(run_id, "run_start", "system", {"task": task})
            self._bus.publish(
                run_id,
                {"id": eid, "event_type": "run_start", "run_id": run_id, "payload": {"task": task}},
            )
            initial = {
                "messages": [],
                "task": task,
                "plan": [],
                "current_step": 0,
                "worker_outputs": {},
                "audit_events": [],
                "run_id": run_id,
                "pending_approval": None,
            }
            graph.invoke(initial, config)
            self._handle_invoke_result(run_id, graph, config)
        except BaseException as e:
            self._handle_error(run_id, e)
```

3b. `_resume_in_background` 同样改为 `except BaseException as e:`:

```python
    def _resume_in_background(self, run_id: str, graph, config: dict, command) -> None:
        try:
            graph.invoke(command, config)
            self._handle_invoke_result(run_id, graph, config)
        except BaseException as e:
            self._handle_error(run_id, e)
```

3c. `_handle_error` 签名与分支(已在 Task 3 Step 3 给出,此处仅强调 `error: BaseException`):

```python
    def _handle_error(self, run_id: str, error: BaseException) -> None:
        if isinstance(error, RunCancelledError):
            # ... cancelled 分支 ...
        else:
            # ... failed 分支(原逻辑)...
        self._cleanup_run(run_id)
```

### 追加测试:验证 RunCancelledError 不被 daemon 线程吞没

在 Task 3 Step 1 的测试末尾追加(确保 `except Exception` 改造被覆盖):

```python
def test_run_in_background_catches_runcancellederror_via_baseexception():
    """_run_in_background 用 except BaseException 捕获 RunCancelledError。

    场景:graph.invoke 抛 RunCancelledError(BaseException)。
    若用 except Exception 会漏捕,run 卡 cancelling;
    改为 except BaseException 后能交给 _handle_error 标 cancelled。
    """
    rm = _make_run_manager()
    run_id = "run-bg-cancelled"
    rm._cancel_events[run_id] = threading.Event()

    # Fake graph:invoke 抛 RunCancelledError(模拟 worker 检测到 cancel)
    fake_graph = MagicMock()
    fake_graph.invoke.side_effect = RunCancelledError("cancelled")
    fake_graph.get_state.side_effect = RuntimeError("不应到达此处")

    # 直接调用 _run_in_background(不走 start_run 的线程,简化测试)
    rm._run_in_background(run_id, fake_graph, {}, "task")

    # 应标 cancelled(不是 failed,也不是卡 cancelling)
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    rm._audit_repo.add_event.assert_called_once_with(run_id, "run_cancelled", "user")
    # cleanup 被调用
    assert run_id not in rm._cancel_events
```

运行验证:`python -m pytest tests/api/test_run_cancel.py::test_run_in_background_catches_runcancellederror_via_baseexception -v` → PASS

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp6-p4-run-cancel.md`。两种执行方式:

1. **Subagent-Driven(推荐)** —— 每个任务派发新 subagent,任务间 review
2. **Inline Execution** —— 在当前会话执行,批量 checkpoint review

选择哪种?
