# SP4 运行时热更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全 TeamStore/AgentLibrary 的 update/delete/reload 方法,新增 PUT/DELETE/admin-reload API 端点,实现企业级运行时热更新能力。

**Architecture:** 在 SP3 持久化基础上,为 `TeamStore` 和 `AgentLibrary` 添加 `update`(仅更新已存在,返回 bool)/ `delete`(AgentLibrary 缺失)/ `reload_from_db`(从 DB 重载缓存)方法。新增 3 类 API 端点:PUT /api/teams/{name}(更新 team)、PUT+DELETE /api/library/agents/{name}(agent 生命周期)、POST /api/admin/reload(外部 DB 编辑后强制刷新内存视图)。所有新方法在无 repo 模式下保持向后兼容。

**Tech Stack:** Python 3.11+, FastAPI, pytest, SQLite

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/domain/library.py` | AgentLibrary | 修改:新增 delete/update/reload_from_db |
| `agentteam/api/store.py` | TeamStore | 修改:新增 update/reload_from_db |
| `agentteam/api/routes/teams.py` | teams 端点 | 修改:新增 PUT /{name} |
| `agentteam/api/routes/library.py` | library 端点 | 修改:新增 PUT/DELETE /{name} |
| `agentteam/api/routes/admin.py` | admin 端点 | 新建:reload |
| `agentteam/api/server.py` | app 工厂 | 修改:注册 admin_router |
| `tests/domain/test_library.py` | AgentLibrary 测试 | 修改:新增方法测试 |
| `tests/api/test_store.py` | TeamStore 测试 | 修改:新增方法测试 |
| `tests/api/test_api_teams.py` | teams API 测试 | 修改:新增 PUT 测试 |
| `tests/api/test_api_library.py` | library API 测试 | 修改:新增 PUT/DELETE 测试 |
| `tests/api/test_api_admin.py` | admin API 测试 | 新建 |

---

## Task 1: AgentLibrary + TeamStore 新增 update/delete/reload_from_db 方法

**Files:**
- Modify: `agentteam/domain/library.py`
- Modify: `agentteam/api/store.py`
- Modify: `tests/domain/test_library.py`
- Modify: `tests/api/test_store.py`

- [ ] **Step 1: 写失败测试 — AgentLibrary 新方法**

在 `d:\project\agentTeam\tests\domain\test_library.py` 末尾追加:

```python
def test_library_delete_existing():
    """AgentLibrary.delete 删除已存在 agent,返回 True。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker"))
    assert lib.delete("coder") is True
    assert lib.get("coder") is None


def test_library_delete_missing_returns_false():
    """AgentLibrary.delete 不存在返回 False(不抛错)。"""
    lib = AgentLibrary()
    assert lib.delete("nonexistent") is False


def test_library_update_existing():
    """AgentLibrary.update 覆盖已存在 agent,返回 True。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker", system_prompt="v1"))
    updated = Agent(name="coder", role="worker", system_prompt="v2")
    assert lib.update(updated) is True
    assert lib.get("coder").system_prompt == "v2"


def test_library_update_missing_returns_false():
    """AgentLibrary.update 不存在返回 False(不创建)。"""
    lib = AgentLibrary()
    assert lib.update(Agent(name="nonexistent", role="worker")) is False
    assert lib.get("nonexistent") is None


def test_library_reload_from_db_no_repo_returns_zero():
    """无 repo 时 reload_from_db 返回 0(no-op)。"""
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker"))
    assert lib.reload_from_db() == 0
    # 内存数据保留(未被清空)
    assert lib.get("coder") is not None


def test_library_reload_from_db_with_repo(tmp_path):
    """DB-backed 模式: reload_from_db 从 DB 重载,外部 DB 编辑生效。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.library import LibraryRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = LibraryRepo(conn)
    lib = AgentLibrary(repo=repo)
    lib.register(Agent(name="coder", role="worker", system_prompt="v1"))

    # 外部直接修改 DB(模拟 DBA 操作)
    repo.upsert(Agent(name="coder", role="worker", system_prompt="externally_updated"))
    # 内存仍是旧值
    assert lib.get("coder").system_prompt == "v1"

    # reload 后内存刷新
    count = lib.reload_from_db()
    assert count == 1
    assert lib.get("coder").system_prompt == "externally_updated"
    conn.close()
```

- [ ] **Step 2: 写失败测试 — TeamStore 新方法**

在 `d:\project\agentTeam\tests\api\test_store.py` 末尾追加:

```python
def test_team_store_update_existing():
    """TeamStore.update 覆盖已存在 team,返回 True。"""
    store = TeamStore()
    store.register(_make_team("dev"))
    team2 = _make_team("dev")
    team2.description = "updated"
    assert store.update(team2) is True
    assert store.get("dev").description == "updated"


def test_team_store_update_missing_returns_false():
    """TeamStore.update 不存在返回 False(不创建)。"""
    store = TeamStore()
    assert store.update(_make_team("nonexistent")) is False
    assert store.get("nonexistent") is None


def test_team_store_reload_from_db_no_repo_returns_zero():
    """无 repo 时 reload_from_db 返回 0(no-op)。"""
    store = TeamStore()
    store.register(_make_team("dev"))
    assert store.reload_from_db() == 0
    # 内存数据保留
    assert store.get("dev") is not None


def test_team_store_reload_from_db_with_repo(tmp_path):
    """DB-backed 模式: reload_from_db 从 DB 重载。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)
    store.register(_make_team("dev"))

    # 外部直接修改 DB
    external_team = _make_team("dev")
    external_team.description = "externally_updated"
    repo.upsert(external_team)
    # 内存仍是旧值
    assert store.get("dev").description == "test"

    # reload 后内存刷新
    count = store.reload_from_db()
    assert count == 1
    assert store.get("dev").description == "externally_updated"
    conn.close()
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/domain/test_library.py tests/api/test_store.py -v -k "delete or update or reload"`
Expected: 10 个新测试 FAIL(AttributeError: 'AgentLibrary'/'TeamStore' object has no attribute ...)

- [ ] **Step 4: 实现 — AgentLibrary 新增方法**

在 `d:\project\agentTeam\agentteam\domain\library.py` 的 `AgentLibrary` 类中,在 `get` 方法之后、`resolve` 方法之前,追加以下三个方法:

```python
    def delete(self, name: str) -> bool:
        """删除库中 agent。不存在返回 False(不抛错)。同步删除 DB。"""
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

    def reload_from_db(self) -> int:
        """从 DB 重新加载所有 agents 到内存。返回加载数量。
        
        无 repo 时返回 0(no-op,内存数据保留)。
        """
        if self._repo is None:
            return 0
        self.agents = {a.name: a for a in self._repo.list_all()}
        return len(self.agents)
```

- [ ] **Step 5: 实现 — TeamStore 新增方法**

在 `d:\project\agentTeam\agentteam\api\store.py` 的 `TeamStore` 类中,在 `delete` 方法之后,追加以下两个方法:

```python
    def update(self, team: Team) -> bool:
        """更新已存在的 team。不存在返回 False(不创建)。同步 DB。"""
        if team.name not in self._cache:
            return False
        self._cache[team.name] = team
        if self._repo is not None:
            self._repo.upsert(team)
        return True

    def reload_from_db(self) -> int:
        """从 DB 重新加载所有 teams 到内存缓存。返回加载数量。
        
        无 repo 时返回 0(no-op,内存数据保留)。
        """
        if self._repo is None:
            return 0
        self._cache = {t.name: t for t in self._repo.list_all()}
        return len(self._cache)
```

- [ ] **Step 6: 运行测试验证通过**

Run: `python -m pytest tests/domain/test_library.py tests/api/test_store.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 320 tests passed(原 310 + 新增 10)

- [ ] **Step 8: 提交**

```bash
git add agentteam/domain/library.py agentteam/api/store.py tests/domain/test_library.py tests/api/test_store.py
git commit -m "feat: AgentLibrary/TeamStore 新增 update/delete/reload_from_db"
```

---

## Task 2: PUT /api/teams/{name} 端点

**Files:**
- Modify: `agentteam/api/routes/teams.py`
- Modify: `tests/api/test_api_teams.py`

- [ ] **Step 1: 写失败测试 — PUT 端点**

先读 `d:\project\agentTeam\tests\api\test_api_teams.py` 了解现有测试风格,然后在末尾追加:

```python
def test_update_existing_team_via_put():
    """PUT /api/teams/{name} 更新已存在 team,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    # 先创建
    team_payload = {
        "name": "dev", "description": "v1",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    }
    client.post("/api/teams", json=team_payload)

    # PUT 更新
    updated_payload = dict(team_payload)
    updated_payload["description"] = "v2"
    resp = client.put("/api/teams/dev", json=updated_payload)
    assert resp.status_code == 200
    assert resp.json()["name"] == "dev"

    # 验证更新生效
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "v2"


def test_update_missing_team_via_put_returns_404():
    """PUT /api/teams/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    resp = client.put("/api/teams/nonexistent", json={
        "name": "nonexistent", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    assert resp.status_code == 404


def test_update_team_name_mismatch_returns_400():
    """PUT /api/teams/{name} body.name 与 URL name 不匹配返回 400。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    app = create_app(web_dist=None)
    client = TestClient(app)
    # 先创建 dev
    client.post("/api/teams", json={
        "name": "dev", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    # PUT 时 body.name 与 URL 不匹配
    resp = client.put("/api/teams/dev", json={
        "name": "different_name", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_api_teams.py -v -k "update"`
Expected: 3 个新测试 FAIL(405 Method Not Allowed 或 404 — PUT 路由不存在)

- [ ] **Step 3: 实现 — 新增 PUT 端点**

在 `d:\project\agentTeam\agentteam\api\routes\teams.py` 的 `teams_router` 函数中,在 `delete_team` 之后(return router 之前)追加:

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
            raise HTTPException(
                status_code=400,
                detail=f"Name in body ({team.name}) must match URL ({name})",
            )
        store.update(team)
        return {"name": team.name}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_api_teams.py -v`
Expected: 全部 PASS(原 + 新 3)

- [ ] **Step 5: 提交**

```bash
git add agentteam/api/routes/teams.py tests/api/test_api_teams.py
git commit -m "feat(api): PUT /api/teams/{name} 更新已存在 team"
```

---

## Task 3: PUT + DELETE /api/library/agents/{name} 端点

**Files:**
- Modify: `agentteam/api/routes/library.py`
- Modify: `tests/api/test_api_library.py`

- [ ] **Step 1: 写失败测试 — PUT/DELETE 端点**

先读 `d:\project\agentTeam\tests\api\test_api_library.py` 了解现有测试风格,然后在末尾追加:

```python
def test_update_existing_agent_via_put():
    """PUT /api/library/agents/{name} 更新已存在 agent,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    # 先创建
    client.post("/api/library/agents", json={
        "name": "coder", "role": "worker",
        "system_prompt": "v1", "tools": [], "max_iterations": 10,
    })
    # PUT 更新
    resp = client.put("/api/library/agents/coder", json={
        "name": "coder", "role": "worker",
        "system_prompt": "v2", "tools": ["read_file"], "max_iterations": 5,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "coder"

    # 验证更新生效
    agents = client.get("/api/library/agents").json()
    coder = [a for a in agents if a["name"] == "coder"][0]
    assert coder["system_prompt"] == "v2"
    assert coder["tools"] == ["read_file"]
    assert coder["max_iterations"] == 5


def test_update_missing_agent_via_put_returns_404():
    """PUT /api/library/agents/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    resp = client.put("/api/library/agents/nonexistent", json={
        "name": "nonexistent", "role": "worker",
    })
    assert resp.status_code == 404


def test_update_agent_name_mismatch_returns_400():
    """PUT /api/library/agents/{name} body.name 与 URL 不匹配返回 400。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})
    resp = client.put("/api/library/agents/coder", json={
        "name": "different", "role": "worker",
    })
    assert resp.status_code == 400


def test_delete_existing_agent():
    """DELETE /api/library/agents/{name} 删除已存在 agent,返回 200。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})
    resp = client.delete("/api/library/agents/coder")
    assert resp.status_code == 200
    # 验证已删除
    agents = client.get("/api/library/agents").json()
    assert all(a["name"] != "coder" for a in agents)


def test_delete_missing_agent_returns_404():
    """DELETE /api/library/agents/{name} 不存在返回 404。"""
    from fastapi.testclient import TestClient
    from agentteam.api.server import create_app

    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)
    resp = client.delete("/api/library/agents/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_api_library.py -v -k "update or delete"`
Expected: 5 个新测试 FAIL(PUT/DELETE 路由不存在)

- [ ] **Step 3: 实现 — 新增 PUT/DELETE 端点**

在 `d:\project\agentTeam\agentteam\api\routes\library.py` 中:

### 3a. 抽取 agent 构造逻辑为模块级 helper

在 `AgentDict` 类之后、`library_router` 函数之前,新增 helper:

```python
def _build_agent_from_dict(agent: AgentDict) -> Agent:
    """从 AgentDict 构造 Agent(POST/PUT 共用)。"""
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.models.provider import ModelRef
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
    return Agent(
        name=agent.name, role=agent.role,
        system_prompt=agent.system_prompt,
        tools=list(agent.tools), max_iterations=agent.max_iterations,
        model=model, approval_policy=ap,
    )
```

### 3b. 重构 register_agent 使用 helper

把 `register_agent` 函数体中的 agent 构造逻辑替换为调用 helper:

```python
    @router.post("/agents")
    def register_agent(agent: AgentDict):
        existing = library.get(agent.name)
        if existing is not None:
            raise HTTPException(status_code=400, detail=f"Agent already exists: {agent.name}")
        a = _build_agent_from_dict(agent)
        library.register(a)
        return {"name": a.name}
```

### 3c. 新增 PUT 和 DELETE 端点

在 `register_agent` 之后、`return router` 之前,追加:

```python
    @router.put("/agents/{name}")
    def update_agent(name: str, agent: AgentDict):
        if library.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        a = _build_agent_from_dict(agent)
        if a.name != name:
            raise HTTPException(
                status_code=400,
                detail=f"Name in body ({a.name}) must match URL ({name})",
            )
        library.update(a)
        return {"name": a.name}

    @router.delete("/agents/{name}")
    def delete_agent(name: str):
        if not library.delete(name):
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        return {"ok": True}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/api/test_api_library.py -v`
Expected: 全部 PASS(原 2 + 新 5 = 7)

- [ ] **Step 5: 提交**

```bash
git add agentteam/api/routes/library.py tests/api/test_api_library.py
git commit -m "feat(api): PUT/DELETE /api/library/agents/{name} agent 生命周期"
```

---

## Task 4: POST /api/admin/reload 端点 + server.py 集成

**Files:**
- Create: `agentteam/api/routes/admin.py`
- Modify: `agentteam/api/server.py`
- Create: `tests/api/test_api_admin.py`

- [ ] **Step 1: 写失败测试 — admin reload 端点**

创建 `d:\project\agentTeam\tests\api\test_api_admin.py`:

```python
"""SP4 admin reload 端点测试。"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_reload_returns_counts(tmp_path: Path):
    """POST /api/admin/reload 返回重载数量。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 先注册一个 team 和 library agent
    client.post("/api/teams", json={
        "name": "dev", "description": "",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })
    client.post("/api/library/agents", json={"name": "coder", "role": "worker"})

    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["teams_reloaded"] == 1
    assert data["agents_reloaded"] == 1


def test_reload_picks_up_external_db_changes(tmp_path: Path):
    """外部直接修改 DB 后,reload 使内存刷新。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo
    from agentteam.domain.agent import Agent
    from agentteam.domain.team import Team
    from agentteam.models.provider import ModelRef

    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 先注册 dev team
    client.post("/api/teams", json={
        "name": "dev", "description": "original",
        "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
        "workers": [{"name": "w1", "role": "r", "description": "", "system_prompt": "x"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
    })

    # 外部直接通过 repo 修改 DB(模拟 DBA 操作)
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    external_team = Team(
        name="dev", description="externally_updated",
        root=Agent(name="leader", role="supervisor", system_prompt="x",
                   children=[Agent(name="w1", role="worker")]),
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    repo.upsert(external_team)
    conn.close()

    # 内存仍是旧值
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "original"

    # reload
    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200

    # 内存刷新
    resp = client.get("/api/teams/dev")
    assert resp.json()["description"] == "externally_updated"


def test_reload_empty_db_returns_zero(tmp_path: Path):
    """空 DB reload 返回 0。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    resp = client.post("/api/admin/reload")
    assert resp.status_code == 200
    assert resp.json()["teams_reloaded"] == 0
    assert resp.json()["agents_reloaded"] == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/api/test_api_admin.py -v`
Expected: 3 个测试 FAIL(404 — /api/admin/reload 路由不存在)

- [ ] **Step 3: 实现 — 创建 admin_router**

创建 `d:\project\agentTeam\agentteam\api\routes\admin.py`:

```python
"""POST /api/admin/reload 端点:从 DB 重新加载内存缓存。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.api.store import TeamStore
from agentteam.domain.library import AgentLibrary


def admin_router(team_store: TeamStore, library: AgentLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    @router.post("/reload")
    def reload_from_db():
        """从 DB 重新加载 TeamStore + AgentLibrary 到内存缓存。

        用于外部修改 SQLite 后强制刷新内存视图。
        在纯内存模式下(repo=None)无效果,返回 200 但 reloaded_count=0。
        """
        team_count = team_store.reload_from_db()
        lib_count = library.reload_from_db()
        return {"teams_reloaded": team_count, "agents_reloaded": lib_count}

    return router
```

- [ ] **Step 4: 实现 — server.py 注册 admin_router**

修改 `d:\project\agentTeam\agentteam\api\server.py`:

### 4a. 追加 import

在 `from agentteam.api.routes.library import library_router` 之后追加:

```python
from agentteam.api.routes.admin import admin_router
```

### 4b. 注册路由

在 `app.include_router(library_router(lib))` 之后追加:

```python
    app.include_router(admin_router(team_store, lib))
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/api/test_api_admin.py -v`
Expected: 3 PASS

- [ ] **Step 6: 运行全套测试确认无回归**

Run: `python -m pytest -q`
Expected: 全部 PASS(原 320 + Task2 新增 3 + Task3 新增 5 + Task4 新增 3 = 331)

- [ ] **Step 7: 提交**

```bash
git add agentteam/api/routes/admin.py agentteam/api/server.py tests/api/test_api_admin.py
git commit -m "feat(api): POST /api/admin/reload 从 DB 重载内存缓存"
```

---

## Task 5: 全套测试验证与清理

**Files:** 无修改,仅验证

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -v`
Expected: 全部 PASS(331 tests)

- [ ] **Step 2: 检查工作树状态**

Run: `git status`
Expected: clean working tree

- [ ] **Step 3: 检查最近提交历史**

Run: `git log --oneline -10`
Expected: 看到 SP4 的 4 个 feat 提交

- [ ] **Step 4: 验证向后兼容 — 运行原 SP1/SP2/SP3 测试**

Run: `python -m pytest tests/integration/ tests/api/test_api_persistence.py tests/api/test_store.py tests/domain/test_library.py -v`
Expected: 全部 PASS(无回归)

- [ ] **Step 5: 如有未提交的修复,提交**

```bash
git add -A
git commit -m "test: SP4 全套测试验证通过"
```

(若无修改则跳过)

---

## Self-Review

**1. Spec coverage:**
- ✅ AgentLibrary.delete / update — Task 1
- ✅ AgentLibrary.reload_from_db — Task 1
- ✅ TeamStore.update / reload_from_db — Task 1
- ✅ PUT /api/teams/{name} — Task 2
- ✅ PUT /api/library/agents/{name} — Task 3
- ✅ DELETE /api/library/agents/{name} — Task 3
- ✅ POST /api/admin/reload — Task 4
- ✅ server.py 集成 admin_router — Task 4
- ✅ 向后兼容:无 repo 时 reload 返回 0(no-op)— Task 1 测试覆盖
- ✅ 现有端点不变:Task 5 Step 4 验证

**2. Placeholder scan:**
- 无 "TBD"/"TODO"
- 每个步骤都有完整代码或命令
- 测试代码完整

**3. Type consistency:**
- `AgentLibrary.delete(name) -> bool` / `update(agent) -> bool` / `reload_from_db() -> int` — 一致
- `TeamStore.update(team) -> bool` / `reload_from_db() -> int` — 一致
- PUT 端点契约:404 if missing, 400 if name mismatch, 200 on success — teams 和 library 一致
- `_build_agent_from_dict` helper 抽取自 POST 逻辑,PUT 复用 — DRY

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp4-runtime-hot-update.md`。两种执行方式:

1. **Subagent-Driven(推荐)** — 每个任务派发新 subagent,任务间 review
2. **Inline Execution** — 在当前会话执行,批量 checkpoint review

选择哪种?
