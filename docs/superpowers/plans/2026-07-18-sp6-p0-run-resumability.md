# SP6-P0 Run 可恢复性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Run 可恢复性 — 服务重启后 `RunManager._graphs`/`_configs` 内存丢失时,`approve_run` 通过 lazy recompile(从 `team_store` 取 Team + 重新 compile graph + 从 SqliteSaver checkpoint 续跑)让 interrupted run 恢复执行,不再永久卡死。

**Architecture:** `RunManager` 注入 `checkpointer` 并新增 `has_graph()` / `recompile_and_resume()` 方法;`approve_run` 路由在 `try_claim` 后分支:graph 存在走原 `resume_run` fast path,graph 缺失走 lazy recompile 路径(从 `run["team_name"]` 取 Team,通过 `compiler_factory` 闭包构造 `TeamCompiler` + 注册所有 team + compile + resume)。`_build_compiler` 抽为模块级 helper 避免循环依赖。

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, SqliteSaver (checkpoint 持久化), pytest, threading

---

## 文件结构

| 文件 | 责任 | 动作 | 所属 Task |
|------|------|------|-----------|
| `agentteam/api/run_manager.py` | RunManager +checkpointer +has_graph +recompile_and_resume | 修改 | Task 1, 2 |
| `agentteam/api/server.py` | create_app 注入 checkpointer 到 RunManager | 修改 | Task 1 |
| `agentteam/api/routes/runs.py` | approve_run lazy recompile 路径 + _build_compiler helper | 修改 | Task 3 |
| `tests/api/test_run_resumability.py` | has_graph / recompile / lazy recompile / fast path 测试 | 新建 | Task 1-4 |
| `tests/api/test_api_approvals.py` | 删除被 P0 淘汰的 test_approve_after_graph_lost_marks_run_failed | 修改 | Task 3 |

---

## Task 1: RunManager 注入 checkpointer + 新增 has_graph 方法

**Files:**
- Modify: `agentteam/api/run_manager.py` (`__init__` 加 checkpointer 参数 + 新增 `has_graph` 方法)
- Modify: `agentteam/api/server.py` (create_app 传 checkpointer 给 RunManager)
- Create: `tests/api/test_run_resumability.py`

- [ ] **Step 1: 写失败测试 — has_graph 行为**

创建 `d:\project\agentTeam\tests\api\test_run_resumability.py`:

```python
"""SP6-P0 Run 可恢复性测试。

覆盖:
- has_graph: 判断 run_id 是否有内存态 graph(Task 1)
- recompile_and_resume: lazy recompile + resume(Task 2)
- approve_run lazy recompile 路径(Task 3)
- approve_run fast path 不变(Task 4)
"""
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _build_app_with_run_manager(tmp_path):
    """手动创建 app 并暴露 run_manager,便于模拟重启/注入 mock。

    与 test_api_approvals_robustness.py 中的同名 helper 同构,
    但 RunManager 注入了 checkpointer(P0 新增),使 lazy recompile 可用。
    """
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    saver.setup()
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=saver)
    provider = make_provider_with_plan()
    tr = ToolRegistry()

    app = FastAPI()
    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, provider, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    return app, run_manager, run_repo, audit_repo, event_bus, conn


# ===== Task 1: has_graph =====


def test_has_graph_returns_true_after_start_run(tmp_path):
    """start_run 后 has_graph(run_id) 应返回 True(graph 已注入内存)。"""
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)

    run_id = run_repo.create_run("test_team", "test task")

    # patch _run_in_background 为 no-op,避免后台线程 cleanup _graphs
    # (start_run 同步注入 _graphs[run_id],后台线程异步可能 cleanup)
    with patch.object(run_manager, "_run_in_background", lambda *a, **k: None):
        run_manager.start_run(
            run_id, MagicMock(name="graph"),
            {"configurable": {"thread_id": run_id}}, "test task",
        )
        assert run_manager.has_graph(run_id) is True

    # 等后台 no-op 线程结束,避免泄漏
    run_manager.wait(run_id, timeout=5)
    conn.close()


def test_has_graph_returns_false_for_unknown_run(tmp_path):
    """未启动的 run_id,has_graph 返回 False。"""
    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)

    assert run_manager.has_graph("nonexistent-run-id") is False
    conn.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_resumability.py::test_has_graph_returns_true_after_start_run tests/api/test_run_resumability.py::test_has_graph_returns_false_for_unknown_run -v`

Expected: 2 个测试 FAIL(`TypeError: RunManager.__init__() got an unexpected keyword argument 'checkpointer'` 或 `AttributeError: 'RunManager' object has no attribute 'has_graph'`)

- [ ] **Step 3: 实现 — 修改 RunManager.__init__ 加 checkpointer + 新增 has_graph**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py`。

3a. 修改顶部 import(加 `Callable` 类型 hint 支持):

```python
"""RunManager：后台线程执行 LangGraph graph + interrupt/resume。"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

from agentteam.api.events import BroadcastTraceWriter, EventBus
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo

if TYPE_CHECKING:
    from agentteam.domain.team import Team
    from agentteam.runtime.graph import TeamCompiler
```

3b. 修改 `__init__` 加 `checkpointer` 参数,并新增 `has_graph` 方法。将原 `__init__`:

```python
    def __init__(self, run_repo: RunRepo, audit_repo: AuditRepo, event_bus: EventBus) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
```

替换为:

```python
    def __init__(
        self,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        event_bus: EventBus,
        checkpointer=None,
    ) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._saver = checkpointer
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def has_graph(self, run_id: str) -> bool:
        """返回 run_id 是否有内存态 graph。

        供 approve_run 判断走 fast path(resume_run)还是 lazy recompile 路径。
        服务重启后 _graphs 清空,has_graph 返回 False → 触发 recompile。
        """
        return run_id in self._graphs
```

- [ ] **Step 4: 实现 — 修改 server.py 注入 checkpointer 到 RunManager**

修改 `d:\project\agentTeam\agentteam\api\server.py`。将原 `create_app` 中的:

```python
    run_manager = RunManager(run_repo, audit_repo, event_bus)
```

替换为:

```python
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=saver)
```

**注意:** `saver` 在原代码中于 `run_manager` 之后创建(`saver = SqliteSaver(conn)`),需把 `saver` 的创建移到 `run_manager` 之前。将原代码块:

```python
    team_store = TeamStore(repo=team_repo)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary(repo=library_repo)

    saver = SqliteSaver(conn)
    saver.lock = conn_lock  # 让 SqliteSaver 也用同一把锁
    assert saver.lock is conn_lock  # 防御：若 langgraph 改名 lock 属性则静默失效
    saver.setup()
```

替换为:

```python
    team_store = TeamStore(repo=team_repo)
    event_bus = EventBus()

    saver = SqliteSaver(conn)
    saver.lock = conn_lock  # 让 SqliteSaver 也用同一把锁
    assert saver.lock is conn_lock  # 防御：若 langgraph 改名 lock 属性则静默失效
    saver.setup()

    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=saver)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary(repo=library_repo)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_resumability.py::test_has_graph_returns_true_after_start_run tests/api/test_run_resumability.py::test_has_graph_returns_false_for_unknown_run -v`

Expected: 2 PASS

- [ ] **Step 6: 回归验证 — 现有 approve 测试不破坏**

Run: `python -m pytest tests/api/test_api_approvals.py tests/api/test_api_approvals_robustness.py -v`

Expected: 全部 PASS(checkpointer 注入是纯加法,不改原 resume_run 行为)

- [ ] **Step 7: 提交**

```powershell
git add agentteam/api/run_manager.py agentteam/api/server.py tests/api/test_run_resumability.py
git commit -m "feat(api): RunManager 注入 checkpointer + has_graph 方法"
```

---

## Task 2: 新增 recompile_and_resume 方法

**Files:**
- Modify: `agentteam/api/run_manager.py` (新增 `recompile_and_resume` 方法)
- Modify: `tests/api/test_run_resumability.py` (新增 2 个测试)

- [ ] **Step 1: 写失败测试 — recompile_and_resume 行为**

在 `d:\project\agentTeam\tests\api\test_run_resumability.py` 末尾追加:

```python
# ===== Task 2: recompile_and_resume =====


def test_recompile_and_resume_constructs_graph_and_resumes(tmp_path):
    """recompile_and_resume 调用 compiler_factory 构造 graph,注入 _graphs/_configs,然后调 resume_run。"""
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    my_saver = MagicMock(name="saver")
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=my_saver)

    run_id = run_repo.create_run("t", "task")
    team = Team(name="t", description="d", root=MagicMock(), default_model=ModelRef("qwen", "qwen-max"))

    fake_graph = MagicMock(name="graph")
    mock_compiler = MagicMock(name="compiler")
    mock_compiler.compile.return_value = fake_graph
    compiler_factory = MagicMock(return_value=mock_compiler)

    # mock resume_run 避免实际启线程(单元测试聚焦 recompile 逻辑)
    with patch.object(run_manager, "resume_run") as mock_resume:
        run_manager.recompile_and_resume(
            run_id, team, compiler_factory, approved=True, reason="ok",
        )

    # compiler_factory 被调用一次
    compiler_factory.assert_called_once()
    # compile 被调用,graph 注入内存
    mock_compiler.compile.assert_called_once()
    assert run_manager._graphs[run_id] is fake_graph
    assert run_manager._configs[run_id] == {"configurable": {"thread_id": run_id}}
    # resume_run 被调用,参数透传
    mock_resume.assert_called_once_with(run_id, True, "ok")
    conn.close()


def test_recompile_uses_correct_checkpointer(tmp_path):
    """recompile_and_resume 调用 compiler.compile 时传入 self._saver 作为 checkpointer。

    这是 lazy recompile 能从 checkpoint 续跑的关键 — 新 graph 必须持有原 saver。
    """
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    conn = init_db(tmp_path / "test.db")
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    event_bus = EventBus()
    my_saver = MagicMock(name="my_saver")
    run_manager = RunManager(run_repo, audit_repo, event_bus, checkpointer=my_saver)

    run_id = run_repo.create_run("t", "task")
    team = Team(name="t", description="d", root=MagicMock(), default_model=ModelRef("qwen", "qwen-max"))

    mock_compiler = MagicMock(name="compiler")
    mock_compiler.compile.return_value = MagicMock(name="graph")
    compiler_factory = MagicMock(return_value=mock_compiler)

    with patch.object(run_manager, "resume_run"):
        run_manager.recompile_and_resume(
            run_id, team, compiler_factory, approved=True,
        )

    # 验证 compile 调用时 checkpointer == my_saver(不是 None,不是新 saver)
    _, kwargs = mock_compiler.compile.call_args
    assert kwargs.get("checkpointer") is my_saver, (
        f"compile 应传入 self._saver 作为 checkpointer,实际: {kwargs.get('checkpointer')}"
    )
    conn.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_resumability.py::test_recompile_and_resume_constructs_graph_and_resumes tests/api/test_run_resumability.py::test_recompile_uses_correct_checkpointer -v`

Expected: 2 个测试 FAIL(`AttributeError: 'RunManager' object has no attribute 'recompile_and_resume'`)

- [ ] **Step 3: 实现 — 新增 recompile_and_resume 方法**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py`。在 `has_graph` 方法之后、`start_run` 方法之前,新增 `recompile_and_resume` 方法:

```python
    def recompile_and_resume(
        self,
        run_id: str,
        team: "Team",
        compiler_factory: "Callable[[], TeamCompiler]",
        approved: bool,
        reason: str | None = None,
    ) -> None:
        """lazy recompile: 用 compiler_factory 构造 graph,注入内存,再 resume。

        供 approve_run 在 _graphs 缺失时(如服务重启后)调用。
        SqliteSaver 的 checkpoint 已持久化 interrupt 状态,
        graph.invoke(Command(resume=...)) 能从 checkpoint 续跑。

        参数:
            run_id: run 标识(同时作为 thread_id)
            team: 要重新编译的 Team(从 team_store.get(run["team_name"]) 取得)
            compiler_factory: 无参闭包,返回注册好所有 team 的 TeamCompiler。
                              抽成闭包避免 RunManager 直接依赖 ModelProvider/ToolRegistry 等。
            approved / reason: 透传给 resume_run
        """
        compiler = compiler_factory()
        trace_writer = BroadcastTraceWriter(self._audit_repo, self._bus)
        graph = compiler.compile(
            team,
            checkpointer=self._saver,
            trace_writer=trace_writer,
            audit_repo=self._audit_repo,
        )
        config = {"configurable": {"thread_id": run_id}}
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
        self.resume_run(run_id, approved, reason)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_resumability.py::test_recompile_and_resume_constructs_graph_and_resumes tests/api/test_run_resumability.py::test_recompile_uses_correct_checkpointer -v`

Expected: 2 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/api/run_manager.py tests/api/test_run_resumability.py
git commit -m "feat(api): RunManager.recompile_and_resume lazy recompile 方法"
```

---

## Task 3: approve_run 路由改造 - lazy recompile 路径

**Files:**
- Modify: `agentteam/api/routes/runs.py` (新增 `_build_compiler` helper + 改造 `approve_run`)
- Modify: `tests/api/test_run_resumability.py` (新增 2 个集成测试)
- Modify: `tests/api/test_api_approvals.py` (删除被 P0 淘汰的旧测试)

- [ ] **Step 1: 写失败测试 — lazy recompile 集成路径**

在 `d:\project\agentTeam\tests\api\test_run_resumability.py` 末尾追加:

```python
# ===== Task 3: approve_run lazy recompile 路径 =====


def test_approve_after_restart_recompiles_and_resumes(tmp_path):
    """模拟服务重启(清空 _graphs/_configs/_threads),approve 应 lazy recompile + resume,run 最终完成。

    场景:
    1. 启动 run → interrupted(step 审批)
    2. 模拟重启:清空 RunManager 内存(_graphs/_configs/_threads)
    3. approve → 触发 lazy recompile(从 team_store 取 Team + 重新 compile + resume)
    4. SqliteSaver checkpoint 持久化 interrupt 状态,新 graph 能从 checkpoint 续跑
    5. run 最终 completed
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "restart recompile"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted", f"setup 失败:run 未到 interrupted(实际 {status})"

    # 模拟服务重启:清空 RunManager 内存状态
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()
    assert not run_manager.has_graph(run_id), "重启后 has_graph 应为 False"

    # approve 应触发 lazy recompile + resume,返回 200
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "ok"}
    )
    assert resp.status_code == 200, f"lazy recompile 后 approve 应成功,实际: {resp.status_code} {resp.text}"

    # run 应最终完成(checkpoint 续跑成功)
    status = _wait_for_run(client, run_id, timeout=15.0)
    assert status == "completed", (
        f"lazy recompile 后 run 应完成,实际: {status}"
    )

    conn.close()


def test_approve_after_restart_team_deleted_returns_409(tmp_path):
    """重启后 team 也被删除,approve 应返回 409 + run 标 failed(ValueError 路径)。"""
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "team deleted"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # 模拟重启 + team 被删
    with run_manager._lock:
        run_manager._graphs.clear()
        run_manager._configs.clear()
        run_manager._threads.clear()
    del_resp = client.delete("/api/teams/dev")
    assert del_resp.status_code == 200

    # approve:team 不存在 → ValueError → 409 + run failed
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 409
    assert "not found" in resp.json()["detail"].lower()

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed", (
        f"team 不存在时 run 应标 failed,实际: {run['status']}"
    )

    # 应有 error 事件
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert any(e["event_type"] == "error" for e in trace)

    conn.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_run_resumability.py::test_approve_after_restart_recompiles_and_resumes tests/api/test_run_resumability.py::test_approve_after_restart_team_deleted_returns_409 -v`

Expected:
- `test_approve_after_restart_recompiles_and_resumes` FAIL(approve 返回 409,因为当前 approve_run 走原 resume_run 路径,graph 丢失抛 ValueError → 409 + failed,run 不会完成)
- `test_approve_after_restart_team_deleted_returns_409` 可能 PASS(因为当前 approve 返回 409 + failed,恰好符合断言) — 这是巧合,Task 3 实现后仍应 PASS

- [ ] **Step 3: 实现 — 新增 _build_compiler 模块级 helper**

修改 `d:\project\agentTeam\agentteam\api\routes\runs.py`。在 `run_to_dict` 函数之后、`runs_router` 函数之前,新增模块级 helper:

```python
def _build_compiler(
    model_provider: ModelProvider,
    tool_registry: ToolRegistry,
    library: AgentLibrary,
    team_store: TeamStore,
) -> TeamCompiler:
    """构造 TeamCompiler 并注册所有已知 Team(供 TeamRef 解析)。

    抽成模块级 helper 供 approve_run 的 lazy recompile 路径调用,
    与 create_run 中的编译逻辑保持一致,避免循环依赖
    (RunManager 不直接依赖 ModelProvider/ToolRegistry)。
    """
    compiler = TeamCompiler(model_provider, tool_registry, library=library)
    for t in team_store.list_all():
        compiler.register_team(t)
    return compiler
```

- [ ] **Step 4: 实现 — 改造 approve_run 加 lazy recompile 分支**

修改 `d:\project\agentTeam\agentteam\api\routes\runs.py` 中的 `approve_run` 函数。将原实现:

```python
    @router.post("/{run_id}/approve")
    def approve_run(run_id: str, req: ApproveRequest):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        # 原子地 claim：仅当状态仍为 interrupted 时才置为 running，
        # 避免两个并发 approve 请求都通过 check-then-act 竞态。
        if not run_repo.try_claim(run_id, "interrupted", "running"):
            current = run_repo.get_run(run_id)
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' is not interrupted (status={current['status']})",
            )

        try:
            run_manager.resume_run(run_id, req.approved, req.reason)
        except Exception as e:
            # BUG-10 修复：原仅捕获 ValueError（服务重启后 graph/config 丢失），
            # 但 resume_run 内部抛非 ValueError（如 sqlite3.OperationalError 锁竞争、
            # RuntimeError）时不被捕获，异常传播到 FastAPI 返回 500。此时 try_claim
            # 已把状态置 running，但无后台线程执行，用户无法重新 approve（try_claim
            # 因状态非 interrupted 失败），run 永久卡死。
            # 修复：catch Exception，确保任何 resume_run 异常都回滚状态为 failed。
            # ValueError 仍是预期内的"graph 丢失"错误，返回 409 保持向后兼容；
            # 其他异常视为服务端错误，返回 500。
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e)}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e)},
                },
            )
            status_code = 409 if isinstance(e, ValueError) else 500
            raise HTTPException(status_code=status_code, detail=str(e))
        return {"ok": True}
```

替换为(完整改造后代码):

```python
    @router.post("/{run_id}/approve")
    def approve_run(run_id: str, req: ApproveRequest):
        run = run_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

        # 原子地 claim：仅当状态仍为 interrupted 时才置为 running，
        # 避免两个并发 approve 请求都通过 check-then-act 竞态。
        if not run_repo.try_claim(run_id, "interrupted", "running"):
            current = run_repo.get_run(run_id)
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' is not interrupted (status={current['status']})",
            )

        try:
            if run_manager.has_graph(run_id):
                # fast path: graph 仍在内存(正常流程),直接 resume
                run_manager.resume_run(run_id, req.approved, req.reason)
            else:
                # lazy recompile (P0): 服务重启后 _graphs/_configs 丢失,
                # 从 team_store 取 Team 重新 compile,再 resume。
                # SqliteSaver checkpoint 已持久化 interrupt 状态,
                # 新 graph 持有原 saver 即可从 checkpoint 续跑。
                team = team_store.get(run["team_name"])
                if team is None:
                    raise ValueError(
                        f"Team '{run['team_name']}' not found (needed for recompile)"
                    )
                run_manager.recompile_and_resume(
                    run_id, team,
                    compiler_factory=lambda: _build_compiler(
                        model_provider, tool_registry, lib, team_store,
                    ),
                    approved=req.approved, reason=req.reason,
                )
        except Exception as e:
            # BUG-10 修复(沿用):catch Exception 确保任何 resume/recompile 异常
            # 都回滚状态为 failed,避免 try_claim 已置 running 后无线程执行导致卡死。
            # ValueError(team 不存在等预期错误)返回 409;
            # 其他异常(锁竞争、compile 失败等)返回 500。
            run_repo.end_run(run_id, "failed")
            eid = audit_repo.add_event(
                run_id, "error", "system", {"error": str(e)}
            )
            event_bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "error",
                    "run_id": run_id,
                    "payload": {"error": str(e)},
                },
            )
            status_code = 409 if isinstance(e, ValueError) else 500
            raise HTTPException(status_code=status_code, detail=str(e))
        return {"ok": True}
```

- [ ] **Step 5: 删除被 P0 淘汰的旧测试**

`tests/api/test_api_approvals.py` 中的 `test_approve_after_graph_lost_marks_run_failed` 测试的是 P0 之前的旧行为(graph 丢失 → 409 + failed)。P0 后 graph 丢失会触发 lazy recompile → approve 成功,该测试会 FAIL。需删除。

修改 `d:\project\agentTeam\tests\api\test_api_approvals.py`:删除整个 `test_approve_after_graph_lost_marks_run_failed` 函数(从 `def test_approve_after_graph_lost_marks_run_failed(tmp_path):` 到该函数末尾 `assert any(e["event_type"] == "error" for e in trace)`)。

删除后 `tests/api/test_api_approvals.py` 保留前 5 个测试:
- `test_approve_resumes_interrupted_run`
- `test_reject_terminates_run`
- `test_approve_non_interrupted_run_returns_400`
- `test_approve_nonexistent_run_returns_404`
- `test_double_approve_second_returns_400`

同时清理因删除该函数而不再使用的 import(若删除后文件顶部 `import threading` / `from langgraph.checkpoint.sqlite import SqliteSaver` / `from agentteam.api.events import EventBus` 等仅被该函数使用的 import 变为未使用,一并删除)。具体:删除文件中 `test_approve_after_graph_lost_marks_run_failed` 函数体内的所有局部 import,以及文件顶部若仅因此函数才引入的 import。文件顶部 `from tests.api.conftest import (...)` 与 `from tests.conftest import FakeLLM, FakeModelProvider` 保留(其他测试仍用)。

- [ ] **Step 6: 运行新测试验证通过**

Run: `python -m pytest tests/api/test_run_resumability.py::test_approve_after_restart_recompiles_and_resumes tests/api/test_run_resumability.py::test_approve_after_restart_team_deleted_returns_409 -v`

Expected: 2 PASS

- [ ] **Step 7: 回归 — 现有 approve 测试(删除旧测试后)仍通过**

Run: `python -m pytest tests/api/test_api_approvals.py tests/api/test_api_approvals_robustness.py -v`

Expected: 全部 PASS(fast path 行为不变;`test_api_approvals_robustness.py::test_approve_run_value_error_still_returns_409` 仍 PASS — 该测试 mock resume_run 抛 ValueError,但 P0 后 fast path 中 resume_run 仍可能抛 ValueError,409 行为沿用)

**注意:** `test_approve_run_value_error_still_returns_409` 在 P0 后行为有变化 — 该测试清空 `_graphs` 后 approve,在 P0 后会走 lazy recompile 而非 ValueError。若该测试 FAIL,需将其调整为:mock `recompile_and_resume` 抛 ValueError(模拟 team 不存在),验证 409 + failed。若 PASS 则无需改动(可能因 team_store 也被清空或其他原因仍触发 ValueError)。运行后根据结果决定是否调整。

- [ ] **Step 8: 提交**

```powershell
git add agentteam/api/routes/runs.py tests/api/test_run_resumability.py tests/api/test_api_approvals.py
git commit -m "feat(api): approve_run lazy recompile on restart (P0 Run 可恢复性)"
```

---

## Task 4: fast path 不变 + 全量回归

**Files:**
- Modify: `tests/api/test_run_resumability.py` (新增 fast path 测试)
- 无源码改动(fast path 已在 Task 3 实现)

- [ ] **Step 1: 写测试 — fast path 不触发 recompile**

在 `d:\project\agentTeam\tests\api\test_run_resumability.py` 末尾追加:

```python
# ===== Task 4: fast path 不变 =====


def test_approve_with_graph_present_uses_fast_path(tmp_path):
    """graph 存在时(未重启),approve 走 fast path(resume_run),不触发 recompile_and_resume。

    保护性测试:确保 P0 改造不破坏正常流程(graph 在内存时不应走 recompile)。
    """
    app, run_manager, run_repo, audit_repo, event_bus, conn = _build_app_with_run_manager(
        tmp_path
    )
    client = TestClient(app)

    client.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client.post("/api/runs", json={"team_name": "dev", "task": "fast path"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # graph 仍存在(未重启)
    assert run_manager.has_graph(run_id) is True

    # mock recompile_and_resume 验证未被调用(fast path 应走 resume_run)
    with patch.object(run_manager, "recompile_and_resume") as mock_recompile:
        resp = client.post(
            f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "fast"}
        )
        assert resp.status_code == 200, f"fast path approve 应成功,实际: {resp.status_code}"
        mock_recompile.assert_not_called()

    # run 应正常完成(fast path resume_run 续跑)
    status = _wait_for_run(client, run_id, timeout=15.0)
    assert status == "completed", f"fast path 后 run 应完成,实际: {status}"

    conn.close()
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/api/test_run_resumability.py::test_approve_with_graph_present_uses_fast_path -v`

Expected: PASS(Task 3 已实现 fast path 分支,此测试验证不回归)

- [ ] **Step 3: 运行 P0 全部新测试**

Run: `python -m pytest tests/api/test_run_resumability.py -v`

Expected: 7 PASS(2 has_graph + 2 recompile + 2 lazy recompile + 1 fast path)

- [ ] **Step 4: 全量回归**

Run: `python -m pytest -q`

Expected: 全部 PASS(原 418 + P0 新增 7 - 删除 1 = 424+,无回归)

**注意:** 若 `tests/api/test_api_approvals_robustness.py::test_approve_run_value_error_still_returns_409` 在 Task 3 Step 7 未调整且此处 FAIL,需回到 Task 3 Step 7 处理:该测试清空 `_graphs` 后 approve,在 P0 后会走 lazy recompile 路径(team_store 仍有 team)而非 ValueError。调整方案:在清空 `_graphs` 后同时删除 team(`client.delete("/api/teams/dev")`),使 lazy recompile 路径抛 ValueError(team not found)→ 409 + failed,保持原测试意图。

- [ ] **Step 5: 检查工作树状态**

Run: `git status`

Expected: clean working tree(所有改动已提交)

- [ ] **Step 6: 检查提交历史**

Run: `git log --oneline -5`

Expected: 看到 P0 的 3-4 个 commit:
- `feat(api): RunManager 注入 checkpointer + has_graph 方法`
- `feat(api): RunManager.recompile_and_resume lazy recompile 方法`
- `feat(api): approve_run lazy recompile on restart (P0 Run 可恢复性)`
- (可选) fast path 测试 commit

- [ ] **Step 7: Phase commit(若 fast path 测试未单独提交)**

若 Task 4 Step 1 的新测试尚未提交:

```powershell
git add tests/api/test_run_resumability.py
git commit -m "test(api): fast path 不触发 recompile 保护性测试"
```

若已随 Task 3 提交则跳过此步。

---

## Self-Review

**1. Spec coverage(spec §2 P0 设计):**
- ✅ §2.2 lazy recompile on approve 方案 — Task 3 approve_run 分支(graph 存在→fast path / graph 缺失→lazy recompile)
- ✅ §2.2 从 `team_store.get(run.team_name)` 取 Team — Task 3 approve_run `team = team_store.get(run["team_name"])`
- ✅ §2.2 用 TeamCompiler 重新 compile(注入 checkpointer) — Task 2 `recompile_and_resume` 调用 `compiler.compile(checkpointer=self._saver)`
- ✅ §2.2 注入 `_graphs[run_id]` / `_configs[run_id]` — Task 2 `recompile_and_resume` 注入
- ✅ §2.2 调用 `resume_run(run_id, ...)` — Task 2 `recompile_and_resume` 末尾调用
- ✅ §2.3 `RunManager.recompile_and_resume(run_id, team, compiler_factory, approved, reason)` — Task 2 新增方法,签名与 spec 一致
- ✅ §2.3 `compiler_factory` 是闭包 — Task 3 approve_run `lambda: _build_compiler(...)`
- ✅ §2.3 `approve_run` 路由改造 — Task 3 完整改造后代码
- ✅ §2.4 RunManager 注入 checkpointer — Task 1 `__init__` 加参数 + server.py 传入
- ✅ §2.5 `test_approve_after_restart_recompiles_and_resumes` — Task 3
- ✅ §2.5 `test_approve_after_restart_team_deleted_returns_409` — Task 3
- ✅ §2.5 `test_approve_with_graph_present_uses_fast_path` — Task 4
- ✅ §2.5 `test_recompile_uses_correct_checkpointer` — Task 2
- ✅ §7 风险缓解:compiler_factory 在 approve_run 内构造闭包,不复用旧 compiler 实例 — Task 3 `_build_compiler` 每次调用构造新 TeamCompiler

**2. Placeholder scan:**
- 无 "TBD"/"TODO"/"fill in details"
- 每个 Step 都有完整代码或确切命令
- approve_run 改造后代码完整给出(非 diff)
- 测试代码完整可运行(含 import / helper / 断言)

**3. Type consistency:**
- `RunManager.__init__(run_repo, audit_repo, event_bus, checkpointer=None)` — Task 1 定义,server.py 调用一致
- `RunManager.has_graph(run_id) -> bool` — Task 1 定义,Task 3 approve_run 调用一致
- `RunManager.recompile_and_resume(run_id, team, compiler_factory, approved, reason=None) -> None` — Task 2 定义,Task 3 approve_run 调用一致
- `_build_compiler(model_provider, tool_registry, library, team_store) -> TeamCompiler` — Task 3 定义,approve_run lambda 调用一致(4 个位置参数)
- `compiler_factory: Callable[[], TeamCompiler]` — Task 2 类型 hint,Task 3 lambda `() -> TeamCompiler` 一致
- `run["team_name"]` 字段 — `RunRepo.create_run(team_name, task)` 已写入(见 storage/runs.py:24-34),无需新增字段

**4. 测试覆盖矩阵:**

| 测试 | Task | 验证点 |
|------|------|--------|
| `test_has_graph_returns_true_after_start_run` | 1 | start_run 后 has_graph True |
| `test_has_graph_returns_false_for_unknown_run` | 1 | 未知 run_id has_graph False |
| `test_recompile_and_resume_constructs_graph_and_resumes` | 2 | compiler_factory 调用 + graph/config 注入 + resume_run 调用 |
| `test_recompile_uses_correct_checkpointer` | 2 | compile 传入 self._saver |
| `test_approve_after_restart_recompiles_and_resumes` | 3 | 重启后 approve → lazy recompile → run completed |
| `test_approve_after_restart_team_deleted_returns_409` | 3 | 重启 + team 删除 → 409 + failed |
| `test_approve_with_graph_present_uses_fast_path` | 4 | graph 存在 → fast path,不触发 recompile |

**5. 潜在风险与缓解:**
- `test_approve_run_value_error_still_returns_409`(test_api_approvals_robustness.py)在 P0 后可能 FAIL:该测试清空 `_graphs` 后 approve,在 P0 后走 lazy recompile 路径。Task 3 Step 7 与 Task 4 Step 4 均注明调整方案(同时删除 team 使 lazy recompile 抛 ValueError)。
- `test_approve_after_graph_lost_marks_run_failed`(test_api_approvals.py)测试旧行为,P0 后必然 FAIL:Task 3 Step 5 删除该测试。
- SqliteSaver checkpoint 续跑依赖 langgraph 版本行为:Task 3 Step 6 集成测试验证续跑成功,若失败需排查 saver.lock 是否与 conn_lock 一致(server.py 已 assert)。

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp6-p0-run-resumability.md`。两种执行方式:

1. **Subagent-Driven(推荐)** — 每个任务派发新 subagent,任务间 review
2. **Inline Execution** — 在当前会话执行,批量 checkpoint review

选择哪种?
