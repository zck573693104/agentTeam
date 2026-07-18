# SP3 配置持久化到 DB 设计

> 上游目标：企业级 Agent 专家团队（多级层级 + 多级 MCP + 企业能力）
> 前置依赖：SP1（Agent 层级核心）+ SP2（多级 MCP）已完成

## 1. 问题陈述

SP1/SP2 完成后，Team 配置与 AgentLibrary 仍为内存存储（`TeamStore` 与 `AgentLibrary`），重启后丢失。企业场景需要：
- 重启后 Team 配置自动恢复
- 重启后专家库配置自动恢复
- 配置变更（注册/删除）持久化

## 2. 设计目标

- `TeamStore` DB-backed：所有 CRUD 操作持久化到 SQLite，重启后自动恢复
- `AgentLibrary` DB-backed：专家库注册持久化
- 向后兼容：现有 API 不变，测试可通过 `repo=None` 回退到内存模式
- 复用现有 SQLite 连接与锁机制

## 3. 数据库 Schema

在 `agentteam/storage/db.py` 的 SCHEMA 中新增两张表：

```sql
CREATE TABLE IF NOT EXISTS teams (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    config      TEXT NOT NULL,  -- JSON: team_to_dict(team)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_agents (
    name        TEXT PRIMARY KEY,
    config      TEXT NOT NULL,  -- JSON: _agent_to_dict(agent)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

## 4. TeamRepo

新建 `agentteam/storage/teams.py`，仿 `RunRepo`/`AuditRepo` 模式：

```python
class TeamRepo:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock):
        self._conn = conn
        self._lock = lock

    def upsert(self, team: Team) -> None:
        """INSERT OR REPLACE，序列化为 JSON。"""

    def get(self, name: str) -> Team | None:
        """SELECT config, team_from_dict 反序列化。"""

    def list_all(self) -> list[Team]:
        """SELECT all, 反序列化。"""

    def delete(self, name: str) -> bool:
        """DELETE, 返回是否删除成功。"""
```

## 5. TeamStore 改造

`TeamStore` 接受可选 `repo: TeamRepo | None = None`：
- `repo=None`：纯内存模式（测试用，向后兼容）
- `repo` 提供：所有操作同步到 DB，同时维护内存缓存加速读取

```python
class TeamStore:
    def __init__(self, repo: TeamRepo | None = None) -> None:
        self._repo = repo
        self._cache: dict[str, Team] = {}
        if repo:
            # 启动时从 DB 加载到缓存
            for t in repo.list_all():
                self._cache[t.name] = t

    def register(self, team: Team) -> None:
        self._cache[team.name] = team
        if self._repo:
            self._repo.upsert(team)

    def get(self, name: str) -> Team | None:
        return self._cache.get(name)

    def list_all(self) -> list[Team]:
        return list(self._cache.values())

    def delete(self, name: str) -> bool:
        if name not in self._cache:
            return False
        del self._cache[name]
        if self._repo:
            self._repo.delete(name)
        return True
```

## 6. LibraryRepo

新建 `agentteam/storage/library.py`：

```python
class LibraryRepo:
    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock):
        ...

    def upsert(self, agent: Agent) -> None:
        """INSERT OR REPLACE，序列化为 JSON。"""

    def get(self, name: str) -> Agent | None:
        """SELECT, 反序列化。"""

    def list_all(self) -> list[Agent]:
        """SELECT all, 反序列化。"""

    def delete(self, name: str) -> bool:
        """DELETE。"""
```

## 7. AgentLibrary 改造

`AgentLibrary` 接受可选 `repo: LibraryRepo | None = None`：
- 启动时从 DB 加载已注册 agents 到 `self.agents` dict
- `register` / `get` 等方法同步到 DB

```python
class AgentLibrary:
    def __init__(self, repo: LibraryRepo | None = None) -> None:
        self.agents: dict[str, Agent] = {}
        self._repo = repo
        if repo:
            for a in repo.list_all():
                self.agents[a.name] = a

    def register(self, agent: Agent) -> None:
        if agent.name in self.agents:
            raise ValueError(f"Agent already registered: {agent.name}")
        self.agents[agent.name] = agent
        if self._repo:
            self._repo.upsert(agent)

    def get(self, name: str) -> Agent | None:
        return self.agents.get(name)
```

## 8. server.py 集成

```python
def create_app(db_path="data/agentteam.db", ...):
    conn = init_db(db_path)
    conn_lock = threading.Lock()
    ...
    team_repo = TeamRepo(conn, lock=conn_lock)
    library_repo = LibraryRepo(conn, lock=conn_lock)
    team_store = TeamStore(repo=team_repo)
    lib = agent_library or AgentLibrary(repo=library_repo)
    ...
```

## 9. 向后兼容

- `TeamStore()` 无参数：纯内存模式（所有现有测试不变）
- `AgentLibrary()` 无参数：纯内存模式
- `TeamStore(repo=...)` / `AgentLibrary(repo=...)`：DB-backed 模式
- `team_to_dict` / `team_from_dict` 已支持新 schema（SP1 Task 6）
- `_agent_to_dict` / `_agent_from_dict` 已支持 mcp_servers（SP2 Task 3）

## 10. 测试策略

- `TeamRepo` 单元测试：CRUD + JSON 序列化往返
- `LibraryRepo` 单元测试：同上
- `TeamStore` DB-backed 测试：register → 重启（new instance same DB）→ get 恢复
- `AgentLibrary` DB-backed 测试：同上
- 集成测试：create_app 启动后注册 team，重启后 team 仍在

## 11. 交付物

- `agentteam/storage/db.py` — SCHEMA 新增 teams/library_agents 表
- `agentteam/storage/teams.py` — TeamRepo
- `agentteam/storage/library.py` — LibraryRepo
- `agentteam/api/store.py` — TeamStore DB-backed
- `agentteam/domain/library.py` — AgentLibrary DB-backed
- `agentteam/api/server.py` — 集成 repos
- 测试文件若干
