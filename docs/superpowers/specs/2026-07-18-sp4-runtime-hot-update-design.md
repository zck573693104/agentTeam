# SP4 运行时热更新设计

> 上游目标:企业级 Agent 专家团队(多级层级 + 多级 MCP + 企业能力)
> 前置依赖:SP1(Agent 层级)+ SP2(多级 MCP)+ SP3(配置持久化)已完成

## 1. 问题陈述

SP3 完成后,Team 与 AgentLibrary 配置已持久化到 SQLite。但 API 层仍缺少完整的热更新能力:

- **AgentLibrary 无删除/更新接口**:`POST /api/library/agents` 创建(重名 400),但无 DELETE/PUT 端点。专家库生命周期不完整。
- **Team 更新语义模糊**:`POST /api/teams` 既是创建也是覆盖(upsert),无法区分"新建 409"与"更新 200"语义。
- **外部 DB 编辑无法生效**:DBA 直接修改 SQLite 后,内存缓存(`TeamStore._cache` / `AgentLibrary.agents`)仍为旧值,需重启服务才能生效。

## 2. 设计目标

- 补全 AgentLibrary 的 CRUD(增 DELETE/PUT 端点)
- 引入显式 PUT /api/teams/{name}(更新已存在,404 if missing)
- 提供 POST /api/admin/reload 端点:从 DB 重新加载 TeamStore + AgentLibrary 到内存缓存
- 保持向后兼容:现有 POST 端点行为不变

## 3. 当前状态分析

### 已具备的热更新能力(无需改动)

- `TeamStore.register(team)` 已是 upsert 语义(内存覆盖 + repo.upsert)
- `create_run` 每次从 `team_store.list_all()` 重建 TeamCompiler,因此**重新注册的 team 立即对下一个 run 生效**
- `ToolRegistry` 已有 `unregister(tool_name)` 方法(test_registry.py 已验证)

### 缺失的能力(本 SP 补齐)

- `AgentLibrary.delete(name)` 方法 + `AgentLibrary.update(agent)` 方法
- `DELETE /api/library/agents/{name}` 端点
- `PUT /api/library/agents/{name}` 端点(更新已存在)
- `PUT /api/teams/{name}` 端点(更新已存在,与 POST 创建分离)
- `POST /api/admin/reload` 端点(从 DB 重载缓存)

## 4. 数据模型变更

无。复用 SP3 的 `teams` 和 `library_agents` 表。

## 5. AgentLibrary 改造

新增两个方法:

```python
def delete(self, name: str) -> bool:
    """删除库中 agent。不存在返回 False。同步删除 DB。"""
    if name not in self.agents:
        return False
    del self.agents[name]
    if self._repo is not None:
        self._repo.delete(name)
    return True

def update(self, agent: Agent) -> bool:
    """更新库中 agent(覆盖)。不存在返回 False(不创建)。同步 DB。"""
    if agent.name not in self.agents:
        return False
    self.agents[agent.name] = agent
    if self._repo is not None:
        self._repo.upsert(agent)
    return True
```

设计要点:
- `delete` / `update` 都返回 bool,表示是否操作成功(不存在时 False)
- 与 `register`(重名抛 ValueError)语义不同:update 不抛错,只返回 False
- DB-backed 模式下同步到 repo

## 6. TeamStore 改造

新增 `update` 方法(虽然 register 已是 upsert,但 update 提供"仅更新已存在"的语义):

```python
def update(self, team: Team) -> bool:
    """更新已存在的 team。不存在返回 False(不创建)。同步 DB。"""
    if team.name not in self._cache:
        return False
    self._cache[team.name] = team
    if self._repo is not None:
        self._repo.upsert(team)
    return True
```

## 7. Reload 端点设计

`POST /api/admin/reload`:

```python
def admin_router(team_store: TeamStore, library: AgentLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.post("/reload")
    def reload_from_db():
        """从 DB 重新加载 TeamStore + AgentLibrary 到内存缓存。
        
        用于外部修改 SQLite 后强制刷新内存视图。
        在纯内存模式下(repo=None)无效果,返回 200 但 loaded_count=0。
        """
        team_count = team_store.reload_from_db()
        lib_count = library.reload_from_db()
        return {"teams_reloaded": team_count, "agents_reloaded": lib_count}

    return router
```

### TeamStore.reload_from_db

```python
def reload_from_db(self) -> int:
    """从 DB 重新加载所有 teams 到内存缓存。返回加载数量。
    
    无 repo 时返回 0(纯内存模式无需 reload)。
    """
    if self._repo is None:
        return 0
    self._cache = {t.name: t for t in self._repo.list_all()}
    return len(self._cache)
```

### AgentLibrary.reload_from_db

```python
def reload_from_db(self) -> int:
    """从 DB 重新加载所有 agents 到内存。返回加载数量。"""
    if self._repo is None:
        return 0
    self.agents = {a.name: a for a in self._repo.list_all()}
    return len(self.agents)
```

## 8. API 端点变更

### 新增端点

| 方法 | 路径 | 行为 | 状态码 |
|------|------|------|--------|
| PUT | `/api/teams/{name}` | 更新已存在 team | 200 / 404 |
| PUT | `/api/library/agents/{name}` | 更新已存在 agent | 200 / 404 |
| DELETE | `/api/library/agents/{name}` | 删除 agent | 200 / 404 |
| POST | `/api/admin/reload` | 从 DB 重载缓存 | 200 |

### 现有端点(不变)

| 方法 | 路径 | 行为 |
|------|------|------|
| POST | `/api/teams` | 创建/覆盖 team(upsert) |
| GET | `/api/teams` | 列出所有 |
| GET | `/api/teams/{name}` | 获取单个 |
| DELETE | `/api/teams/{name}` | 删除 |
| POST | `/api/library/agents` | 创建 agent(重名 400) |
| GET | `/api/library/agents` | 列出所有 |

## 9. PUT 端点契约

### PUT /api/teams/{name}

请求体:完整 team dict(与 POST 相同 schema)。

```python
@router.put("/{name}")
def update_team(name: str, body: dict):
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    try:
        team = team_from_dict(body)
    except (KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid team JSON: {e}")
    if team.name != name:
        raise HTTPException(status_code=400, detail=f"Name in body ({team.name}) must match URL ({name})")
    store.update(team)  # 已确认存在,update 返回 True
    return {"name": team.name}
```

### PUT /api/library/agents/{name}

请求体:agent dict。

```python
@router.put("/{name}")
def update_agent(name: str, body: AgentDict):
    if library.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    # 构造 Agent(同 POST 逻辑)
    a = _build_agent(body)
    if a.name != name:
        raise HTTPException(status_code=400, detail=f"Name in body ({a.name}) must match URL ({name})")
    library.update(a)
    return {"name": a.name}
```

## 10. 测试策略

- `AgentLibrary.delete` / `update` 单元测试:内存模式 + DB-backed 模式
- `TeamStore.update` / `reload_from_db` 单元测试
- `AgentLibrary.reload_from_db` 单元测试
- API 端点测试:PUT/DELETE 各路径,404/200/422 状态码
- 集成测试:reload 端点 + 外部 DB 编辑场景

## 11. 向后兼容

- 所有现有端点行为不变
- `AgentLibrary` / `TeamStore` 新增方法不影响现有调用
- `reload_from_db` 在无 repo 模式下返回 0(no-op),向后兼容

## 12. 交付物

- `agentteam/domain/library.py` — AgentLibrary 新增 delete/update/reload_from_db
- `agentteam/api/store.py` — TeamStore 新增 update/reload_from_db
- `agentteam/api/routes/teams.py` — 新增 PUT /{name}
- `agentteam/api/routes/library.py` — 新增 PUT /{name} + DELETE /{name}
- `agentteam/api/routes/admin.py` — 新建,admin_router + reload 端点
- `agentteam/api/server.py` — 注册 admin_router
- 测试文件若干
