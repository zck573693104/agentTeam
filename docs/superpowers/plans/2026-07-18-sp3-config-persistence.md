# SP3 配置持久化到 DB 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 TeamStore 和 AgentLibrary 从纯内存存储改造为可选 DB-backed 持久化,重启后自动恢复配置,同时保持向后兼容(无 repo 参数时仍为纯内存模式)。

**Architecture:** 复用现有 SQLite 连接与共享锁机制(`conn_lock`),新增 `teams` 和 `library_agents` 两张表存储配置 JSON。新增 `TeamRepo` / `LibraryRepo` 两个仓储类封装 CRUD,`TeamStore` / `AgentLibrary` 接受可选 `repo` 参数:有 repo 时所有写操作同步到 DB 并在初始化时从 DB 加载到内存缓存,无 repo 时退化为纯内存模式(向后兼容)。

**Tech Stack:** Python 3.11+, sqlite3, threading.Lock, dataclasses, Pydantic, FastAPI, pytest

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/storage/db.py` | SQLite schema 定义与初始化 | 修改:SCHEMA 新增两表 |
| `agentteam/storage/teams.py` | TeamRepo 仓储类 | 新建 |
| `agentteam/storage/library.py` | LibraryRepo 仓储类 | 新建 |
| `agentteam/api/store.py` | TeamStore 内存注册表 | 修改:接受可选 repo |
| `agentteam/domain/library.py` | AgentLibrary 专家库 | 修改:接受可选 repo |
| `agentteam/api/server.py` | FastAPI app 工厂 | 修改:创建 repos 并注入 |
| `tests/storage/test_db.py` | DB schema 测试 | 修改:新增表存在性测试 |
| `tests/storage/test_teams.py` | TeamRepo 测试 | 新建 |
| `tests/storage/test_library_repo.py` | LibraryRepo 测试 | 新建 |
| `tests/api/test_store.py` | TeamStore 测试 | 修改:新增 DB-backed 测试 |
| `tests/domain/test_library.py` | AgentLibrary 测试 | 修改:新增 DB-backed 测试 |
| `tests/api/test_api_persistence.py` | 集成测试:create_app 重启恢复 | 新建 |

---

## Task 1: DB Schema 新增 teams 和 library_agents 表

**Files:**
- Modify: `agentteam/storage/db.py` (SCHEMA 常量)
- Modify: `tests/storage/test_db.py`

- [ ] **Step 1: 写失败测试 — 验证新表存在**

在 `tests/storage/test_db.py` 末尾追加:

```python
def test_init_db_creates_teams_table(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "teams" in tables


def test_init_db_creates_library_agents_table(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "library_agents" in tables


def test_teams_table_columns(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("PRAGMA table_info(teams)")
    cols = {row[1] for row in cur.fetchall()}
    assert {"name", "description", "config", "created_at", "updated_at"}.issubset(cols)


def test_library_agents_table_columns(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("PRAGMA table_info(library_agents)")
    cols = {row[1] for row in cur.fetchall()}
    assert {"name", "config", "created_at", "updated_at"}.issubset(cols)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_db.py -v`
Expected: 4 个新测试 FAIL(表不存在)

- [ ] **Step 3: 实现 — SCHEMA 新增两表**

在 `agentteam/storage/db.py` 的 `SCHEMA` 字符串中,在 `approvals` 表之后追加:

```sql
CREATE TABLE IF NOT EXISTS teams (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    config      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_agents (
    name        TEXT PRIMARY KEY,
    config      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

完整 SCHEMA 应包含:runs / run_events(含索引) / approvals / teams / library_agents。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_db.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 280 tests passed(原 280 + 新增 4)

- [ ] **Step 6: 提交**

```bash
git add agentteam/storage/db.py tests/storage/test_db.py
git commit -m "feat(storage): 新增 teams/library_agents 表 schema"
```

---

## Task 2: TeamRepo — upsert 与 get

**Files:**
- Create: `agentteam/storage/teams.py`
- Create: `tests/storage/test_teams.py`

- [ ] **Step 1: 写失败测试 — upsert + get 往返**

创建 `tests/storage/test_teams.py`:

```python
import sqlite3

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


def _make_team(name="dev") -> Team:
    return Team(
        name=name,
        description="test team",
        root=Agent(
            name="lead", role="supervisor",
            system_prompt="plan",
            children=[Agent(name="w1", role="worker", tools=["read_file"])],
        ),
        default_model=ModelRef(provider="qwen", name="qwen-max"),
        skills=["python"],
    )


def test_team_repo_upsert_and_get(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    team = _make_team("dev")
    repo.upsert(team)

    got = repo.get("dev")
    assert got is not None
    assert got.name == "dev"
    assert got.description == "test team"
    assert got.root.name == "lead"
    assert got.root.role == "supervisor"
    assert got.root.children[0].name == "w1"
    assert got.root.children[0].tools == ["read_file"]
    assert got.default_model.provider == "qwen"
    assert got.default_model.name == "qwen-max"
    assert got.skills == ["python"]


def test_team_repo_get_missing_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    assert repo.get("nonexistent") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_teams.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.storage.teams'`

- [ ] **Step 3: 实现 — 创建 TeamRepo**

创建 `agentteam/storage/teams.py`:

```python
"""teams 表的读写:Team 配置持久化。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from agentteam.api.serializer import team_from_dict, team_to_dict
from agentteam.domain.team import Team


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TeamRepo:
    """teams 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def upsert(self, team: Team) -> None:
        """INSERT OR REPLACE,序列化为 JSON。"""
        config = json.dumps(team_to_dict(team), ensure_ascii=False)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO teams (name, description, config, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "description=excluded.description, config=excluded.config, updated_at=excluded.updated_at",
                (team.name, team.description, config, now, now),
            )
            self._conn.commit()

    def get(self, name: str) -> Team | None:
        """SELECT config,反序列化为 Team。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT config FROM teams WHERE name = ?", (name,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return team_from_dict(json.loads(row["config"]))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_teams.py -v`
Expected: 2 PASS

- [ ] **Step 5: 提交**

```bash
git add agentteam/storage/teams.py tests/storage/test_teams.py
git commit -m "feat(storage): TeamRepo upsert/get 实现"
```

---

## Task 3: TeamRepo — list_all 与 delete

**Files:**
- Modify: `agentteam/storage/teams.py`
- Modify: `tests/storage/test_teams.py`

- [ ] **Step 1: 写失败测试 — list_all 与 delete**

在 `tests/storage/test_teams.py` 末尾追加:

```python
def test_team_repo_list_all(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    repo.upsert(_make_team("a"))
    repo.upsert(_make_team("b"))
    teams = repo.list_all()
    names = sorted(t.name for t in teams)
    assert names == ["a", "b"]


def test_team_repo_list_all_empty(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    assert repo.list_all() == []


def test_team_repo_delete_existing(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    repo.upsert(_make_team("dev"))
    assert repo.delete("dev") is True
    assert repo.get("dev") is None


def test_team_repo_delete_missing_returns_false(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    assert repo.delete("nonexistent") is False


def test_team_repo_upsert_overwrites(tmp_db: sqlite3.Connection):
    from agentteam.storage.teams import TeamRepo

    repo = TeamRepo(tmp_db)
    repo.upsert(_make_team("dev"))
    team2 = _make_team("dev")
    team2.description = "updated desc"
    repo.upsert(team2)
    got = repo.get("dev")
    assert got.description == "updated desc"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_teams.py -v`
Expected: 5 个新测试 FAIL(AttributeError: 'TeamRepo' object has no attribute 'list_all')

- [ ] **Step 3: 实现 — 添加 list_all 与 delete 方法**

在 `agentteam/storage/teams.py` 的 `TeamRepo` 类中,在 `get` 方法后追加:

```python
    def list_all(self) -> list[Team]:
        """SELECT all,反序列化为 Team 列表。"""
        with self._lock:
            cur = self._conn.execute("SELECT config FROM teams ORDER BY name")
            rows = cur.fetchall()
        return [team_from_dict(json.loads(r["config"])) for r in rows]

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM teams WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_teams.py -v`
Expected: 7 PASS

- [ ] **Step 5: 提交**

```bash
git add agentteam/storage/teams.py tests/storage/test_teams.py
git commit -m "feat(storage): TeamRepo list_all/delete 实现"
```

---

## Task 4: TeamStore 接受可选 repo 参数

**Files:**
- Modify: `agentteam/api/store.py`
- Modify: `tests/api/test_store.py`

- [ ] **Step 1: 写失败测试 — DB-backed 模式**

在 `tests/api/test_store.py` 末尾追加:

```python
def test_team_store_db_backed_register_persists(tmp_path):
    """DB-backed TeamStore: register 后,新实例同 DB 能 get 到。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)
    team = _make_team("dev")
    store.register(team)
    # 新 store 同 DB,模拟重启
    store2 = TeamStore(repo=TeamRepo(conn))
    got = store2.get("dev")
    assert got is not None
    assert got.name == "dev"
    assert got.description == "test"
    conn.close()


def test_team_store_db_backed_loads_existing_on_init(tmp_path):
    """DB-backed TeamStore: 初始化时从 DB 加载已有 teams。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    repo.upsert(_make_team("pre_existing"))
    # 新 store 初始化时应加载 pre_existing
    store = TeamStore(repo=repo)
    assert store.get("pre_existing") is not None
    assert "pre_existing" in [t.name for t in store.list_all()]
    conn.close()


def test_team_store_db_backed_delete_persists(tmp_path):
    """DB-backed TeamStore: delete 后,新实例同 DB 仍为空。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)
    store.register(_make_team("dev"))
    assert store.delete("dev") is True
    # 新 store 同 DB,模拟重启
    store2 = TeamStore(repo=TeamRepo(conn))
    assert store2.get("dev") is None
    conn.close()


def test_team_store_no_repo_is_in_memory_only(tmp_path):
    """无 repo 参数:纯内存模式,不持久化(向后兼容)。"""
    store = TeamStore()
    store.register(_make_team("dev"))
    assert store.get("dev") is not None
    # 新 store 无 DB,模拟重启 —— 数据应丢失
    store2 = TeamStore()
    assert store2.get("dev") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_store.py -v`
Expected: 4 个新测试 FAIL(`TypeError: TeamStore.__init__() got an unexpected keyword argument 'repo'`)

- [ ] **Step 3: 实现 — TeamStore 改造**

替换 `agentteam/api/store.py` 全部内容:

```python
"""团队注册表:可选 DB-backed 持久化。

- repo=None:纯内存模式(测试用,向后兼容)
- repo 提供:所有操作同步到 DB,同时维护内存缓存加速读取
"""
from __future__ import annotations

from agentteam.domain.team import Team


class TeamStore:
    """团队注册表。

    默认纯内存(重启后清空);传入 TeamRepo 后变为 DB-backed,
    初始化时从 DB 加载到内存缓存,所有写操作同步到 DB。
    """

    def __init__(self, repo=None) -> None:
        self._repo = repo
        self._cache: dict[str, Team] = {}
        if repo is not None:
            for t in repo.list_all():
                self._cache[t.name] = t

    def register(self, team: Team) -> None:
        self._cache[team.name] = team
        if self._repo is not None:
            self._repo.upsert(team)

    def get(self, name: str) -> Team | None:
        return self._cache.get(name)

    def list_all(self) -> list[Team]:
        return list(self._cache.values())

    def delete(self, name: str) -> bool:
        if name not in self._cache:
            return False
        del self._cache[name]
        if self._repo is not None:
            self._repo.delete(name)
        return True
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_store.py -v`
Expected: 全部 PASS(原 6 + 新 4 = 10)

- [ ] **Step 5: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 291 tests passed(原 280 + Task1 新增 4 + 本任务新增 4,减去覆盖的 3 = 291)

- [ ] **Step 6: 提交**

```bash
git add agentteam/api/store.py tests/api/test_store.py
git commit -m "feat(api): TeamStore 接受可选 repo 参数(DB-backed)"
```

---

## Task 5: LibraryRepo — upsert 与 get

**Files:**
- Create: `agentteam/storage/library.py`
- Create: `tests/storage/test_library_repo.py`

- [ ] **Step 1: 写失败测试 — upsert + get 往返**

创建 `tests/storage/test_library_repo.py`:

```python
import sqlite3

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef


def _make_agent(name="coder") -> Agent:
    return Agent(
        name=name,
        role="worker",
        system_prompt="code prompt",
        model=ModelRef(provider="qwen", name="qwen-max"),
        tools=["read_file", "write_file"],
        max_iterations=5,
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )


def test_library_repo_upsert_and_get(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    agent = _make_agent("coder")
    repo.upsert(agent)

    got = repo.get("coder")
    assert got is not None
    assert got.name == "coder"
    assert got.role == "worker"
    assert got.system_prompt == "code prompt"
    assert got.model.provider == "qwen"
    assert got.model.name == "qwen-max"
    assert got.tools == ["read_file", "write_file"]
    assert got.max_iterations == 5
    assert got.approval_policy.level == "tool"
    assert got.approval_policy.targets == ["write_file"]
    assert len(got.mcp_servers) == 1
    assert got.mcp_servers[0].name == "git"


def test_library_repo_get_missing_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    assert repo.get("nonexistent") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_library_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.storage.library'`

- [ ] **Step 3: 实现 — 创建 LibraryRepo**

创建 `agentteam/storage/library.py`:

```python
"""library_agents 表的读写:AgentLibrary 配置持久化。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from agentteam.api.serializer import _agent_from_dict, _agent_to_dict
from agentteam.domain.agent import Agent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LibraryRepo:
    """library_agents 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo / TeamRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def upsert(self, agent: Agent) -> None:
        """INSERT OR REPLACE,序列化为 JSON。"""
        config = json.dumps(_agent_to_dict(agent), ensure_ascii=False)
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO library_agents (name, config, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "config=excluded.config, updated_at=excluded.updated_at",
                (agent.name, config, now, now),
            )
            self._conn.commit()

    def get(self, name: str) -> Agent | None:
        """SELECT config,反序列化为 Agent。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT config FROM library_agents WHERE name = ?", (name,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _agent_from_dict(json.loads(row["config"]))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_library_repo.py -v`
Expected: 2 PASS

- [ ] **Step 5: 提交**

```bash
git add agentteam/storage/library.py tests/storage/test_library_repo.py
git commit -m "feat(storage): LibraryRepo upsert/get 实现"
```

---

## Task 6: LibraryRepo — list_all 与 delete

**Files:**
- Modify: `agentteam/storage/library.py`
- Modify: `tests/storage/test_library_repo.py`

- [ ] **Step 1: 写失败测试 — list_all 与 delete**

在 `tests/storage/test_library_repo.py` 末尾追加:

```python
def test_library_repo_list_all(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    repo.upsert(_make_agent("a"))
    repo.upsert(_make_agent("b"))
    agents = repo.list_all()
    names = sorted(a.name for a in agents)
    assert names == ["a", "b"]


def test_library_repo_list_all_empty(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    assert repo.list_all() == []


def test_library_repo_delete_existing(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    repo.upsert(_make_agent("coder"))
    assert repo.delete("coder") is True
    assert repo.get("coder") is None


def test_library_repo_delete_missing_returns_false(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    assert repo.delete("nonexistent") is False


def test_library_repo_upsert_overwrites(tmp_db: sqlite3.Connection):
    from agentteam.storage.library import LibraryRepo

    repo = LibraryRepo(tmp_db)
    repo.upsert(_make_agent("coder"))
    agent2 = _make_agent("coder")
    agent2.system_prompt = "updated prompt"
    repo.upsert(agent2)
    got = repo.get("coder")
    assert got.system_prompt == "updated prompt"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/storage/test_library_repo.py -v`
Expected: 5 个新测试 FAIL(AttributeError: 'LibraryRepo' object has no attribute 'list_all')

- [ ] **Step 3: 实现 — 添加 list_all 与 delete 方法**

在 `agentteam/storage/library.py` 的 `LibraryRepo` 类中,在 `get` 方法后追加:

```python
    def list_all(self) -> list[Agent]:
        """SELECT all,反序列化为 Agent 列表。"""
        with self._lock:
            cur = self._conn.execute("SELECT config FROM library_agents ORDER BY name")
            rows = cur.fetchall()
        return [_agent_from_dict(json.loads(r["config"])) for r in rows]

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM library_agents WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/storage/test_library_repo.py -v`
Expected: 7 PASS

- [ ] **Step 5: 提交**

```bash
git add agentteam/storage/library.py tests/storage/test_library_repo.py
git commit -m "feat(storage): LibraryRepo list_all/delete 实现"
```

---

## Task 7: AgentLibrary 接受可选 repo 参数

**Files:**
- Modify: `agentteam/domain/library.py`
- Modify: `tests/domain/test_library.py`

- [ ] **Step 1: 写失败测试 — DB-backed 模式**

在 `tests/domain/test_library.py` 末尾追加:

```python
def test_library_db_backed_register_persists(tmp_path):
    """DB-backed AgentLibrary: register 后,新实例同 DB 能 get 到。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.library import LibraryRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = LibraryRepo(conn)
    lib = AgentLibrary(repo=repo)
    lib.register(Agent(name="coder", role="worker", system_prompt="code"))
    # 新 lib 同 DB,模拟重启
    lib2 = AgentLibrary(repo=LibraryRepo(conn))
    got = lib2.get("coder")
    assert got is not None
    assert got.name == "coder"
    assert got.system_prompt == "code"
    conn.close()


def test_library_db_backed_loads_existing_on_init(tmp_path):
    """DB-backed AgentLibrary: 初始化时从 DB 加载已有 agents。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.library import LibraryRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = LibraryRepo(conn)
    repo.upsert(Agent(name="pre_existing", role="worker", system_prompt="x"))
    # 新 lib 初始化时应加载 pre_existing
    lib = AgentLibrary(repo=repo)
    assert lib.get("pre_existing") is not None
    conn.close()


def test_library_db_backed_register_still_rejects_duplicates(tmp_path):
    """DB-backed AgentLibrary: register 仍拒绝重名(ValueError)。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.library import LibraryRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = LibraryRepo(conn)
    lib = AgentLibrary(repo=repo)
    lib.register(Agent(name="coder", role="worker"))
    # 重名应抛错(即使 DB-backed)
    with pytest.raises(ValueError, match="already in library"):
        lib.register(Agent(name="coder", role="worker"))
    conn.close()


def test_library_db_backed_duplicate_raises_after_restart(tmp_path):
    """DB-backed AgentLibrary: 重启后,再 register 同名 agent 仍抛错。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.library import LibraryRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = LibraryRepo(conn)
    lib = AgentLibrary(repo=repo)
    lib.register(Agent(name="coder", role="worker"))
    # 模拟重启
    lib2 = AgentLibrary(repo=LibraryRepo(conn))
    # coder 已在 DB,再 register 同名应抛错
    with pytest.raises(ValueError, match="already in library"):
        lib2.register(Agent(name="coder", role="worker"))
    conn.close()


def test_library_no_repo_is_in_memory_only():
    """无 repo 参数:纯内存模式(向后兼容)。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker"))
    assert lib.get("coder") is not None
    # 新 lib 无 DB,模拟重启 —— 数据应丢失
    lib2 = AgentLibrary()
    assert lib2.get("coder") is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/domain/test_library.py -v -k "db_backed or no_repo"`
Expected: 5 个新测试 FAIL(`TypeError: AgentLibrary() got an unexpected keyword argument 'repo'`)

- [ ] **Step 3: 实现 — AgentLibrary 改造为普通类**

将 `agentteam/domain/library.py` 中的 `@dataclass class AgentLibrary` 改为普通类。删除 `@dataclass` 装饰器和 `agents: dict[str, Agent] = field(default_factory=dict)` 字段声明,改为 `__init__` 方法。

修改 `agentteam/domain/library.py` 顶部 import:

```python
"""专家 Agent 库:name → Agent 定义,支持 $ref 引用复用。"""
from __future__ import annotations

from copy import deepcopy

from agentteam.domain.agent import Agent, TeamRef
```

(删除 `from dataclasses import dataclass, field`)

将 `@dataclass class AgentLibrary:` 整段替换为:

```python
class AgentLibrary:
    """专家 Agent 库。

    - register(agent): 注册命名 Agent
    - get(name): 取库中 Agent(不拷贝)
    - resolve(agent): 递归解析 ref —— 若 agent.ref 指向库,
      深拷贝库定义,调用处非空字段覆盖模板

    Persistence:
    - repo=None(默认):纯内存模式,重启后丢失(向后兼容)
    - repo 提供:初始化时从 DB 加载,register 同步到 DB

    Limitations(sentinel 值限制,per spec L230-231):
    ---------------------------------------------------------------
    resolve() 使用 "字段非空才覆盖" 的 sentinel 判定逻辑,这意味着
    调用处无法将模板字段覆盖 *为* 以下"空/默认"值:

    - system_prompt: 无法覆盖为 ""(空字符串)。若模板 system_prompt="模板提示",
      调用处传 system_prompt="" 不会清空,仍保留模板值。
    - tools: 无法覆盖为 [](空列表)。若模板 tools=["read_file"],
      调用处传 tools=[] 不会清空,仍保留模板值。
    - children: 无法覆盖为 [](空列表)。若模板有 children,
      调用处传 children=[] 不会清空,仍保留模板值。
    - max_iterations: 无法覆盖为 10(默认值)。若模板 max_iterations=5,
      调用处传 max_iterations=10 不会还原为 10,仍保留模板值 5。
    - model / approval_policy: 这两个字段默认为 None,调用处传 None 表示
      "不覆盖",因此无法区分"清空为 None"与"不覆盖"两种语义。

    Workaround(绕过限制):
    - 如需清空某字段,直接修改库中模板对象(register 前修改 tmpl)。
    - 如需将 max_iterations 还原为 10,可先用其他值(如 9)触发覆盖,再后处理。
    - 如需彻底清空 tools/system_prompt,建议拆分为多个模板,或不用 ref 直接构造 Agent。

    此限制是 spec 有意为之(避免误清空),如需改变需修正 spec。

    循环引用保护:
    ----------------
    resolve() 内部维护 _visited 路径,追踪当前递归分支已展开的库 ref 名。
    若 A 的 children 中有 ref="library:A",或 A→B→A 形成间接环,
    resolve() 会抛出 ValueError("Circular library reference: A -> B -> A")。
    """

    def __init__(self, agents: dict[str, Agent] | None = None, repo=None) -> None:
        self.agents: dict[str, Agent] = dict(agents) if agents else {}
        self._repo = repo
        if repo is not None:
            for a in repo.list_all():
                self.agents[a.name] = a

    def register(self, agent: Agent) -> None:
        if agent.name in self.agents:
            raise ValueError(f"Agent already in library: {agent.name}")
        self.agents[agent.name] = agent
        if self._repo is not None:
            self._repo.upsert(agent)

    def get(self, name: str) -> Agent | None:
        return self.agents.get(name)
```

(保留 `resolve` 和 `_resolve_child` 方法不变)

注意:`__init__` 仍接受 `agents` 参数以保持与原 dataclass 行为兼容(虽然实际无调用方传入,但防御性保留)。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/domain/test_library.py -v`
Expected: 全部 PASS(原 18 + 新 5 = 23)

- [ ] **Step 5: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 全部 PASS(原 280 + Task1-7 新增 4+2+5+4+2+5+5=27,共 307)

- [ ] **Step 6: 提交**

```bash
git add agentteam/domain/library.py tests/domain/test_library.py
git commit -m "feat(domain): AgentLibrary 接受可选 repo 参数(DB-backed)"
```

---

## Task 8: server.py 集成 — 创建 repos 并注入

**Files:**
- Modify: `agentteam/api/server.py`
- Modify: `tests/api/test_api_persistence.py`(新建)

- [ ] **Step 1: 写失败测试 — create_app 重启恢复**

创建 `tests/api/test_api_persistence.py`:

```python
"""SP3 集成测试:create_app 启动后注册,重启后恢复。"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_team_persistence_across_restart(tmp_path: Path):
    """create_app 注册 team 后,新 app 同 DB 能取到。"""
    db_path = tmp_path / "test.db"

    # 第一次启动:注册 team
    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    team_payload = {
        "name": "persist_team",
        "description": "persistence test",
        "leader": {
            "name": "leader", "role": "主管",
            "system_prompt": "plan",
        },
        "workers": [
            {"name": "w1", "role": "编码", "description": "", "system_prompt": "code"},
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": ["python"],
    }
    resp = client1.post("/api/teams", json=team_payload)
    assert resp.status_code == 200

    # 第二次启动:同 DB,新 app —— team 应自动恢复
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/teams")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "persist_team" in names

    resp = client2.get("/api/teams/persist_team")
    assert resp.status_code == 200
    assert resp.json()["name"] == "persist_team"


def test_library_persistence_across_restart(tmp_path: Path):
    """create_app 注册 library agent 后,新 app 同 DB 能取到。"""
    db_path = tmp_path / "test.db"

    # 第一次启动:注册 library agent
    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    resp = client1.post("/api/library/agents", json={
        "name": "persist_coder",
        "role": "worker",
        "system_prompt": "code",
        "tools": ["read_file"],
        "max_iterations": 5,
    })
    assert resp.status_code == 200

    # 第二次启动:同 DB,新 app —— library agent 应自动恢复
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/library/agents")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert "persist_coder" in names


def test_team_delete_persists_across_restart(tmp_path: Path):
    """create_app 删除 team 后,新 app 同 DB 仍无此 team。"""
    db_path = tmp_path / "test.db"

    app1 = create_app(db_path=str(db_path), web_dist=None)
    client1 = TestClient(app1)
    team_payload = {
        "name": "to_delete",
        "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    }
    client1.post("/api/teams", json=team_payload)
    resp = client1.delete("/api/teams/to_delete")
    assert resp.status_code == 200

    # 重启:to_delete 应不存在
    app2 = create_app(db_path=str(db_path), web_dist=None)
    client2 = TestClient(app2)
    resp = client2.get("/api/teams/to_delete")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_api_persistence.py -v`
Expected: 3 个测试 FAIL(team 不存在 —— 当前 TeamStore 是内存模式,不持久化)

- [ ] **Step 3: 实现 — server.py 集成 repos**

修改 `agentteam/api/server.py`:

在 import 部分追加:

```python
from agentteam.storage.library import LibraryRepo
from agentteam.storage.teams import TeamRepo
```

修改 `create_app` 函数体,把:

```python
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary()
```

替换为:

```python
    team_repo = TeamRepo(conn, lock=conn_lock)
    library_repo = LibraryRepo(conn, lock=conn_lock)
    team_store = TeamStore(repo=team_repo)
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    mp = model_provider or ModelProvider()
    tr = tool_registry or ToolRegistry()
    lib = agent_library or AgentLibrary(repo=library_repo)
```

(注意:当外部传入 `agent_library` 时,不再传 repo —— 调用方自己决定是否 DB-backed。只有默认构造时才用 `library_repo`。)

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_api_persistence.py -v`
Expected: 3 PASS

- [ ] **Step 5: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 全部 PASS(原 280 + Task1-8 新增 30 = 310)

- [ ] **Step 6: 提交**

```bash
git add agentteam/api/server.py tests/api/test_api_persistence.py
git commit -m "feat(api): server.py 集成 TeamRepo/LibraryRepo,持久化恢复"
```

---

## Task 9: 全套测试验证与清理

**Files:** 无修改,仅验证

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -v`
Expected: 全部 PASS(310 tests)

- [ ] **Step 2: 检查工作树状态**

Run: `git status`
Expected: clean working tree

- [ ] **Step 3: 检查最近提交历史**

Run: `git log --oneline -10`
Expected: 看到 SP3 的 8 个提交

- [ ] **Step 4: 验证向后兼容 — 运行原 SP1/SP2 测试**

Run: `python -m pytest tests/integration/ tests/domain/test_library.py tests/api/test_store.py tests/api/test_api_library.py -v`
Expected: 全部 PASS(无回归)

- [ ] **Step 5: 如有未提交的修复,提交**

```bash
git add -A
git commit -m "test: SP3 全套测试验证通过"
```

(若无修改则跳过)

---

## Self-Review

**1. Spec coverage:**
- ✅ DB schema 新增 teams/library_agents 表 — Task 1
- ✅ TeamRepo(upsert/get/list_all/delete)— Task 2-3
- ✅ LibraryRepo(upsert/get/list_all/delete)— Task 5-6
- ✅ TeamStore 接受可选 repo — Task 4
- ✅ AgentLibrary 接受可选 repo — Task 7
- ✅ server.py 集成 repos — Task 8
- ✅ 向后兼容(repo=None 时纯内存)— Task 4 Step 1 + Task 7 Step 1 均有专门测试
- ✅ 测试策略(CRUD + 重启恢复 + 集成测试)— Task 2-8 全覆盖
- ✅ 复用共享锁机制 — Task 2/5 实现中 `lock: threading.Lock | None = None`,Task 8 server.py 传 `lock=conn_lock`

**2. Placeholder scan:**
- 无 "TBD"/"TODO"/"implement later"
- 每个步骤都有完整代码或命令
- 测试代码完整,非"write tests for the above"

**3. Type consistency:**
- `TeamRepo.upsert(team: Team)` / `get(name: str) -> Team | None` / `list_all() -> list[Team]` / `delete(name: str) -> bool` — 一致
- `LibraryRepo.upsert(agent: Agent)` / `get(name: str) -> Agent | None` / `list_all() -> list[Agent]` / `delete(name: str) -> bool` — 一致
- `TeamStore(repo=None)` / `AgentLibrary(repo=None)` — 一致
- `team_to_dict` / `team_from_dict` 已存在(SP1 Task 6)
- `_agent_to_dict` / `_agent_from_dict` 已存在(支持 mcp_servers,SP2 Task 3)

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp3-config-persistence.md`。两种执行方式:

1. **Subagent-Driven(推荐)** — 每个任务派发新 subagent,任务间 review,快速迭代
2. **Inline Execution** — 在当前会话执行,批量 checkpoint review

选择哪种?
