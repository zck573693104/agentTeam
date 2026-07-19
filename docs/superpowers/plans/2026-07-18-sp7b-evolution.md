# SP7b: Agent 自进化系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Agent 新增自进化能力 —— 每次 run 终态(completed/failed)后异步触发 4 维度进化(PromptOptimizer/ParamTuner/SkillGenerator/SkillSelector),版本化记录历史,支持回滚到任意 version,失败保护(任一维度失败不影响其他维度),防抖(5 分钟内同 agent 不重复触发)。

**Architecture:** 新增 `evolution_history` SQLite 表 + `EvolutionRepo`;`Agent` 加 `version` 字段(默认 1);`AgentLibrary` 加 `update_version/update_prompt/update_params` 三个更新方法;新增 `EvolutionEngine` 协调 4 维度顺序执行,任一成功则 version+=1;`RunManager` 加 `evolution_engine` 参数,在 `_handle_invoke_result`/`_handle_error` 中异步 daemon thread 触发;新增 `POST /api/agents/{name}/rollback?version=N` 回滚 endpoint;4 个维度各自封装 LLM 调用 + history 写入 + 失败保护 try/except。

**Tech Stack:** Python 3.11+, sqlite3, threading, FastAPI, langchain (SystemMessage/HumanMessage), pytest

**Prerequisite:** SP7a(Skill 系统)已完成 — SkillGenerator 依赖 SkillLoader.reload(),SkillSelector 依赖 SkillLoader.list_available()。

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/domain/agent.py` | Agent 数据类 | 修改:加 `version` 字段 |
| `agentteam/domain/library.py` | AgentLibrary | 修改:加 `update_version`/`update_prompt`/`update_params` |
| `agentteam/storage/db.py` | init_db / 表 schema | 修改:create evolution_history 表 + library_agents.version 列 |
| `agentteam/storage/evolution.py` | EvolutionRepo | 新建 |
| `agentteam/storage/library.py` | LibraryRepo | 修改:upsert 含 version 字段 |
| `agentteam/runtime/evolution.py` | EvolutionEngine + 4 维度 | 新建 |
| `agentteam/runtime/evolution_prompts.py` | 4 个 LLM 指令模板 | 新建 |
| `agentteam/api/routes/evolution.py` | rollback + history endpoint | 新建 |
| `agentteam/api/run_manager.py` | RunManager | 修改:`__init__` 加 `evolution_engine`,触发点 |
| `agentteam/api/server.py` | create_app | 修改:创建 EvolutionRepo + EvolutionEngine |
| `tests/storage/test_evolution_repo.py` | EvolutionRepo 单元测试 | 新建 |
| `tests/runtime/test_evolution.py` | EvolutionEngine 4 维度测试 | 新建 |
| `tests/api/test_api_evolution.py` | rollback + history endpoint 测试 | 新建 |

---

## Task 1: evolution_history 表 + EvolutionRepo 基础

**Files:**
- Modify: `agentteam/storage/db.py`(create table SQL)
- Create: `agentteam/storage/evolution.py`
- Create: `tests/storage/test_evolution_repo.py`

- [ ] **Step 1: 写失败测试 — EvolutionRepo.add_record + list_history**

创建 `d:\project\agentTeam\tests\storage\test_evolution_repo.py`:

```python
"""SP7b EvolutionRepo 单元测试。"""
import sqlite3
import threading

import pytest

from agentteam.storage.evolution import EvolutionRepo


def _make_repo(tmp_path) -> tuple[EvolutionRepo, sqlite3.Connection]:
    """构造测试用 EvolutionRepo(含 evolution_history 表)。"""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    # 创建表(复用生产 schema)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evolution_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            dimension TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            diff TEXT,
            reason TEXT,
            run_id TEXT,
            success BOOLEAN NOT NULL,
            error TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_evo_agent ON evolution_history(agent_name, version);
    """)
    conn.commit()
    repo = EvolutionRepo(conn, lock=threading.Lock())
    return repo, conn


def test_add_record_returns_id(tmp_path):
    """add_record 返回新插入的 id(>=1)。"""
    repo, _ = _make_repo(tmp_path)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="old", after_value="new", diff="...",
        reason="test", run_id="r1", success=True,
    )
    assert eid >= 1


def test_add_record_failed_dimension_stores_error(tmp_path):
    """success=False 时,error 字段记录失败原因。"""
    repo, _ = _make_repo(tmp_path)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="", after_value="", diff="",
        reason="LLM error", run_id="r1", success=False, error="timeout",
    )
    assert eid >= 1


def test_list_history_returns_records_for_agent(tmp_path):
    """list_history 返回该 agent 的所有记录。"""
    repo, _ = _make_repo(tmp_path)
    repo.add_record("coder", 1, "prompt", "a", "b", "", "r1", "r1", True)
    repo.add_record("coder", 2, "params", "{}", "{}", "", "r2", "r2", True)
    repo.add_record("reviewer", 1, "prompt", "x", "y", "", "r3", "r3", True)
    history = repo.list_history("coder")
    assert len(history) == 2
    assert all(h["agent_name"] == "coder" for h in history)


def test_list_history_ordered_by_timestamp_desc(tmp_path):
    """list_history 按 timestamp 倒序(最新在前)。"""
    repo, conn = _make_repo(tmp_path)
    # 手动插入两条带不同 timestamp
    conn.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 1, "prompt", 1, "2026-01-01 10:00:00"),
    )
    conn.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 2, "prompt", 1, "2026-01-02 10:00:00"),
    )
    conn.commit()
    history = repo.list_history("a")
    assert len(history) == 2
    assert history[0]["version"] == 2  # 新版本在前
    assert history[1]["version"] == 1


def test_list_history_respects_limit(tmp_path):
    """list_history(limit=N) 最多返回 N 条。"""
    repo, _ = _make_repo(tmp_path)
    for i in range(5):
        repo.add_record("a", i + 1, "prompt", "", "", "", "", "r", True)
    assert len(repo.list_history("a", limit=3)) == 3
    assert len(repo.list_history("a", limit=10)) == 5


def test_list_history_unknown_agent_returns_empty(tmp_path):
    """未知 agent:list_history 返回空 list,不抛异常。"""
    repo, _ = _make_repo(tmp_path)
    assert repo.list_history("nonexistent") == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_evolution_repo.py -v`
Expected: 6 个 FAIL(`ImportError: cannot import name 'EvolutionRepo' from 'agentteam.storage.evolution'`)

- [ ] **Step 3: 实现 — 创建 EvolutionRepo**

创建 `d:\project\agentTeam\agentteam\storage\evolution.py`:

```python
"""EvolutionRepo:agent 进化历史的 SQLite 持久化。"""
from __future__ import annotations

import sqlite3
import threading


class EvolutionRepo:
    """evolution_history 表的 CRUD 仓库。

    表 schema(由 init_db 创建):
        id, agent_name, version, dimension, before_value, after_value,
        diff, reason, run_id, success, error, timestamp

    线程安全:与 RunRepo/AuditRepo 共享同一 sqlite3.Connection,
    必须传入同一把 threading.Lock 串行化所有访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def add_record(
        self,
        agent_name: str,
        version: int,
        dimension: str,
        before_value: str,
        after_value: str,
        diff: str,
        reason: str,
        run_id: str | None,
        success: bool,
        error: str | None = None,
    ) -> int:
        """插入一条 history 记录,返回新 id。

        dimension: 'prompt' | 'params' | 'skill_gen' | 'skill_select' | 'rollback'
        success=False 时 error 字段记录失败原因。
        run_id=None 表示用户触发的 rollback(不关联 run)。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO evolution_history
                    (agent_name, version, dimension, before_value, after_value,
                     diff, reason, run_id, success, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (agent_name, version, dimension, before_value, after_value,
                 diff, reason, run_id, 1 if success else 0, error),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_history(self, agent_name: str, limit: int = 20) -> list[dict]:
        """按 timestamp 倒序返回该 agent 的 history(最多 limit 条)。"""
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (agent_name, limit),
            )
            return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 4: 实现 — db.py init_db 加 evolution_history 表**

修改 `d:\project\agentTeam\agentteam\storage\db.py` 的 `init_db`,在现有表创建 SQL 之后追加 evolution_history 表与 library_agents.version 列:

```python
    # SP7b: evolution_history 表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evolution_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name   TEXT NOT NULL,
            version      INTEGER NOT NULL,
            dimension    TEXT NOT NULL,
            before_value TEXT,
            after_value  TEXT,
            diff         TEXT,
            reason       TEXT,
            run_id       TEXT,
            success      BOOLEAN NOT NULL,
            error        TEXT,
            timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_evo_agent ON evolution_history(agent_name, version);
    """)
    # library_agents 加 version 列(若不存在)
    try:
        conn.execute("ALTER TABLE library_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.commit()
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_evolution_repo.py -v`
Expected: 6 PASS

- [ ] **Step 6: 运行现有 storage 测试验证无回归**

Run: `python -m pytest tests/storage/ -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```powershell
git add agentteam/storage/evolution.py agentteam/storage/db.py tests/storage/test_evolution_repo.py
git commit -m "feat(storage): EvolutionRepo + evolution_history 表"
```

---

## Task 2: EvolutionRepo 扩展 + Agent.version 字段

**Files:**
- Modify: `agentteam/storage/evolution.py`(加 get_version_snapshot + list_recent_runs)
- Modify: `agentteam/domain/agent.py`(加 version 字段)
- Modify: `tests/storage/test_evolution_repo.py`(追加测试)

- [ ] **Step 1: 写失败测试 — get_version_snapshot + list_recent_runs**

在 `d:\project\agentTeam\tests\storage\test_evolution_repo.py` 末尾追加:

```python
def test_get_version_snapshot_returns_all_records_for_version(tmp_path):
    """get_version_snapshot 返回指定 version 的所有记录(可能多条,因 4 维度)。"""
    repo, _ = _make_repo(tmp_path)
    # version 2 有 3 条记录(4 维度中 3 个成功)
    repo.add_record("coder", 2, "prompt", "a", "b", "", "r", "r1", True)
    repo.add_record("coder", 2, "params", "{}", "{}", "", "r", "r1", True)
    repo.add_record("coder", 2, "skill_gen", "", "auto_x.md", "", "r", "r1", True)
    # version 3 有 1 条
    repo.add_record("coder", 3, "prompt", "b", "c", "", "r", "r2", True)
    snapshot = repo.get_version_snapshot("coder", 2)
    assert len(snapshot) == 3
    assert all(s["version"] == 2 for s in snapshot)


def test_get_version_snapshot_unknown_version_returns_empty(tmp_path):
    """未知 version 返回空 list,不抛异常。"""
    repo, _ = _make_repo(tmp_path)
    assert repo.get_version_snapshot("coder", 999) == []


def test_list_recent_runs_returns_successful_records(tmp_path):
    """list_recent_runs 返回该 agent 最近 N 次成功的进化记录(用于 ParamTuner 统计)。"""
    repo, _ = _make_repo(tmp_path)
    repo.add_record("coder", 1, "prompt", "a", "b", "", "r1", "r1", True)
    repo.add_record("coder", 1, "params", "{}", "{}", "", "r1", "r1", False, error="x")  # 失败,排除
    repo.add_record("coder", 2, "prompt", "b", "c", "", "r2", "r2", True)
    repo.add_record("coder", 3, "params", "{}", "{}", "", "r3", "r3", True)
    recent = repo.list_recent_runs("coder", limit=2)
    # 只返回成功的,按时间倒序
    assert len(recent) == 2
    assert all(r["success"] == 1 or r["success"] is True for r in recent)
    # 最新版本在前
    assert recent[0]["version"] == 3
    assert recent[1]["version"] == 2


def test_list_recent_runs_unknown_agent_returns_empty(tmp_path):
    """未知 agent 返回空 list。"""
    repo, _ = _make_repo(tmp_path)
    assert repo.list_recent_runs("nonexistent") == []
```

并在 `d:\project\agentTeam\tests\runtime\test_skills.py` 或新建测试文件 `tests/domain/test_agent_version.py`:

```python
"""SP7b: Agent.version 字段测试。"""
from agentteam.domain.agent import Agent


def test_agent_version_defaults_to_1():
    """Agent.version 默认 1(向后兼容)。"""
    agent = Agent(name="w", role="worker")
    assert agent.version == 1


def test_agent_version_accepts_custom_value():
    """Agent.version 可在构造时传入。"""
    agent = Agent(name="w", role="worker", version=5)
    assert agent.version == 5
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_evolution_repo.py -v -k "version_snapshot or recent_runs" tests/domain/test_agent_version.py -v`
Expected: FAIL(`AttributeError: 'EvolutionRepo' object has no attribute 'get_version_snapshot'` + Agent.version 不存在)

- [ ] **Step 3: 实现 — EvolutionRepo 加两个方法**

修改 `d:\project\agentTeam\agentteam\storage\evolution.py`,在 `list_history` 之后追加:

```python
    def get_version_snapshot(self, agent_name: str, version: int) -> list[dict]:
        """取指定 version 的所有 history 记录(可能多条,因一次 trigger 触发 4 维度)。

        用于回滚:把该 version 所有 dimension 的 before_value 应用回 Agent。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ? AND version = ?
                ORDER BY id ASC
                """,
                (agent_name, version),
            )
            return [dict(row) for row in cur.fetchall()]

    def list_recent_runs(self, agent_name: str, limit: int = 5) -> list[dict]:
        """取该 agent 最近 N 次成功的进化记录(用于 ParamTuner 统计历史指标)。

        按 timestamp 倒序,只返回 success=True 的记录。
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT * FROM evolution_history
                WHERE agent_name = ? AND success = 1
                ORDER BY id DESC
                LIMIT ?
                """,
                (agent_name, limit),
            )
            return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 4: 实现 — Agent 加 version 字段**

修改 `d:\project\agentTeam\agentteam\domain\agent.py` 的 Agent dataclass,在 `skills` 字段之后追加:

```python
    # SP7b: 进化代数,默认 1(每次 EvolutionEngine.trigger 任一维度成功后 +=1)
    version: int = 1
```

完整的字段尾部应为:

```python
    # SP7a: 装备的 skill 名列表(对应 skills/ 目录下 .md 文件的 stem)
    # 编译期由 SkillLoader.load 解析;缺失抛 KeyError(编译期 fail-fast)。
    skills: list[str] = field(default_factory=list)

    # SP7b: 进化代数,默认 1(每次 EvolutionEngine.trigger 任一维度成功后 +=1)
    version: int = 1
```

- [ ] **Step 5: 运行新测试验证通过**

Run: `python -m pytest tests/storage/test_evolution_repo.py -v tests/domain/test_agent_version.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 运行全量测试验证无回归**

Run: `python -m pytest tests/ -v --tb=short -x`
Expected: 全部 PASS(Agent.version 默认 1,旧代码不传该字段不受影响)

- [ ] **Step 7: 提交**

```powershell
git add agentteam/storage/evolution.py agentteam/domain/agent.py tests/storage/test_evolution_repo.py tests/domain/test_agent_version.py
git commit -m "feat(domain): Agent.version 字段 + EvolutionRepo 扩展(version_snapshot/recent_runs)"
```

---

## Task 3: AgentLibrary 加 update_version / update_prompt / update_params

**Files:**
- Modify: `agentteam/domain/library.py`(AgentLibrary 加 3 个方法)
- Modify: `agentteam/storage/library.py`(LibraryRepo.upsert 含 version 字段)
- Create: `tests/domain/test_library_updates.py`

- [ ] **Step 1: 写失败测试 — AgentLibrary 3 个 update 方法**

创建 `d:\project\agentTeam\tests\domain\test_library_updates.py`:

```python
"""SP7b: AgentLibrary update_* 方法测试。"""
from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


def test_update_version_changes_agent_version():
    """update_version 修改内存中的 Agent.version。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker", version=1))
    lib.update_version("coder", 5)
    assert lib.get("coder").version == 5


def test_update_prompt_changes_agent_system_prompt():
    """update_prompt 修改内存中的 Agent.system_prompt。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker", system_prompt="old"))
    lib.update_prompt("coder", "new prompt")
    assert lib.get("coder").system_prompt == "new prompt"


def test_update_params_changes_agent_max_iterations_and_policy():
    """update_params 修改 max_iterations 和 approval_policy。"""
    from agentteam.domain.approval import ApprovalPolicy
    lib = AgentLibrary()
    lib.register(Agent(
        name="coder", role="worker",
        max_iterations=5,
        approval_policy=ApprovalPolicy(mode="never"),
    ))
    new_policy = ApprovalPolicy(mode="always", tools=["dangerous_tool"])
    lib.update_params("coder", {
        "max_iterations": 15,
        "approval_policy": new_policy,
    })
    agent = lib.get("coder")
    assert agent.max_iterations == 15
    assert agent.approval_policy == new_policy


def test_update_version_unknown_agent_is_noop():
    """update_version 未知 agent:不抛异常(幂等)。"""
    lib = AgentLibrary()
    lib.update_version("nonexistent", 5)  # 应不抛


def test_update_version_with_repo_persists():
    """有 repo 时,update_version 同步到 DB。"""
    from unittest.mock import MagicMock
    mock_repo = MagicMock()
    lib = AgentLibrary(repo=mock_repo)
    lib.register(Agent(name="coder", role="worker", version=1))
    lib.update_version("coder", 3)
    # repo.upsert 应被调用,传入更新后的 agent
    assert mock_repo.upsert.called
    updated_agent = mock_repo.upsert.call_args[0][0]
    assert updated_agent.version == 3
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/domain/test_library_updates.py -v`
Expected: 5 个 FAIL(`AttributeError: 'AgentLibrary' object has no attribute 'update_version'`)

- [ ] **Step 3: 实现 — AgentLibrary 加 3 个方法**

修改 `d:\project\agentTeam\agentteam\domain\library.py`,在 `delete` 方法之后追加:

```python
    def update_version(self, name: str, version: int) -> None:
        """更新 Agent.version(SP7b:EvolutionEngine 触发后递增)。

        持锁保证并发安全;DB 先、内存后(BUG-03 规约)。
        未知 agent 静默忽略(幂等)。
        """
        with self._lock:
            agent = self.agents.get(name)
            if agent is None:
                return
            agent.version = version
            if self._repo is not None:
                self._repo.upsert(agent)

    def update_prompt(self, name: str, new_prompt: str) -> None:
        """更新 Agent.system_prompt(SP7b:PromptOptimizer 调用)。"""
        with self._lock:
            agent = self.agents.get(name)
            if agent is None:
                return
            agent.system_prompt = new_prompt
            if self._repo is not None:
                self._repo.upsert(agent)

    def update_params(self, name: str, params: dict) -> None:
        """更新 Agent.max_iterations / approval_policy(SP7b:ParamTuner 调用)。

        params 仅包含需更新的字段,其他字段保持不变。
        """
        with self._lock:
            agent = self.agents.get(name)
            if agent is None:
                return
            if "max_iterations" in params:
                agent.max_iterations = params["max_iterations"]
            if "approval_policy" in params:
                agent.approval_policy = params["approval_policy"]
            if self._repo is not None:
                self._repo.upsert(agent)
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `python -m pytest tests/domain/test_library_updates.py -v`
Expected: 5 PASS

- [ ] **Step 5: 运行旧 library 测试验证无回归**

Run: `python -m pytest tests/domain/ tests/storage/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```powershell
git add agentteam/domain/library.py tests/domain/test_library_updates.py
git commit -m "feat(domain): AgentLibrary 加 update_version/update_prompt/update_params"
```

---

## Task 4: EvolutionEngine 骨架 + 辅助函数

**Files:**
- Create: `agentteam/runtime/evolution.py`
- Create: `tests/runtime/test_evolution.py`

- [ ] **Step 1: 写失败测试 — EvolutionEngine 骨架行为**

创建 `d:\project\agentTeam\tests\runtime\test_evolution.py`:

```python
"""SP7b EvolutionEngine 测试。"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.runtime.evolution import EvolutionEngine, EvolutionResult


def _make_engine(tmp_path=None, skill_loader=None, skills_dir=None):
    """构造测试用 EvolutionEngine,所有 repo 用 MagicMock。"""
    return EvolutionEngine(
        model_provider=MagicMock(),
        agent_library=MagicMock(),
        evolution_repo=MagicMock(),
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        skill_loader=skill_loader,
        skills_dir=skills_dir,
    )


def test_evolution_result_dataclass():
    """EvolutionResult 数据类字段。"""
    r = EvolutionResult(success=True, dimension="prompt", reason="ok")
    assert r.success is True
    assert r.dimension == "prompt"
    assert r.error is None


def test_trigger_unknown_run_does_nothing(tmp_path):
    """trigger 未知 run_id(get_run 返回 None):不调用任何进化。"""
    engine = _make_engine()
    engine._run_repo.get_run.return_value = None
    engine.trigger("nonexistent-run")
    # 不应访问 audit_repo.list_events
    engine._audit.list_events.assert_not_called()


def test_trigger_no_agents_in_trace_does_nothing():
    """trace 中无 agent(空 trace):不调用 _evolve_agent。"""
    engine = _make_engine()
    engine._run_repo.get_run.return_value = {"status": "completed"}
    engine._audit.list_events.return_value = []  # 空 trace
    engine.trigger("r1")
    engine._agent_library.get.assert_not_called()


def test_collect_agents_from_trace_extracts_unique_names():
    """_collect_agents_from_trace 从 worker_start / leader_plan 事件提取 agent 名(去重)。"""
    engine = _make_engine()
    engine._audit.list_events.return_value = [
        {"event_type": "worker_start", "actor": "coder"},
        {"event_type": "worker_end", "actor": "coder"},
        {"event_type": "leader_plan", "actor": "ceo"},
        {"event_type": "worker_start", "actor": "reviewer"},
        {"event_type": "tool_call", "actor": "system"},
    ]
    agents = engine._collect_agents_from_trace("r1")
    assert set(agents) == {"coder", "ceo", "reviewer"}


def test_evolve_agent_debounce_blocks_within_5_minutes():
    """防抖:5 分钟内同 agent 不重复触发。"""
    engine = _make_engine()
    engine._agent_library.get.return_value = Agent(name="coder", role="worker", version=1)
    engine._audit.list_events.return_value = []
    # 第一次触发
    engine._evolve_agent("coder", "r1")
    first_call_count = engine._evolution_repo.add_record.call_count
    # 立即第二次触发(应被防抖拦截)
    engine._evolve_agent("coder", "r2")
    assert engine._evolution_repo.add_record.call_count == first_call_count


def test_evolve_agent_debounce_allows_after_5_minutes(monkeypatch):
    """防抖:5 分钟后允许再次触发。"""
    engine = _make_engine()
    engine._agent_library.get.return_value = Agent(name="coder", role="worker", version=1)
    engine._audit.list_events.return_value = []

    # mock time.time():第一次调用返回 1000,第二次返回 1000 + 301
    call_count = [0]
    fake_time = [1000]
    def mock_time():
        return fake_time[0]
    monkeypatch.setattr("agentteam.runtime.evolution.time.time", mock_time)

    engine._evolve_agent("coder", "r1")
    first_count = engine._evolution_repo.add_record.call_count
    # 推进时间到 5 分钟后
    fake_time[0] = 1000 + 301
    engine._evolve_agent("coder", "r2")
    # 第二次应执行(虽然 trace 空导致 4 维度都跳过,但 _evolve_agent 入口未被防抖)
    # 验证:_agent_library.get 被调用 2 次(防抖通过)
    assert engine._agent_library.get.call_count == 2


def test_evolve_agent_unknown_agent_does_nothing():
    """_evolve_agent 未知 agent(library.get 返回 None):不抛异常。"""
    engine = _make_engine()
    engine._agent_library.get.return_value = None
    engine._evolve_agent("nonexistent", "r1")
    engine._evolution_repo.add_record.assert_not_called()


def test_evolve_agent_version_increments_on_success():
    """任一维度成功 → Agent.version += 1。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    engine._agent_library.get.return_value = agent
    engine._audit.list_events.return_value = []

    # mock 4 维度:1 个成功,3 个跳过
    engine._optimize_prompt = MagicMock(return_value=EvolutionResult(True, "prompt", "ok"))
    engine._tune_params = MagicMock(return_value=EvolutionResult(True, "params", "skip"))
    engine._generate_skill = MagicMock(return_value=EvolutionResult(True, "skill_gen", "skip"))
    engine._select_skills = MagicMock(return_value=EvolutionResult(True, "skill_select", "skip"))

    engine._evolve_agent("coder", "r1")
    engine._agent_library.update_version.assert_called_once_with("coder", 2)


def test_evolve_agent_version_not_incremented_on_all_fail():
    """4 维度全部失败 → version 不递增。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    engine._agent_library.get.return_value = agent
    engine._audit.list_events.return_value = []

    engine._optimize_prompt = MagicMock(return_value=EvolutionResult(False, "prompt", "err", "x"))
    engine._tune_params = MagicMock(return_value=EvolutionResult(False, "params", "err", "x"))
    engine._generate_skill = MagicMock(return_value=EvolutionResult(False, "skill_gen", "err", "x"))
    engine._select_skills = MagicMock(return_value=EvolutionResult(False, "skill_select", "err", "x"))

    engine._evolve_agent("coder", "r1")
    engine._agent_library.update_version.assert_not_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v`
Expected: FAIL(`ImportError: cannot import name 'EvolutionEngine' from 'agentteam.runtime.evolution'`)

- [ ] **Step 3: 实现 — 创建 EvolutionEngine 骨架**

创建 `d:\project\agentTeam\agentteam\runtime\evolution.py`:

```python
"""EvolutionEngine:Run 终态后异步触发 4 维度自进化。

4 维度:PromptOptimizer / ParamTuner / SkillGenerator / SkillSelector。
设计原则:
- 异步:不阻塞 RunManager 的 API 响应
- 隔离:4 维度独立 LLM 调用 + 独立写 history,互不影响
- 失败保护:任一维度失败仅记 error,不影响其他维度 / run 结果
- 防抖:同一 agent 5 分钟内只触发一次
- 版本原子性:一次 trigger 内 4 维度全部尝试后,任一成功则 version += 1
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelProvider
from agentteam.runtime.skills import SkillLoader
from agentteam.storage.audit import AuditRepo
from agentteam.storage.evolution import EvolutionRepo
from agentteam.storage.runs import RunRepo


@dataclass
class EvolutionResult:
    """单个维度进化结果。"""
    success: bool
    dimension: str
    reason: str
    error: str | None = None


class EvolutionEngine:
    """协调 4 维度进化的主控。"""

    DEBOUNCE_SECONDS = 300  # 5 分钟

    def __init__(
        self,
        model_provider: ModelProvider,
        agent_library: AgentLibrary,
        evolution_repo: EvolutionRepo,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        skill_loader: SkillLoader | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        self._mp = model_provider
        self._lib = agent_library
        self._evo_repo = evolution_repo
        self._run_repo = run_repo
        self._audit = audit_repo
        self._skill_loader = skill_loader
        self._skills_dir = skills_dir
        self._last_trigger: dict[str, float] = {}
        self._lock = threading.Lock()

    def trigger(self, run_id: str) -> None:
        """RunManager 在 run 终态后异步调用。"""
        run = self._run_repo.get_run(run_id)
        if run is None:
            return
        agents = self._collect_agents_from_trace(run_id)
        if not agents:
            return
        for agent_name in agents:
            self._evolve_agent(agent_name, run_id)

    def _collect_agents_from_trace(self, run_id: str) -> list[str]:
        """从 audit_events 提取涉及的 agent 名(去重)。

        扫描 worker_start / leader_plan 事件的 actor 字段。
        失败返回空列表。
        """
        try:
            events = self._audit.list_events(run_id)
        except Exception:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for ev in events:
            ev_type = ev.get("event_type", "") if isinstance(ev, dict) else ""
            if ev_type in ("worker_start", "leader_plan"):
                actor = ev.get("actor", "") if isinstance(ev, dict) else ""
                if actor and actor not in seen:
                    seen.add(actor)
                    names.append(actor)
        return names

    def _evolve_agent(self, agent_name: str, run_id: str) -> None:
        """对单个 agent 执行 4 维度进化。"""
        # 防抖
        now = time.time()
        with self._lock:
            last = self._last_trigger.get(agent_name, 0)
            if now - last < self.DEBOUNCE_SECONDS:
                return
            self._last_trigger[agent_name] = now

        agent = self._lib.get(agent_name)
        if agent is None:
            return

        trace = self._audit.list_events(run_id)
        old_version = agent.version or 1

        # 4 维度顺序执行(每个维度独立 try/except,失败仅记 error)
        results: list[EvolutionResult] = []
        results.append(self._optimize_prompt(agent, trace, run_id))
        results.append(self._tune_params(agent, trace, run_id))
        results.append(self._generate_skill(agent, trace, run_id))
        results.append(self._select_skills(agent, trace, run_id))

        # 任一维度成功 → version += 1
        if any(r.success for r in results):
            new_version = old_version + 1
            self._lib.update_version(agent_name, new_version)

    def _optimize_prompt(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 1:分析 trace + LLM 重写 system_prompt。Task 6 实现。"""
        return EvolutionResult(True, "prompt", "not implemented yet")

    def _tune_params(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 2:统计历史 + LLM 建议参数调整。Task 7 实现。"""
        return EvolutionResult(True, "params", "not implemented yet")

    def _generate_skill(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 3:从成功 run 提炼 skill。Task 8 实现。"""
        return EvolutionResult(True, "skill_gen", "not implemented yet")

    def _select_skills(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 4:任务匹配 skill 软推荐。Task 9 实现。"""
        return EvolutionResult(True, "skill_select", "not implemented yet")
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v`
Expected: 8 PASS(部分测试可能需调整,如 `test_evolve_agent_debounce_allows_after_5_minutes` 因 4 维度全跳过,add_record 不被调用,改为验证 `get` 调用次数)

- [ ] **Step 5: 提交**

```powershell
git add agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): EvolutionEngine 骨架 — trigger + 防抖 + version 递增"
```

---

## Task 5: LLM 指令模板 + 辅助函数

**Files:**
- Create: `agentteam/runtime/evolution_prompts.py`
- Modify: `agentteam/runtime/evolution.py`(加辅助函数: _summarize_trace / _is_successful_run / _extract_task / _extract_tool_calls / _extract_final_answer / _compute_diff / _compute_stats / _parse_prompt / _parse_params / _parse_skill_response / _parse_skill_list)
- Modify: `tests/runtime/test_evolution.py`(追加测试)

- [ ] **Step 1: 写失败测试 — 辅助函数**

在 `d:\project\agentTeam\tests\runtime\test_evolution.py` 末尾追加:

```python
from agentteam.runtime.evolution import (
    _summarize_trace, _is_successful_run, _extract_task,
    _extract_tool_calls, _extract_final_answer, _compute_diff,
    _compute_stats, _parse_prompt, _parse_params, _parse_skill_list,
)


def test_summarize_trace_includes_key_events():
    """_summarize_trace 包含 worker_start/tool_call/error/worker_end 等关键事件。"""
    trace = [
        {"event_type": "run_start", "actor": "system"},
        {"event_type": "worker_start", "actor": "coder"},
        {"event_type": "tool_call", "actor": "coder", "payload": {"tool": "read_file"}},
        {"event_type": "worker_end", "actor": "coder"},
        {"event_type": "run_end", "actor": "system"},
    ]
    summary = _summarize_trace(trace)
    assert "worker_start" in summary
    assert "tool_call" in summary
    assert "coder" in summary


def test_is_successful_run_true_when_run_end_no_error():
    """run_end 存在且无 error 事件 → 成功。"""
    trace = [
        {"event_type": "worker_start"},
        {"event_type": "run_end"},
    ]
    assert _is_successful_run(trace) is True


def test_is_successful_run_false_when_error_event():
    """有 error 事件 → 失败。"""
    trace = [
        {"event_type": "error", "payload": {"error": "x"}},
        {"event_type": "run_end"},
    ]
    assert _is_successful_run(trace) is False


def test_is_successful_run_false_when_no_run_end():
    """无 run_end → 失败(被 cancel 或中断)。"""
    trace = [{"event_type": "worker_start"}]
    assert _is_successful_run(trace) is False


def test_extract_task_from_run_start_payload():
    """_extract_task 从 run_start 事件的 payload 提取 task。"""
    trace = [
        {"event_type": "run_start", "payload": {"task": "审查代码"}},
        {"event_type": "worker_start"},
    ]
    assert _extract_task(trace) == "审查代码"


def test_extract_task_returns_empty_when_no_run_start():
    """无 run_start → 返回空字符串。"""
    trace = [{"event_type": "worker_start"}]
    assert _extract_task(trace) == ""


def test_extract_tool_calls_returns_tool_names():
    """_extract_tool_calls 返回所有 tool_call 事件的 tool 名列表。"""
    trace = [
        {"event_type": "tool_call", "payload": {"tool": "read_file", "args": {}}},
        {"event_type": "tool_call", "payload": {"tool": "write_file", "args": {}}},
        {"event_type": "worker_end"},
    ]
    tools = _extract_tool_calls(trace)
    assert tools == ["read_file", "write_file"]


def test_extract_final_answer_from_worker_end_payload():
    """_extract_final_answer 从 worker_end 事件的 payload 提取 answer。"""
    trace = [
        {"event_type": "worker_end", "payload": {"answer": "最终答案"}},
    ]
    assert _extract_final_answer(trace) == "最终答案"


def test_compute_diff_shows_changes():
    """_compute_diff 返回 unified diff 文本(含 - / + 标记)。"""
    old = "line1\nline2\nline3"
    new = "line1\nline2-modified\nline3"
    diff = _compute_diff(old, new)
    assert "-line2" in diff
    assert "+line2-modified" in diff


def test_compute_stats_empty_history_returns_empty_dict():
    """空历史 → _compute_stats 返回空 dict。"""
    assert _compute_stats([]) == {}


def test_compute_stats_with_history():
    """有历史 → 返回统计字段。"""
    # 用 mock history 记录(字段按 EvolutionRepo schema)
    history = [
        {"dimension": "params", "before_value": "{}", "after_value": '{"max_iterations": 5}', "success": 1},
        {"dimension": "params", "before_value": "{}", "after_value": '{"max_iterations": 10}', "success": 1},
    ]
    stats = _compute_stats(history)
    # 至少包含某些统计字段(具体字段由实现决定)
    assert isinstance(stats, dict)


def test_parse_prompt_extracts_from_code_block():
    """_parse_prompt 从 ```...``` 代码块提取 prompt。"""
    response = "分析:\n```\nYou are a better coder.\n```\n结束"
    prompt = _parse_prompt(response)
    assert "better coder" in prompt


def test_parse_prompt_returns_original_if_no_code_block():
    """无代码块 → 返回原文(trim)。"""
    response = "You are a coder."
    assert _parse_prompt(response) == "You are a coder."


def test_parse_params_extracts_json():
    """_parse_params 从 JSON 代码块提取参数 dict。"""
    response = '建议:\n```json\n{"max_iterations": 15}\n```'
    params = _parse_params(response)
    assert params == {"max_iterations": 15}


def test_parse_params_invalid_json_returns_empty():
    """无效 JSON → 返回空 dict。"""
    response = "no json here"
    assert _parse_params(response) == {}


def test_parse_skill_list_extracts_names():
    """_parse_skill_list 从逗号分隔或 list 格式提取 skill 名。"""
    response = "推荐: code_review, testing"
    skills = _parse_skill_list(response)
    assert "code_review" in skills
    assert "testing" in skills


def test_parse_skill_list_empty_returns_empty():
    """无推荐 → 返回空 list。"""
    response = "no recommendation"
    assert _parse_skill_list(response) == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "summarize or successful or extract or compute or parse"`
Expected: FAIL(函数未定义)

- [ ] **Step 3: 实现 — 创建 evolution_prompts.py**

创建 `d:\project\agentTeam\agentteam\runtime\evolution_prompts.py`:

```python
"""SP7b:4 个进化维度的 LLM 指令模板。"""

PROMPT_OPTIMIZER_INSTRUCTION = """你是一个 Agent prompt 优化专家。

任务:分析 Agent 本次 run 的执行 trace,判断其 system_prompt 是否需要优化。

分析角度:
1. **模糊性**: prompt 是否包含模糊指令(如"做好"、"合适")导致 LLM 行为不确定?
2. **缺失约束**: 是否缺少必要约束(如输出格式、工具使用顺序、错误处理策略)?
3. **工具匹配**: prompt 描述的职责与 agent 装备的工具是否匹配?
4. **trace 反映的问题**: 是否在 trace 中看到反复尝试、错误、低效模式?

输出格式:
- 若需优化,用 ``` 代码块包裹新 prompt(纯文本,无 markdown 标题)
- 若已合理,原样返回当前 prompt(用 ``` 包裹)
- 简要说明优化理由(1-2 句)

不要在 prompt 中添加 LLM 无法执行的指令(如"思考 5 分钟")。"""

PARAM_TUNER_INSTRUCTION = """你是一个 Agent 参数调优专家。

任务:基于 Agent 最近 N 次 run 的统计指标,建议参数调整。

参数说明:
- max_iterations: ReAct 循环最大迭代数(1-20)
- approval_policy: 工具审批策略(never/always/on-failure)

统计指标可能包含:
- avg_iterations: 平均迭代次数(过高 → 可能 prompt 不清或工具集不当)
- max_iterations_reached_rate: 达到上限的比例(高 → 应增大 max_iterations 或优化 prompt)
- approval_rejected_rate: 审批拒绝率(高 → 应改 approval_policy 为 always 或调优工具集)
- tool_call_error_rate: 工具调用错误率(高 → 应换工具或加错误处理)

输出格式:
- 用 ```json 代码块包裹参数 dict,只包含需改动的字段
- 若无需改动,返回 ```json {}
- 简要说明调整理由

约束:
- max_iterations 必须在 [1, 20] 范围内
- 不要无理由地改动(避免漂移)"""

SKILL_GENERATOR_INSTRUCTION = """你是一个 Skill 提炼专家。

任务:从 Agent 本次成功执行中提炼可复用的 skill 模式,生成 markdown skill 文件。

分析角度:
1. **可复用模式**: 本次执行中是否有可抽象为通用指导的步骤或策略?
   (如"先 read_file 再 write_file"、"错误后重试 3 次")
2. **工具使用模式**: 是否有值得固化的工具调用顺序?
3. **决策模式**: 是否有可复用的判断逻辑?

输出格式:
- 若有可提炼模式,用 ```markdown 代码块包裹 skill 内容
  并在开头用 `# Skill: <name>` 标注 skill 名(命名 auto_<pattern>)
- 若无可提炼模式,返回 SKIP
- skill 内容应具体、可执行(不要泛泛而谈"做好工作")

约束:
- skill 名以 auto_ 开头(避免覆盖用户预置 skill)
- skill 内容不超过 500 字(简洁可读)"""

SKILL_SELECTOR_INSTRUCTION = """你是一个 Skill 推荐专家。

任务:根据 Agent 本次任务描述,从可用 skill 库中推荐相关 skill。

输入:
- Agent 当前已装备的 skills
- 候选 skills(库中所有可用 skill 名)
- 本次任务描述

输出格式:
- 用逗号分隔的 skill 名列表(如 code_review, testing)
- 若无推荐,返回空
- 简要说明推荐理由

约束:
- 只推荐与任务直接相关的 skill
- 不要推荐已装备的 skill
- 推荐数量不超过 3 个(避免 skill 过载)"""
```

- [ ] **Step 4: 实现 — evolution.py 加辅助函数**

修改 `d:\project\agentTeam\agentteam\runtime\evolution.py`,在文件末尾(EvolutionEngine 类之后)追加模块级辅助函数:

```python
import difflib
import json
import re


def _summarize_trace(trace: list) -> str:
    """把 trace 压缩为 LLM 可读的文本摘要(关键事件 + actor + payload 摘要)。"""
    if not trace:
        return "(empty trace)"
    lines = []
    for ev in trace:
        if not isinstance(ev, dict):
            continue
        ev_type = ev.get("event_type", "unknown")
        actor = ev.get("actor", "")
        payload = ev.get("payload", {})
        # payload 摘要:取前 100 字符
        payload_str = json.dumps(payload, ensure_ascii=False)[:100] if payload else ""
        lines.append(f"[{ev_type}] {actor}: {payload_str}")
    return "\n".join(lines)


def _is_successful_run(trace: list) -> bool:
    """run 成功 = 有 run_end 事件且无 error 事件。"""
    has_run_end = any(
        isinstance(ev, dict) and ev.get("event_type") == "run_end"
        for ev in trace
    )
    has_error = any(
        isinstance(ev, dict) and ev.get("event_type") == "error"
        for ev in trace
    )
    return has_run_end and not has_error


def _extract_task(trace: list) -> str:
    """从 run_start 事件的 payload.task 提取任务描述。"""
    for ev in trace:
        if isinstance(ev, dict) and ev.get("event_type") == "run_start":
            payload = ev.get("payload", {})
            return payload.get("task", "") if isinstance(payload, dict) else ""
    return ""


def _extract_tool_calls(trace: list) -> list[str]:
    """从所有 tool_call 事件提取 tool 名列表(按出现顺序)。"""
    tools = []
    for ev in trace:
        if isinstance(ev, dict) and ev.get("event_type") == "tool_call":
            payload = ev.get("payload", {})
            if isinstance(payload, dict):
                tool = payload.get("tool", "")
                if tool:
                    tools.append(tool)
    return tools


def _extract_final_answer(trace: list) -> str:
    """从 worker_end 事件的 payload.answer 提取最终答案。"""
    for ev in reversed(trace):
        if isinstance(ev, dict) and ev.get("event_type") == "worker_end":
            payload = ev.get("payload", {})
            return payload.get("answer", "") if isinstance(payload, dict) else ""
    return ""


def _compute_diff(old: str, new: str) -> str:
    """计算两段文本的 unified diff。"""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=3,
    )
    return "".join(diff)


def _compute_stats(history: list) -> dict:
    """从历史 evolution 记录计算统计指标(ParamTuner 用)。

    history 元素为 EvolutionRepo 返回的 dict。
    返回字段:
    - record_count: 记录数
    - success_rate: 成功率
    - has_params_dimension: 是否有 params 维度的记录
    """
    if not history:
        return {}
    total = len(history)
    success_count = sum(1 for h in history if h.get("success"))
    has_params = any(h.get("dimension") == "params" for h in history)
    return {
        "record_count": total,
        "success_rate": success_count / total if total > 0 else 0,
        "has_params_dimension": has_params,
    }


def _parse_prompt(response: str) -> str:
    """从 LLM 响应提取 prompt 文本。

    优先从 ``` 代码块提取;无代码块则返回 trim 后的原文。
    """
    match = re.search(r"```\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def _parse_params(response: str) -> dict:
    """从 LLM 响应提取参数 dict。

    优先从 ```json 代码块提取;失败返回空 dict。
    """
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    # 尝试直接 parse 整个响应
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return {}


def _parse_skill_response(response: str) -> tuple[str, str]:
    """从 LLM 响应提取 skill 名 + 内容。

    格式:`# Skill: <name>` 在 markdown 代码块开头或内部。
    返回 (skill_name, skill_md_content)。
    """
    # 提取 markdown 代码块
    match = re.search(r"```(?:markdown)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    content = match.group(1) if match else response
    # 提取 skill 名
    name_match = re.search(r"#\s*Skill:\s*(\S+)", content)
    skill_name = name_match.group(1) if name_match else "auto_unknown"
    return skill_name, content.strip()


def _parse_skill_list(response: str) -> list[str]:
    """从 LLM 响应提取 skill 名列表。

    支持格式:逗号分隔、JSON 数组、每行一个。
    """
    # 尝试 JSON 数组
    match = re.search(r"\[([^\]]+)\]", response)
    if match:
        try:
            arr = json.loads(f"[{match.group(1)}]")
            if isinstance(arr, list):
                return [str(s).strip() for s in arr if str(s).strip()]
        except json.JSONDecodeError:
            pass
    # 尝试逗号分隔
    comma_match = re.search(r"[\w_]+(?:\s*,\s*[\w_]+)+", response)
    if comma_match:
        return [s.strip() for s in comma_match.group(0).split(",") if s.strip()]
    return []
```

- [ ] **Step 5: 运行新测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "summarize or successful or extract or compute or parse"`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```powershell
git add agentteam/runtime/evolution_prompts.py agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): LLM 指令模板 + 辅助函数(trace 解析/diff/parse)"
```

---

## Task 6: PromptOptimizer 维度

**Files:**
- Modify: `agentteam/runtime/evolution.py`(_optimize_prompt 实现)
- Modify: `tests/runtime/test_evolution.py`(追加测试)

- [ ] **Step 1: 写失败测试 — _optimize_prompt**

在 `d:\project\agentTeam\tests\runtime\test_evolution.py` 末尾追加:

```python
from langchain_core.messages import AIMessage


def test_optimize_prompt_no_change_skips_history():
    """LLM 返回相同 prompt → 不写 history,不更新 Agent。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="You are a coder.", version=1)
    trace = [{"event_type": "run_start", "payload": {"task": "x"}},
             {"event_type": "run_end"}]
    # mock LLM 返回相同 prompt
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nYou are a coder.\n```"
    )
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is True
    assert "no change" in result.reason.lower()
    engine._evo_repo.add_record.assert_not_called()
    engine._agent_library.update_prompt.assert_not_called()


def test_optimize_prompt_change_writes_history_and_updates_agent():
    """LLM 返回新 prompt → 写 history + 更新 Agent。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="old prompt", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nnew prompt with detail\n```"
    )
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is True
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "prompt"
    assert call_kwargs.kwargs["before_value"] == "old prompt"
    assert call_kwargs.kwargs["after_value"] == "new prompt with detail"
    assert call_kwargs.kwargs["success"] is True
    engine._agent_library.update_prompt.assert_called_once_with("coder", "new prompt with detail")


def test_optimize_prompt_llm_failure_records_error():
    """LLM 调用失败 → 写 success=False 的 history,不更新 Agent。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="p", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("LLM timeout")
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is False
    assert "error" in result.reason.lower() or result.error is not None
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["success"] is False
    assert call_kwargs.kwargs["error"] is not None
    engine._agent_library.update_prompt.assert_not_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "optimize_prompt"`
Expected: 3 FAIL(当前 `_optimize_prompt` 返回 "not implemented yet",不写 history)

- [ ] **Step 3: 实现 — _optimize_prompt**

修改 `d:\project\agentTeam\agentteam\runtime\evolution.py` 的 `_optimize_prompt`,替换占位实现:

```python
    def _optimize_prompt(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 1:分析 trace + LLM 重写 system_prompt。

        LLM 返回相同 prompt → 不写 history,不更新 Agent。
        LLM 返回新 prompt → 写 history + 更新 Agent。
        LLM 失败 → 写 success=False history,不更新 Agent。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import PROMPT_OPTIMIZER_INSTRUCTION

        try:
            old_prompt = agent.system_prompt
            trace_summary = _summarize_trace(trace)

            llm = self._mp.get_llm(None)
            response = llm.invoke([
                SystemMessage(content=PROMPT_OPTIMIZER_INSTRUCTION),
                HumanMessage(content=(
                    f"当前 system_prompt:\n{old_prompt}\n\n"
                    f"本次 run trace:\n{trace_summary}\n\n"
                    f"请基于 trace 分析 prompt 是否需要优化。"
                    f"若需优化给出新版本,若已合理则原样返回。"
                )),
            ])
            new_prompt = _parse_prompt(response.content)

            if new_prompt == old_prompt:
                return EvolutionResult(True, "prompt", "no change needed")

            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="prompt",
                before_value=old_prompt, after_value=new_prompt,
                diff=_compute_diff(old_prompt, new_prompt),
                reason=response.content, run_id=run_id, success=True,
            )
            self._lib.update_prompt(agent.name, new_prompt)
            return EvolutionResult(True, "prompt", "prompt updated")
        except Exception as e:
            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="prompt", before_value="", after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "prompt", "error", str(e))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "optimize_prompt"`
Expected: 3 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): PromptOptimizer 维度 — LLM 重写 system_prompt + history 记录"
```

---

## Task 7: ParamTuner 维度

**Files:**
- Modify: `agentteam/runtime/evolution.py`(_tune_params 实现)
- Modify: `tests/runtime/test_evolution.py`(追加测试)

- [ ] **Step 1: 写失败测试 — _tune_params**

在 `d:\project\agentTeam\tests\runtime\test_evolution.py` 末尾追加:

```python
def test_tune_params_no_change_skips_history():
    """LLM 返回相同参数 → 不写 history,不更新 Agent。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(mode="never"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evo_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 5, "approval_policy": {"mode": "never"}}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is True
    engine._evo_repo.add_record.assert_not_called()
    engine._agent_library.update_params.assert_not_called()


def test_tune_params_change_writes_history_and_updates_agent():
    """LLM 返回新参数 → 写 history + 更新 Agent。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(mode="never"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evo_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 15}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is True
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "params"
    assert call_kwargs.kwargs["success"] is True
    engine._agent_library.update_params.assert_called_once()
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[0] == "coder"
    assert update_args.args[1]["max_iterations"] == 15


def test_tune_params_clamps_max_iterations_to_range():
    """max_iterations 边界保护:LLM 返回 100 → clamp 到 20。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(mode="never"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evo_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 100}\n```'
    )
    engine._tune_params(agent, trace, "r1")
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[1]["max_iterations"] == 20


def test_tune_params_clamps_max_iterations_to_min_1():
    """max_iterations 边界保护:LLM 返回 0 → clamp 到 1。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(mode="never"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evo_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 0}\n```'
    )
    engine._tune_params(agent, trace, "r1")
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[1]["max_iterations"] == 1


def test_tune_params_llm_failure_records_error():
    """LLM 失败 → 写 success=False history。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(mode="never"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evo_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("fail")
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is False
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["success"] is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "tune_params"`
Expected: 5 FAIL(当前占位实现)

- [ ] **Step 3: 实现 — _tune_params**

修改 `d:\project\agentTeam\agentteam\runtime\evolution.py` 的 `_tune_params`:

```python
    def _tune_params(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 2:统计历史 N 次 run + LLM 建议参数调整。

        边界保护:max_iterations 限制 [1, 20]。
        LLM 返回与当前相同 → 不写 history。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import PARAM_TUNER_INSTRUCTION
        import json as _json

        try:
            history = self._evo_repo.list_recent_runs(agent.name, limit=5)
            stats = _compute_stats(history)

            current_params = {
                "max_iterations": agent.max_iterations,
                "approval_policy": (
                    agent.approval_policy.model_dump()
                    if hasattr(agent.approval_policy, "model_dump")
                    else _json.dumps(agent.approval_policy.__dict__) if agent.approval_policy else None
                ),
            }

            llm = self._mp.get_llm(None)
            response = llm.invoke([
                SystemMessage(content=PARAM_TUNER_INSTRUCTION),
                HumanMessage(content=(
                    f"当前参数: {current_params}\n"
                    f"最近 {len(history)} 次统计: {stats}\n"
                    f"建议调整(只给必要改动,否则返回空 dict)。"
                )),
            ])
            new_params = _parse_params(response.content)

            # 边界保护:max_iterations 限制 [1, 20]
            if "max_iterations" in new_params:
                new_params["max_iterations"] = max(1, min(20, int(new_params["max_iterations"])))

            # 判断是否有变化(只比对实际会改动的字段)
            has_change = False
            if "max_iterations" in new_params and new_params["max_iterations"] != agent.max_iterations:
                has_change = True
            # approval_policy 比对省略(复杂对象,简化为有 key 即视为改动)
            if "approval_policy" in new_params:
                has_change = True

            if not has_change:
                return EvolutionResult(True, "params", "no change needed")

            old_params = {
                "max_iterations": agent.max_iterations,
                "approval_policy": current_params["approval_policy"],
            }
            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="params",
                before_value=_json.dumps(old_params, default=str),
                after_value=_json.dumps(new_params, default=str),
                diff="", reason=response.content, run_id=run_id, success=True,
            )
            self._lib.update_params(agent.name, new_params)
            return EvolutionResult(True, "params", "params tuned")
        except Exception as e:
            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="params", before_value="", after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "params", "error", str(e))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "tune_params"`
Expected: 5 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): ParamTuner 维度 — LLM 建议参数调整 + 边界保护"
```

---

## Task 8: SkillGenerator 维度

**Files:**
- Modify: `agentteam/runtime/evolution.py`(_generate_skill 实现)
- Modify: `tests/runtime/test_evolution.py`(追加测试)

- [ ] **Step 1: 写失败测试 — _generate_skill**

在 `d:\project\agentTeam\tests\runtime\test_evolution.py` 末尾追加:

```python
def test_generate_skill_skips_failed_run():
    """run 失败 → 跳过(不调用 LLM)。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "error", "payload": {"error": "x"}}]  # 无 run_end
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "skip" in result.reason.lower() or "failed" in result.reason.lower()
    engine._mp.get_llm.assert_not_called()


def test_generate_skill_no_skills_dir_skips():
    """skills_dir=None → 跳过。"""
    engine = _make_engine(skills_dir=None)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "no skills_dir" in result.reason.lower() or "skip" in result.reason.lower()


def test_generate_skill_llm_returns_skip():
    """LLM 返回 SKIP → 不生成文件。"""
    engine = _make_engine(skills_dir=MagicMock())
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(content="SKIP")
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "no reusable" in result.reason.lower() or "skip" in result.reason.lower()


def test_generate_skill_creates_file_and_notifies_loader(tmp_path):
    """LLM 返回 skill → 写入 auto_*.md + 通知 SkillLoader.reload + 写 history。"""
    from unittest.mock import MagicMock
    mock_loader = MagicMock()
    engine = _make_engine(skill_loader=mock_loader, skills_dir=tmp_path)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```markdown\n# Skill: auto_pattern1\ndo x then y\n```"
    )
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    # 文件已创建
    skill_path = tmp_path / "auto_pattern1.md"
    assert skill_path.exists()
    assert "auto_pattern1" in skill_path.read_text(encoding="utf-8")
    # SkillLoader.reload 被调用
    mock_loader.reload.assert_called_once()
    # history 写入
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "skill_gen"
    assert call_kwargs.kwargs["success"] is True


def test_generate_skill_existing_file_appends_version(tmp_path):
    """auto_X.md 已存在 → 写 auto_X_v2.md。"""
    from unittest.mock import MagicMock
    # 预创建 auto_pattern1.md
    (tmp_path / "auto_pattern1.md").write_text("existing", encoding="utf-8")
    mock_loader = MagicMock()
    engine = _make_engine(skill_loader=mock_loader, skills_dir=tmp_path)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```markdown\n# Skill: auto_pattern1\nnew content\n```"
    )
    engine._generate_skill(agent, trace, "r1")
    # 应写入 auto_pattern1_v2.md(不覆盖原文件)
    assert (tmp_path / "auto_pattern1_v2.md").exists()
    assert (tmp_path / "auto_pattern1.md").read_text(encoding="utf-8") == "existing"


def test_generate_skill_llm_failure_records_error():
    """LLM 失败 → 写 success=False history。"""
    engine = _make_engine(skills_dir=MagicMock())
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("fail")
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is False
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["success"] is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "generate_skill"`
Expected: 6 FAIL(占位实现)

- [ ] **Step 3: 实现 — _generate_skill**

修改 `d:\project\agentTeam\agentteam\runtime\evolution.py` 的 `_generate_skill`:

```python
    def _generate_skill(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 3:从成功 run 提炼 skill。

        仅在 run 成功时尝试;LLM 返回 SKIP 跳过;
        生成的 skill 命名 auto_*.md,已存在则附加 _v2/_v3。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import SKILL_GENERATOR_INSTRUCTION

        try:
            if not _is_successful_run(trace):
                return EvolutionResult(True, "skill_gen", "run failed, skip")

            if self._skills_dir is None:
                return EvolutionResult(True, "skill_gen", "no skills_dir configured")

            task = _extract_task(trace)
            tool_calls = _extract_tool_calls(trace)
            final_answer = _extract_final_answer(trace)

            llm = self._mp.get_llm(None)
            response = llm.invoke([
                SystemMessage(content=SKILL_GENERATOR_INSTRUCTION),
                HumanMessage(content=(
                    f"Agent: {agent.name} (role={agent.role})\n"
                    f"Task: {task}\n"
                    f"Tool calls: {tool_calls}\n"
                    f"Final answer: {final_answer[:500]}\n\n"
                    f"从本次成功执行中提炼可复用的 skill 模式。"
                    f"若无可复用模式则返回 SKIP。"
                    f"否则返回 markdown skill 内容,开头用 '# Skill: auto_<name>' 标注。"
                )),
            ])

            if response.content.strip() == "SKIP":
                return EvolutionResult(True, "skill_gen", "no reusable pattern")

            skill_name, skill_md = _parse_skill_response(response.content)

            # 处理重名:auto_X.md 已存在 → auto_X_v2.md
            skill_path = self._skills_dir / f"{skill_name}.md"
            if skill_path.exists():
                version = 2
                while (self._skills_dir / f"{skill_name}_v{version}.md").exists():
                    version += 1
                skill_path = self._skills_dir / f"{skill_name}_v{version}.md"

            skill_path.write_text(skill_md, encoding="utf-8")

            # 通知 SkillLoader 重载缓存
            if self._skill_loader is not None:
                self._skill_loader.reload()

            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_gen", before_value="",
                after_value=str(skill_path),
                diff="", reason=f"Generated skill: {skill_name}",
                run_id=run_id, success=True,
            )
            return EvolutionResult(True, "skill_gen", f"generated {skill_name}")
        except Exception as e:
            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_gen", before_value="", after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "skill_gen", "error", str(e))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "generate_skill"`
Expected: 6 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): SkillGenerator 维度 — LLM 提炼 skill 写入 auto_*.md"
```

---

## Task 9: SkillSelector 维度

**Files:**
- Modify: `agentteam/runtime/evolution.py`(_select_skills 实现)
- Modify: `tests/runtime/test_evolution.py`(追加测试)

- [ ] **Step 1: 写失败测试 — _select_skills**

在 `d:\project\agentTeam\tests\runtime\test_evolution.py` 末尾追加:

```python
def test_select_skills_no_skill_loader_skips():
    """skill_loader=None → 跳过。"""
    engine = _make_engine(skill_loader=None)
    agent = Agent(name="coder", role="worker", skills=[], version=1)
    trace = [{"event_type": "run_end"}]
    result = engine._select_skills(agent, trace, "r1")
    assert result.success is True
    assert "no" in result.reason.lower() or "skip" in result.reason.lower()


def test_select_skills_no_candidates_skips():
    """候选 skill 为空(已装备全部) → 跳过。"""
    mock_loader = MagicMock()
    mock_loader.list_available.return_value = ["code_review"]
    engine = _make_engine(skill_loader=mock_loader)
    agent = Agent(name="coder", role="worker", skills=["code_review"], version=1)
    trace = [{"event_type": "run_end"}]
    result = engine._select_skills(agent, trace, "r1")
    assert result.success is True
    assert "no new" in result.reason.lower() or "skip" in result.reason.lower()


def test_select_skills_llm_returns_empty_no_recommendation():
    """LLM 返回空推荐 → 不写 history。"""
    mock_loader = MagicMock()
    mock_loader.list_available.return_value = ["code_review", "testing"]
    engine = _make_engine(skill_loader=mock_loader)
    agent = Agent(name="coder", role="worker", skills=[], version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(content="no recommendation")
    result = engine._select_skills(agent, trace, "r1")
    assert result.success is True
    assert "no recommendation" in result.reason.lower()
    engine._evo_repo.add_record.assert_not_called()


def test_select_skills_recommended_writes_history_does_not_modify_agent():
    """LLM 推荐成功 → 写 history,但不直接改 Agent.skills(软推荐)。"""
    mock_loader = MagicMock()
    mock_loader.list_available.return_value = ["code_review", "testing"]
    engine = _make_engine(skill_loader=mock_loader)
    agent = Agent(name="coder", role="worker", skills=[], version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="推荐: code_review, testing"
    )
    result = engine._select_skills(agent, trace, "r1")
    assert result.success is True
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "skill_select"
    assert call_kwargs.kwargs["success"] is True
    # 关键:Agent.skills 未被直接修改
    assert agent.skills == []
    # AgentLibrary.update_params 等修改方法未被调用
    engine._agent_library.update_params.assert_not_called()


def test_select_skills_llm_failure_records_error():
    """LLM 失败 → 写 success=False history。"""
    mock_loader = MagicMock()
    mock_loader.list_available.return_value = ["code_review"]
    engine = _make_engine(skill_loader=mock_loader)
    agent = Agent(name="coder", role="worker", skills=[], version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("fail")
    result = engine._select_skills(agent, trace, "r1")
    assert result.success is False
    engine._evo_repo.add_record.assert_called_once()
    call_kwargs = engine._evo_repo.add_record.call_args
    assert call_kwargs.kwargs["success"] is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "select_skills"`
Expected: 5 FAIL(占位实现)

- [ ] **Step 3: 实现 — _select_skills**

修改 `d:\project\agentTeam\agentteam\runtime\evolution.py` 的 `_select_skills`:

```python
    def _select_skills(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 4:任务匹配 skill 软推荐。

        不直接改 Agent.skills,仅写 history 供用户 review 后手动 apply。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import SKILL_SELECTOR_INSTRUCTION
        import json as _json

        try:
            if self._skill_loader is None:
                return EvolutionResult(True, "skill_select", "no skill_loader configured")

            available = self._skill_loader.list_available()
            candidates = [s for s in available if s not in agent.skills]
            if not candidates:
                return EvolutionResult(True, "skill_select", "no new skills to recommend")

            task = _extract_task(trace)
            llm = self._mp.get_llm(None)
            response = llm.invoke([
                SystemMessage(content=SKILL_SELECTOR_INSTRUCTION),
                HumanMessage(content=(
                    f"Agent: {agent.name}, role={agent.role}\n"
                    f"Task: {task}\n"
                    f"Already equipped skills: {agent.skills}\n"
                    f"Candidate skills: {candidates}\n\n"
                    f"推荐本次任务应装备的 skill(可多选,空则不推荐)。"
                )),
            ])
            recommended = _parse_skill_list(response.content)
            if not recommended:
                return EvolutionResult(True, "skill_select", "no recommendation")

            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_select",
                before_value=_json.dumps(agent.skills),
                after_value=_json.dumps(recommended),
                diff="", reason=f"Recommended for tasks like: {task[:100]}",
                run_id=run_id, success=True,
            )
            return EvolutionResult(True, "skill_select", f"recommended {recommended}")
        except Exception as e:
            self._evo_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_select", before_value="", after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "skill_select", "error", str(e))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/runtime/test_evolution.py -v -k "select_skills"`
Expected: 5 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/runtime/evolution.py tests/runtime/test_evolution.py
git commit -m "feat(runtime): SkillSelector 维度 — LLM 软推荐 skill 写 history"
```

---

## Task 10: RunManager 触发机制

**Files:**
- Modify: `agentteam/api/run_manager.py:35-50`(__init__ 加 evolution_engine)
- Modify: `agentteam/api/run_manager.py:256-282`(_handle_invoke_result 触发)
- Modify: `agentteam/api/run_manager.py:284-330`(_handle_error 触发,RunCancelledError 不触发)
- Modify: `tests/api/test_run_cancel.py` 或新建 `tests/api/test_evolution_trigger.py`

- [ ] **Step 1: 写失败测试 — RunManager 触发 EvolutionEngine**

创建 `d:\project\agentTeam\tests\api\test_evolution_trigger.py`:

```python
"""SP7b: RunManager 触发 EvolutionEngine 测试。"""
import threading
from unittest.mock import MagicMock

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunCancelledError, RunManager


def _make_run_manager(evolution_engine=None):
    return RunManager(
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
        evolution_engine=evolution_engine,
    )


def test_run_manager_accepts_evolution_engine_param():
    """RunManager.__init__ 接受 evolution_engine 参数(默认 None)。"""
    rm = _make_run_manager()
    assert rm._evolution is None


def test_handle_invoke_result_completed_triggers_evolution():
    """run completed → 异步触发 evolution.trigger。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    # mock graph.get_state 返回 completed 状态
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []  # 无 next → completed
    mock_state.values = {"total_tokens": 100}
    mock_graph.get_state.return_value = mock_state

    rm._handle_invoke_result("r1", mock_graph, {"configurable": {"thread_id": "r1"}})
    # 等待 daemon thread 执行
    import time
    time.sleep(0.1)
    mock_evo.trigger.assert_called_once_with("r1")


def test_handle_invoke_result_interrupted_does_not_trigger_evolution():
    """run interrupted → 不触发 evolution(等待用户 approve)。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = ["some_node"]  # 有 next → interrupted
    mock_graph.get_state.return_value = mock_state

    rm._handle_invoke_result("r1", mock_graph, {})
    import time
    time.sleep(0.1)
    mock_evo.trigger.assert_not_called()


def test_handle_error_failed_triggers_evolution():
    """run failed(普通异常)→ 触发 evolution。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RuntimeError("bug"))
    import time
    time.sleep(0.1)
    mock_evo.trigger.assert_called_once_with("r1")


def test_handle_error_cancelled_does_not_trigger_evolution():
    """run cancelled(RunCancelledError)→ 不触发 evolution(用户主动取消)。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RunCancelledError())
    import time
    time.sleep(0.1)
    mock_evo.trigger.assert_not_called()


def test_no_evolution_engine_does_not_raise():
    """evolution_engine=None 时,_handle_invoke_result/_handle_error 不抛异常。"""
    rm = _make_run_manager(evolution_engine=None)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []
    mock_state.values = {"total_tokens": 0}
    mock_graph.get_state.return_value = mock_state
    # 应正常执行,不抛 AttributeError
    rm._handle_invoke_result("r1", mock_graph, {})
    rm._handle_error("r1", RuntimeError("x"))
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_evolution_trigger.py -v`
Expected: FAIL(`TypeError: RunManager.__init__() got an unexpected keyword argument 'evolution_engine'`)

- [ ] **Step 3: 实现 — RunManager.__init__ 加 evolution_engine**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py:35-50` 的 `__init__`:

```python
    def __init__(
        self,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        event_bus: EventBus,
        checkpointer=None,
        evolution_engine=None,
    ) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._saver = checkpointer
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._evolution = evolution_engine
```

- [ ] **Step 4: 实现 — _handle_invoke_result 触发**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py:256-282` 的 `_handle_invoke_result`,在 `end_run("completed")` 后追加触发逻辑:

```python
    def _handle_invoke_result(self, run_id: str, graph, config: dict) -> None:
        # (现有 docstring 与 try/except 逻辑保持不变)
        try:
            state = graph.get_state(config)
        except Exception:
            self._run_repo.update_status(run_id, "interrupted")
            self._bus.publish(
                run_id, {"event_type": "run_interrupted", "run_id": run_id}
            )
            return
        if state.next:
            # interrupted:不触发 evolution
            self._run_repo.update_status(run_id, "interrupted")
            self._bus.publish(run_id, {"event_type": "run_interrupted", "run_id": run_id})
        else:
            tokens = state.values.get("total_tokens", 0) if state.values else 0
            self._run_repo.end_run(run_id, "completed", total_tokens=tokens)
            eid = self._audit_repo.add_event(run_id, "run_end", "system")
            self._bus.publish(
                run_id, {"id": eid, "event_type": "run_end", "run_id": run_id}
            )
            self._cleanup_run(run_id)
            # SP7b: completed 后异步触发进化
            self._trigger_evolution_async(run_id)
```

- [ ] **Step 5: 实现 — _handle_error 触发(RunCancelledError 不触发)**

修改 `d:\project\agentTeam\agentteam\api\run_manager.py:284-330` 的 `_handle_error`,在 `_cleanup_run` 之后追加:

```python
    def _handle_error(self, run_id: str, error: BaseException) -> None:
        """(现有 docstring)"""
        if isinstance(error, RunCancelledError):
            self._run_repo.end_run(run_id, "cancelled")
            eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
            self._bus.publish(
                run_id,
                {"id": eid, "event_type": "run_cancelled", "run_id": run_id,
                 "payload": {"reason": "user requested cancel"}},
            )
            # cancelled 不触发进化(用户主动取消,数据可能不完整)
        else:
            self._run_repo.end_run(run_id, "failed")
            eid = self._audit_repo.add_event(run_id, "error", "system", {"error": str(error)})
            self._bus.publish(
                run_id,
                {"id": eid, "event_type": "error", "run_id": run_id,
                 "payload": {"error": str(error)}},
            )
            # failed 触发进化(失败 run 也是学习素材)
            self._trigger_evolution_async(run_id)
        self._cleanup_run(run_id)
```

- [ ] **Step 6: 实现 — 新增 _trigger_evolution_async helper**

在 `_cleanup_run` 方法之后新增:

```python
    def _trigger_evolution_async(self, run_id: str) -> None:
        """异步触发 EvolutionEngine(SP7b)。

        daemon thread:不阻塞 API 响应,失败不影响 run 结果。
        evolution_engine=None 时静默跳过(向后兼容)。
        """
        if self._evolution is None:
            return
        threading.Thread(
            target=self._evolution.trigger,
            args=(run_id,),
            daemon=True,
        ).start()
```

- [ ] **Step 7: 运行新测试验证通过**

Run: `python -m pytest tests/api/test_evolution_trigger.py -v`
Expected: 6 PASS

- [ ] **Step 8: 运行现有 run_manager 测试验证无回归**

Run: `python -m pytest tests/api/test_run_cancel.py tests/api/ -v`
Expected: 全部 PASS

- [ ] **Step 9: 提交**

```powershell
git add agentteam/api/run_manager.py tests/api/test_evolution_trigger.py
git commit -m "feat(api): RunManager 在 run 终态后异步触发 EvolutionEngine(cancelled 不触发)"
```

---

## Task 11: 回滚 API + history endpoint

**Files:**
- Create: `agentteam/api/routes/evolution.py`
- Create: `tests/api/test_api_evolution.py`

- [ ] **Step 1: 写失败测试 — history + rollback endpoint**

创建 `d:\project\agentTeam\tests\api\test_api_evolution.py`:

```python
"""SP7b: Evolution API 测试。"""
import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.routes.evolution import evolution_router
from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


def _make_app():
    """构造测试 app,mock repo + 真实 AgentLibrary。"""
    evo_repo = MagicMock()
    lib = AgentLibrary()
    app = FastAPI()
    app.include_router(evolution_router(evo_repo, lib))
    return app, evo_repo, lib


def test_list_history_returns_records():
    """GET /api/agents/{name}/history 返回 history 列表。"""
    app, evo_repo, _ = _make_app()
    evo_repo.list_history.return_value = [
        {"id": 1, "agent_name": "coder", "version": 1, "dimension": "prompt"},
    ]
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["history"]) == 1
    assert body["history"][0]["dimension"] == "prompt"


def test_list_history_empty_returns_empty_list():
    """无 history → 返回空列表。"""
    app, evo_repo, _ = _make_app()
    evo_repo.list_history.return_value = []
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/history")
    assert resp.status_code == 200
    assert resp.json() == {"history": []}


def test_get_version_returns_snapshot():
    """GET /api/agents/{name}/versions/{v} 返回 version 快照。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "prompt", "before_value": "old", "after_value": "new"},
    ]
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/versions/2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert len(body["records"]) == 1


def test_get_version_unknown_returns_404():
    """未知 version → 404。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = []
    with TestClient(app) as client:
        resp = client.get("/api/agents/coder/versions/999")
    assert resp.status_code == 404


def test_rollback_applies_before_value_and_increments_version():
    """POST /api/agents/{name}/rollback?version=N 成功回滚 + version 递增。"""
    from agentteam.domain.approval import ApprovalPolicy
    app, evo_repo, lib = _make_app()
    # 预置 agent
    lib.register(Agent(
        name="coder", role="worker",
        system_prompt="current", max_iterations=10,
        approval_policy=ApprovalPolicy(mode="never"),
        version=5,
    ))
    # mock version 2 的 snapshot:prompt + params
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "prompt", "before_value": "old_prompt"},
        {"dimension": "params", "before_value": json.dumps({
            "max_iterations": 3,
            "approval_policy": {"mode": "always", "tools": ["x"]},
        })},
    ]
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["new_version"] == 6  # 5 + 1
    # Agent 已被回滚
    agent = lib.get("coder")
    assert agent.system_prompt == "old_prompt"
    assert agent.max_iterations == 3
    assert agent.version == 6
    # rollback 记录已写入 history
    evo_repo.add_record.assert_called_once()
    call_kwargs = evo_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "rollback"
    assert call_kwargs.kwargs["success"] is True


def test_rollback_unknown_version_returns_404():
    """未知 version → 404,不修改 agent。"""
    app, evo_repo, lib = _make_app()
    lib.register(Agent(name="coder", role="worker", version=1))
    evo_repo.get_version_snapshot.return_value = []
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=999")
    assert resp.status_code == 404


def test_rollback_unknown_agent_returns_404():
    """未知 agent → 404。"""
    app, evo_repo, _ = _make_app()
    evo_repo.get_version_snapshot.return_value = [{"dimension": "prompt", "before_value": "x"}]
    with TestClient(app) as client:
        resp = client.post("/api/agents/nonexistent/rollback?version=1")
    assert resp.status_code == 404


def test_rollback_does_not_revert_skill_gen_or_select():
    """rollback 不回滚 skill_gen / skill_select(已生成的文件保留)。"""
    app, evo_repo, lib = _make_app()
    lib.register(Agent(
        name="coder", role="worker",
        system_prompt="current", skills=["auto_x"], version=3,
    ))
    evo_repo.get_version_snapshot.return_value = [
        {"dimension": "skill_gen", "before_value": "", "after_value": "/skills/auto_x.md"},
        {"dimension": "skill_select", "before_value": "[]", "after_value": '["auto_x"]'},
    ]
    with TestClient(app) as client:
        resp = client.post("/api/agents/coder/rollback?version=2")
    assert resp.status_code == 200
    # skills 未被回滚(保留 auto_x)
    agent = lib.get("coder")
    assert agent.skills == ["auto_x"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_api_evolution.py -v`
Expected: FAIL(`ImportError: cannot import name 'evolution_router' from 'agentteam.api.routes.evolution'`)

- [ ] **Step 3: 实现 — 创建 evolution_router**

创建 `d:\project\agentTeam\agentteam\api\routes\evolution.py`:

```python
"""SP7b Evolution API:history 查询 + rollback。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agentteam.domain.library import AgentLibrary
from agentteam.storage.evolution import EvolutionRepo


def evolution_router(evolution_repo: EvolutionRepo, agent_library: AgentLibrary) -> APIRouter:
    """构造 /api/agents evolution 相关路由。

    endpoints:
    - GET  /api/agents/{name}/history          — 查询 agent 进化历史
    - GET  /api/agents/{name}/versions/{v}     — 取指定 version 快照
    - POST /api/agents/{name}/rollback?v=N     — 回滚到 version N
    """
    router = APIRouter(prefix="/api/agents", tags=["evolution"])

    @router.get("/{agent_name}/history")
    def list_history(agent_name: str, limit: int = 20):
        return {"history": evolution_repo.list_history(agent_name, limit)}

    @router.get("/{agent_name}/versions/{version}")
    def get_version(agent_name: str, version: int):
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        return {"version": version, "records": records}

    @router.post("/{agent_name}/rollback")
    def rollback_agent(agent_name: str, version: int):
        # 1. 取目标 version 的所有 history 记录
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")

        # 2. 把 before_value 应用回 Agent
        agent = agent_library.get(agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        for record in records:
            dimension = record.get("dimension")
            before_value = record.get("before_value", "")
            if dimension == "prompt":
                agent.system_prompt = before_value
            elif dimension == "params":
                try:
                    params = json.loads(before_value)
                    if "max_iterations" in params:
                        agent.max_iterations = params["max_iterations"]
                    if "approval_policy" in params:
                        # approval_policy 已是 dict,转回 ApprovalPolicy 对象
                        from agentteam.domain.approval import ApprovalPolicy
                        ap = params["approval_policy"]
                        if isinstance(ap, dict):
                            agent.approval_policy = ApprovalPolicy(**ap)
                        else:
                            agent.approval_policy = ap
                except (json.JSONDecodeError, TypeError):
                    pass  # 跳过损坏的 params 记录
            # skill_gen / skill_select 不回滚(已写入文件的 skill 保留)

        # 3. 写新 history 记录(类型: rollback)
        new_version = agent.version + 1
        evolution_repo.add_record(
            agent_name=agent_name, version=new_version, dimension="rollback",
            before_value=f"v{agent.version}", after_value=f"v{version}",
            diff="", reason=f"User rolled back to v{version}",
            run_id=None, success=True,
        )
        agent.version = new_version
        agent_library.update_version(agent_name, new_version)
        return {"ok": True, "new_version": new_version}

    return router
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `python -m pytest tests/api/test_api_evolution.py -v`
Expected: 8 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/api/routes/evolution.py tests/api/test_api_evolution.py
git commit -m "feat(api): GET history + GET version + POST rollback endpoint"
```

---

## Task 12: server.py 集成 + 全量回归

**Files:**
- Modify: `agentteam/api/server.py:36-102`(create_app 集成 EvolutionRepo + EvolutionEngine)

- [ ] **Step 1: 修改 server.py 创建 EvolutionRepo + EvolutionEngine 并注入**

修改 `d:\project\agentTeam\agentteam\api\server.py`:

1a. 在 import 区(第 1-27 行附近)新增:

```python
from agentteam.api.routes.evolution import evolution_router
from agentteam.runtime.evolution import EvolutionEngine
from agentteam.storage.evolution import EvolutionRepo
```

1b. 在 `lib = ...` 之后(第 75 行附近)新增:

```python
    lib = agent_library or AgentLibrary(repo=library_repo)
    skill_loader = SkillLoader(skills_dir)
    evolution_repo = EvolutionRepo(conn, lock=conn_lock)
    evolution_engine = EvolutionEngine(
        model_provider=mp,
        agent_library=lib,
        evolution_repo=evolution_repo,
        run_repo=run_repo,
        audit_repo=audit_repo,
        skill_loader=skill_loader,
        skills_dir=skills_dir,
    )
```

1c. 修改 `RunManager(...)` 构造(第 72 行),传入 `evolution_engine`:

```python
    run_manager = RunManager(
        run_repo, audit_repo, event_bus,
        checkpointer=saver, evolution_engine=evolution_engine,
    )
```

1d. 在 `app.include_router(...)` 区(第 77-86 行)新增 `evolution_router`:

```python
    app.include_router(skills_router(skill_loader))
    app.include_router(evolution_router(evolution_repo, lib))
```

- [ ] **Step 2: 写集成测试 — server.py 集成验证**

在 `d:\project\agentTeam\tests\api\test_api_evolution.py` 末尾追加:

```python
def test_create_app_includes_evolution_routes(tmp_path):
    """create_app 集成后,evolution endpoint 可访问。"""
    from agentteam.api.server import create_app
    from agentteam.models.provider import ModelProvider
    from agentteam.tools.registry import ToolRegistry

    app = create_app(
        db_path=str(tmp_path / "test.db"),
        model_provider=ModelProvider(),
        tool_registry=ToolRegistry(),
        skills_dir=tmp_path,
        web_dist=None,
    )
    with TestClient(app) as client:
        # history endpoint 可访问(返回空,因无 agent)
        resp = client.get("/api/agents/nonexistent/history")
        assert resp.status_code == 200
        assert resp.json() == {"history": []}
```

- [ ] **Step 3: 运行新测试验证通过**

Run: `python -m pytest tests/api/test_api_evolution.py::test_create_app_includes_evolution_routes -v`
Expected: PASS

- [ ] **Step 4: 运行全量测试套件验证无回归**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS(SP7b 新增约 50+ 个测试 + 现有测试无回归)

- [ ] **Step 5: 运行 SP7a 测试验证集成无冲突**

Run: `python -m pytest tests/runtime/test_skills.py tests/api/test_api_skills.py -v`
Expected: 全部 PASS(SP7a 功能不受 SP7b 影响)

- [ ] **Step 6: 提交**

```powershell
git add agentteam/api/server.py tests/api/test_api_evolution.py
git commit -m "feat(api): server.py 集成 EvolutionRepo + EvolutionEngine"
```

---

## Self-Review

### 1. Spec 覆盖检查

| Spec 章节 | 对应 Task | 状态 |
|----------|----------|------|
| §3.1 EvolutionEngine 主控 | Task 4 | ✅ |
| §3.2 维度 1 PromptOptimizer | Task 6 | ✅ |
| §3.2 维度 2 ParamTuner | Task 7 | ✅ |
| §3.2 维度 3 SkillGenerator | Task 8 | ✅ |
| §3.2 维度 4 SkillSelector | Task 9 | ✅ |
| §3.3 触发机制(RunManager) | Task 10 | ✅ |
| §3.4 数据模型(evolution_history 表 + version 字段) | Task 1, 2 | ✅ |
| §3.4 AgentLibrary 新增方法 | Task 3 | ✅ |
| §3.5 EvolutionRepo | Task 1, 2 | ✅ |
| §3.6 回滚 API | Task 11 | ✅ |
| §3.7 LLM 指令模板 | Task 5 | ✅ |
| §3.8 关键设计权衡(顺序/软推荐/不覆盖/独立失败/版本/异步/防抖/cancelled) | Task 4-10 | ✅ |
| §3.10 测试策略(单元 + 存储 + API + 集成 + 回归) | Task 1-12 | ✅ |

### 2. 类型一致性检查

- `EvolutionRepo.add_record(agent_name, version, dimension, before_value, after_value, diff, reason, run_id, success, error=None) -> int` — Task 1 定义,Task 6/7/8/9/11 使用 ✅
- `EvolutionRepo.list_history(agent_name, limit=20) -> list[dict]` — Task 1 定义,Task 11 使用 ✅
- `EvolutionRepo.get_version_snapshot(agent_name, version) -> list[dict]` — Task 2 定义,Task 11 使用 ✅
- `EvolutionRepo.list_recent_runs(agent_name, limit=5) -> list[dict]` — Task 2 定义,Task 7 使用 ✅
- `AgentLibrary.update_version(name, version)` — Task 3 定义,Task 4/11 使用 ✅
- `AgentLibrary.update_prompt(name, new_prompt)` — Task 3 定义,Task 6 使用 ✅
- `AgentLibrary.update_params(name, params: dict)` — Task 3 定义,Task 7 使用 ✅
- `EvolutionResult(success, dimension, reason, error=None)` — Task 4 定义,Task 6-9 使用 ✅
- `EvolutionEngine.__init__(model_provider, agent_library, evolution_repo, run_repo, audit_repo, skill_loader=None, skills_dir=None)` — Task 4 定义,Task 12 使用 ✅
- `RunManager.__init__(..., evolution_engine=None)` — Task 10 定义,Task 12 使用 ✅
- `_optimize_prompt(agent, trace, run_id) -> EvolutionResult` — Task 6 定义,Task 4 调用 ✅
- `_parse_prompt / _parse_params / _parse_skill_response / _parse_skill_list` — Task 5 定义,Task 6-9 使用 ✅
- `evolution_router(evolution_repo, agent_library) -> APIRouter` — Task 11 定义,Task 12 使用 ✅

### 3. 占位符扫描

- 无 TBD/TODO/placeholder
- 每个 Step 都有完整代码或命令
- 测试代码完整可执行
- 实现代码完整可粘贴
- 4 个维度占位实现(`return EvolutionResult(True, ..., "not implemented yet")`)在 Task 6-9 中被替换为真实实现

### 4. 风险点

- **Task 10 测试时序**:daemon thread 异步触发,测试用 `time.sleep(0.1)` 等待。在慢机器上可能 flaky。若 CI 失败可改为 `time.sleep(0.5)` 或用 `threading.Event` 同步。
- **Task 7 ApprovalPolicy 序列化**:`ApprovalPolicy` 可能是 pydantic model 或 dataclass,`model_dump()` 与 `__dict__` 的兼容性需在实现时验证。fallback 用 `default=str` 的 json.dumps。
- **Task 8 SkillGenerator 文件名**:`auto_<pattern>` 中的 pattern 可能含特殊字符。实现时若需可加 sanitize(只保留 `[a-zA-Z0-9_]`)。当前测试用 `auto_pattern1` 安全命名。
- **Task 11 rollback 的 approval_policy 反序列化**:`ApprovalPolicy(**dict)` 假设构造函数接受 kwargs。若是 dataclass 用 `ApprovalPolicy(**ap)` 也能工作,但需验证。
- **Task 12 server.py 集成**:EvolutionEngine 需要 model_provider,但 `mp = model_provider or ModelProvider()` 在 EvolutionEngine 构造之前。需保证顺序正确(已保证)。

### 5. 与 SP7a 的依赖

- Task 8 SkillGenerator 调用 `SkillLoader.reload()` — SP7a Task 2 已实现 ✅
- Task 9 SkillSelector 调用 `SkillLoader.list_available()` — SP7a Task 2 已实现 ✅
- Task 12 server.py 复用 SP7a 的 `skill_loader` — SP7a Task 5 已实现 ✅
