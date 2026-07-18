# SP5 预置企业级团队设计

> 上游目标:打造企业级 Agent 专家团队,方便扩展 Agent 层级,挂载 MCP。
> SP1-SP4 已完成(Agent 层级 + 多级 MCP + DB 持久化 + 运行时热更新)。SP5 是 5 个子项目的最后一程:交付"开箱即用"的企业级预置团队。

## 1. 目标与范围

### 1.1 目标
交付 4 个开箱即用的企业级预置团队,覆盖研发、客服、数据分析、内容营销四种典型场景。每个预置团队:
- 展示 SP1 的多级 Agent 层级(supervisor→supervisor→worker)
- 至少挂载 1 个 MCP 服务(展示 SP2 能力)
- 至少 1 个声明式审批策略(展示 SP1 审批隔离)
- 通过 CLI 一键安装到 API 服务,自动持久化到 SQLite(SP3),支持重复安装幂等(借助 SP4 PUT 端点)

### 1.2 范围
**包含:**
- 4 个预置团队 Python 模块(每模块含 Team + LIB_AGENTS + METADATA)
- `agentteam/presets/` 包:catalog 注册表 + install helper
- CLI 3 个新命令:list-presets / show-preset / install-preset
- 完整测试覆盖(每 preset 模块 + catalog + CLI)

**不包含:**
- 实际运行 MCP server 进程(预置团队定义 `mcp_servers` 配置,但不启动外部进程)
- Web UI 集成(后续可选)
- 预置团队的实际模型调用验证(用 stub model provider 做编译期校验,不发起真实 LLM 调用)
- YAML/JSON 配置格式(统一用 Python 模块,保留类型安全与表达力)

## 2. 架构

### 2.1 选型对比

**Approach A(推荐):Python 模块包 + CLI**
- 每个 preset 一个 `.py` 模块,导出 `TEAM`/`LIB_AGENTS`/`METADATA`
- `presets/__init__.py` 维护 `PRESET_REGISTRY: dict[str, ModuleType]`
- CLI 通过 `agentteam.presets.list_presets()` / `get_preset(name)` 访问
- 优点:类型安全、IDE 补全、复用现有 Team/Agent/MCPServer 类、无新依赖
- 缺点:用户编辑需懂 Python(但本项目用户即开发者,可接受)

**Approach B:单一 presets.py 大文件**
- 4 个 preset 函数集中在一个文件
- 优点:文件少
- 缺点:文件臃肿、扩展新 preset 需改主文件、违反单一职责

**Approach C:YAML 配置 + loader**
- preset 存为 `.yaml`,运行时加载
- 优点:声明式、非开发者友好
- 缺点:需新增 PyYAML 依赖、嵌套 Team/Agent 反序列化复杂、丧失类型安全

**决定:** Approach A。与用户选择"Python 模块 + CLI"一致,且最大化复用 SP1-SP4 既有能力。

### 2.2 组件结构

```
agentteam/
├── presets/                          # 新建包
│   ├── __init__.py                   # PRESET_REGISTRY + list_presets/get_preset/install_preset_to_api
│   ├── enterprise_dev.py             # 研发团队(CEO→CTO→eng/tester/reviewer + 测试小队)
│   ├── customer_support.py           # 客服团队(主管→一线→升级→投诉)
│   ├── data_analysis.py              # 数据分析团队(总监→SQL→可视化)
│   └── content_marketing.py          # 内容营销团队(主编→策划→写手→SEO)
├── cli.py                            # 修改:+3 命令
└── ...

tests/
├── presets/                          # 新建
│   ├── __init__.py
│   ├── test_catalog.py               # list_presets / get_preset 测试
│   ├── test_enterprise_dev.py        # 研发团队结构 + 编译验证
│   ├── test_customer_support.py
│   ├── test_data_analysis.py
│   └── test_content_marketing.py
└── test_cli_presets.py               # CLI 命令测试(mocked HTTP)
```

### 2.3 Preset 模块契约

每个 preset 模块必须导出 3 个模块级变量:

```python
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.team import Team
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef

TEAM: Team = Team(...)                          # 主团队
LIB_AGENTS: list[Agent] = [...]                 # 依赖的专家库 agent(可空列表)
METADATA: dict = {
    "name": "enterprise_dev",                   # 与模块名一致,作为 catalog key
    "title": "企业级研发团队",                   # 展示名
    "description": "...",                        # 一句话描述
    "category": "research",                      # research | support | analytics | marketing
    "tags": ["研发", "MCP", "审批", "多层级"],
    "deps_teams": [],                            # 依赖的 sub-team 名(供 TeamRef 解析),按顺序注册
    "deps_library": ["code_engineer", "tester"], # 本 preset 注册的 library agent 名
}
```

**约束:**
- `METADATA["name"]` 必须与模块文件名(去 `.py`)一致
- `TEAM.name` 可以与 `METADATA["name"]` 不同(团队业务名 vs preset 标识)
- `LIB_AGENTS` 中的 Agent `name` 必须与 `METADATA["deps_library"]` 列表一致
- 若 `TEAM` 通过 `TeamRef` 引用 sub-team,该 sub-team 必须出现在 `METADATA["deps_teams"]` 中(可被同模块定义为辅助 Team 变量,或依赖其他 preset)

## 3. 4 个预置团队设计

### 3.1 `enterprise_dev.py` — 企业级研发团队

**层级:**
```
CEO (supervisor, system_prompt: "你是 CEO,派活给 CTO")
└── CTO (supervisor, system_prompt: "你是 CTO,派活给工程师和测试小队")
    ├── eng (worker, ref=library:code_engineer)  # 专家库引用
    ├── reviewer (worker, tools=[read_file], approval_policy=step)
    └── TeamRef(name="test_subteam", alias="qa_team")  # 嵌套子团队
```

**辅助 sub-team(同模块定义,供 TeamRef 引用):**
```python
TEST_SUBTEAM: Team = Team(
    name="test_subteam",
    description="测试小队",
    root=Agent(name="test_lead", role="supervisor",
               system_prompt="你是测试主管,派活给 tester",
               children=[Agent(name="tester", role="worker",
                               system_prompt="你是测试员,写测试用例",
                               tools=["read_file", "write_file"], max_iterations=5,
                               approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]))]),
    default_model=ModelRef("qwen", "qwen-max"),
)
```

**MCP 挂载:**
- Team 级:`MCPServer(name="git", command="npx", args=["-y", "@modelcontextprotocol/server-git", "--repository", "."])`
- CTO 的 eng worker 通过 `library:code_engineer` 继承工具,包括 `mcp:git:git_status`/`git_diff`/`git_log`

**LIB_AGENTS:**
- `code_engineer` (worker, tools=[read_file, write_file, mcp:git:git_status, mcp:git:git_diff, mcp:git:git_log], approval_policy=tool targets=[write_file])

**METADATA:**
```python
{
    "name": "enterprise_dev",
    "title": "企业级研发团队",
    "description": "CEO→CTO→工程师+测试+审查,含 git MCP、写文件审批、嵌套测试小队",
    "category": "research",
    "tags": ["研发", "MCP", "审批", "多层级", "专家库"],
    "deps_teams": ["test_subteam"],
    "deps_library": ["code_engineer"],
}
```

### 3.2 `customer_support.py` — 客户支持团队

**层级:**
```
support_manager (supervisor, system_prompt: "你是客服主管,派活给一线客服和升级专员")
├── frontline (worker, tools=[mcp:ticket:list_tickets, mcp:ticket:get_ticket, mcp:ticket:create_note], max_iterations=10)
├── escalation (supervisor, system_prompt: "你是升级专员,处理复杂问题", approval_policy=step)
│   └── specialist (worker, tools=[mcp:ticket:resolve_ticket, read_file], max_iterations=8)
└── complaint_handler (worker, tools=[mcp:ticket:escalate_complaint], approval_policy=tool targets=[mcp:ticket:escalate_complaint], max_iterations=5)
```

**MCP 挂载:**
- Team 级:`MCPServer(name="ticket", command="npx", args=["-y", "@modelcontextprotocol/server-ticket"])` (假想的 ticket MCP)

**审批:**
- `escalation` supervisor:`level=step`(每步审批,防止升级决策失控)
- `complaint_handler`:`level=tool, targets=[mcp:ticket:escalate_complaint]`(投诉升级前需审批)

**LIB_AGENTS:** `[]`(无专家库依赖)

**METADATA:**
```python
{
    "name": "customer_support",
    "title": "客户支持团队",
    "description": "主管→一线→升级专员→投诉处理,挂接工单 MCP,升级与投诉需审批",
    "category": "support",
    "tags": ["客服", "MCP", "审批", "工单"],
    "deps_teams": [],
    "deps_library": [],
}
```

### 3.3 `data_analysis.py` — 数据分析团队

**层级:**
```
analytics_director (supervisor, system_prompt: "你是分析总监,派活给 SQL 工程师和可视化师")
├── sql_engineer (worker, tools=[mcp:db:query, mcp:db:schema, read_file], max_iterations=10)
└── visualizer (worker, tools=[mcp:chart:render, mcp:chart:export, write_file], max_iterations=8)
```

**MCP 挂载:**
- Team 级:2 个 MCP
  - `MCPServer(name="db", command="npx", args=["-y", "@modelcontextprotocol/server-postgres", "--connection-string", "..."])`
  - `MCPServer(name="chart", command="npx", args=["-y", "@modelcontextprotocol/server-chart"])`

**审批:** 无(数据分析无破坏性操作)

**LIB_AGENTS:** `[]`

**METADATA:**
```python
{
    "name": "data_analysis",
    "title": "数据分析团队",
    "description": "总监→SQL工程师→可视化师,挂接数据库 MCP + 图表 MCP",
    "category": "analytics",
    "tags": ["数据分析", "MCP", "数据库", "可视化"],
    "deps_teams": [],
    "deps_library": [],
}
```

### 3.4 `content_marketing.py` — 内容营销团队

**层级:**
```
editor_in_chief (supervisor, system_prompt: "你是主编,派活给策划、写手和 SEO")
├── planner (worker, tools=[mcp:search:query, mcp:search:trends], max_iterations=5)
├── writer (worker, tools=[read_file, write_file], approval_policy=tool targets=[write_file], max_iterations=10)
└── seo (worker, tools=[mcp:search:keywords, mcp:social:schedule_post], approval_policy=tool targets=[mcp:social:schedule_post], max_iterations=5)
```

**MCP 挂载:**
- Team 级:2 个 MCP
  - `MCPServer(name="search", command="npx", args=["-y", "@modelcontextprotocol/server-search"])`
  - `MCPServer(name="social", command="npx", args=["-y", "@modelcontextprotocol/server-social-media"])`

**审批:**
- `writer`:`level=tool, targets=[write_file]`(写文件前审批)
- `seo`:`level=tool, targets=[mcp:social:schedule_post]`(社媒发布前审批)

**LIB_AGENTS:** `[]`

**METADATA:**
```python
{
    "name": "content_marketing",
    "title": "内容营销团队",
    "description": "主编→策划→写手→SEO,挂接搜索 MCP + 社媒 MCP,写文与发布需审批",
    "category": "marketing",
    "tags": ["内容", "营销", "MCP", "审批", "SEO"],
    "deps_teams": [],
    "deps_library": [],
}
```

## 4. Preset Catalog API

### 4.1 `agentteam/presets/__init__.py`

```python
"""预置企业级团队目录与安装 helper。"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
    "customer_support": "agentteam.presets.customer_support",
    "data_analysis": "agentteam.presets.data_analysis",
    "content_marketing": "agentteam.presets.content_marketing",
}


def list_presets() -> list[dict[str, Any]]:
    """返回所有预置团队的 METADATA 列表(按 name 排序)。"""
    result = []
    for name, module_path in sorted(PRESET_REGISTRY.items()):
        mod = importlib.import_module(module_path)
        result.append(mod.METADATA)
    return result


def get_preset(name: str) -> ModuleType:
    """按 name 获取 preset 模块。不存在抛 KeyError。"""
    if name not in PRESET_REGISTRY:
        raise KeyError(f"Preset '{name}' not found. Available: {list(PRESET_REGISTRY)}")
    return importlib.import_module(PRESET_REGISTRY[name])


def install_preset_to_api(name: str, api: str = "http://localhost:8000") -> dict[str, Any]:
    """安装预置团队到 API 服务。

    安装顺序(确保依赖先就位):
    1. 注册 LIB_AGENTS 到 /api/library/agents(POST 失败为 400 重复 → PUT 更新)
    2. 注册 deps_teams 中的 sub-team 到 /api/teams(POST 失败为 400 重复 → PUT 更新)
       sub-team 需在 preset 模块中定义为模块级变量(变量名即 team.name)
    3. 注册 TEAM 到 /api/teams(POST 失败为 400 重复 → PUT 更新)

    返回 {"library": [...], "teams": [...]} 记录每步注册结果。
    """
    import requests
    from agentteam.api.serializer import team_to_dict
    from dataclasses import asdict

    mod = get_preset(name)
    meta = mod.METADATA
    result: dict[str, list[str]] = {"library": [], "teams": []}

    def _post_or_put(url_post: str, url_put: str | None, payload: dict, label: str) -> None:
        resp = requests.post(url_post, json=payload, timeout=10)
        if resp.status_code < 400:
            result[label].append(payload.get("name", "?"))
            return
        if resp.status_code == 400 and url_put is not None:
            # 重复 → 回退 PUT(SP4 热更新)
            resp2 = requests.put(url_put, json=payload, timeout=10)
            if resp2.status_code < 400:
                result[label].append(payload.get("name", "?") + "(updated)")
                return
        raise RuntimeError(f"注册 {label} '{payload.get('name')}' 失败: {resp.status_code} {resp.text}")

    # 1. LIB_AGENTS
    for agent in getattr(mod, "LIB_AGENTS", []):
        agent_dict = {
            "name": agent.name, "role": agent.role,
            "system_prompt": agent.system_prompt,
            "tools": list(agent.tools), "max_iterations": agent.max_iterations,
            "model": asdict(agent.model) if agent.model else None,
            "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        }
        _post_or_put(
            f"{api}/api/library/agents",
            f"{api}/api/library/agents/{agent.name}",
            agent_dict, "library",
        )

    # 2. deps_teams (sub-teams referenced by TeamRef)
    for team_name in meta.get("deps_teams", []):
        # 约定:sub-team 模块级变量名 = team.name.upper()(如 "test_subteam" → TEST_SUBTEAM)
        sub_team = getattr(mod, team_name.upper(), None) or getattr(mod, team_name, None)
        if sub_team is None:
            raise RuntimeError(
                f"preset '{name}' 声明 deps_teams=[{team_name!r}] 但模块未定义 "
                f"变量 {team_name.upper()!r} 或 {team_name!r}"
            )
        _post_or_put(
            f"{api}/api/teams",
            f"{api}/api/teams/{team_name}",
            team_to_dict(sub_team), "teams",
        )

    # 3. TEAM (主团队)
    _post_or_put(
        f"{api}/api/teams",
        f"{api}/api/teams/{mod.TEAM.name}",
        team_to_dict(mod.TEAM), "teams",
    )

    return result
```

**设计要点:**
- `PRESET_REGISTRY` 显式列出 4 个 preset 的模块路径(便于扩展)
- `list_presets()` 返回 METADATA 列表(供 CLI 展示)
- `get_preset(name)` 返回模块对象(供 CLI show-preset 与 install-preset 复用)
- `install_preset_to_api` 用 POST-then-PUT 实现幂等安装,顺序保证依赖先就位
- sub-team 命名约定:模块级变量名为 team.name 大写(如 `TEST_SUBTEAM`),CLI/installer 优先找大写,回退原 name

### 4.2 CLI 新增 3 命令(修改 `agentteam/cli.py`)

```python
# 新增 import
from agentteam.presets import list_presets, get_preset, install_preset_to_api

def list_presets_cmd() -> int:
    """列出所有可用预置团队。"""
    presets = list_presets()
    if not presets:
        print("(无预置团队)")
        return 0
    print(f"共 {len(presets)} 个预置团队:\n")
    for p in presets:
        print(f"  [{p['category']}] {p['name']}  {p['title']}")
        print(f"      {p['description']}")
        print(f"      tags: {', '.join(p['tags'])}")
        if p['deps_library']:
            print(f"      library: {', '.join(p['deps_library'])}")
        if p['deps_teams']:
            print(f"      sub-teams: {', '.join(p['deps_teams'])}")
        print()
    return 0


def show_preset(name: str) -> int:
    """显示指定预置团队的详细信息。"""
    try:
        mod = get_preset(name)
    except KeyError as e:
        print(f"错误: {e}")
        return 1
    meta = mod.METADATA
    team = mod.TEAM
    print(f"名称: {meta['name']}")
    print(f"标题: {meta['title']}")
    print(f"描述: {meta['description']}")
    print(f"分类: {meta['category']}")
    print(f"标签: {', '.join(meta['tags'])}")
    print(f"\n主团队: {team.name}")
    print(f"  描述: {team.description}")
    print(f"  root: {team.root.name} ({team.root.role})")
    if team.mcp_servers:
        print(f"  MCP (team-level): {[s.name for s in team.mcp_servers]}")
    print(f"\n专家库 agents ({len(mod.LIB_AGENTS)}):")
    for a in mod.LIB_AGENTS:
        print(f"  - {a.name} ({a.role}): tools={a.tools}")
    if meta['deps_teams']:
        print(f"\n依赖 sub-teams: {meta['deps_teams']}")
    return 0


def install_preset(name: str, api: str = "http://localhost:8000") -> int:
    """安装预置团队到 API 服务。"""
    try:
        result = install_preset_to_api(name, api=api)
    except KeyError as e:
        print(f"错误: {e}")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1
    print(f"预置团队 '{name}' 安装成功:")
    if result["library"]:
        print(f"  专家库: {result['library']}")
    print(f"  团队: {result['teams']}")
    return 0


# main() 中新增:
p_list_p = sub.add_parser("list-presets", help="列出所有预置团队")
p_show_p = sub.add_parser("show-preset", help="显示预置团队详情")
p_show_p.add_argument("name", help="预置团队名称")
p_install_p = sub.add_parser("install-preset", help="安装预置团队到 API")
p_install_p.add_argument("name", help="预置团队名称")
p_install_p.add_argument("--api", default="http://localhost:8000", help="API 地址")

# 分支:
elif args.command == "list-presets":
    return list_presets_cmd()
elif args.command == "show-preset":
    return show_preset(args.name)
elif args.command == "install-preset":
    return install_preset(args.name, args.api)
```

## 5. 测试策略

### 5.1 单元测试 — 每个 preset 模块

`tests/presets/test_<name>.py` 每个文件验证:
1. `mod.TEAM` 是 `Team` 实例,且 `team.root` 非 None
2. `mod.LIB_AGENTS` 是 `list[Agent]`(可空)
3. `mod.METADATA` 包含必需 keys(name/title/description/category/tags/deps_teams/deps_library)
4. `METADATA["name"]` 与模块文件名一致
5. `LIB_AGENTS` 中每个 agent.name 出现在 `METADATA["deps_library"]` 中
6. `deps_teams` 中每个 name 在模块中有对应 Team 变量(大写或原名)
7. **编译验证**:用 stub `ModelProvider` + `ToolRegistry` + `AgentLibrary`(注册 LIB_AGENTS) + `TeamCompiler` 编译 TEAM,断言不抛异常。注册 deps_teams 到 compiler._team_registry 后再编译主 TEAM
8. **MCP 验证**:断言至少 1 个 MCP 挂载(Team 级或 Agent 级)

### 5.2 单元测试 — Catalog

`tests/presets/test_catalog.py`:
1. `list_presets()` 返回 4 个 entry,name 包含 4 个预期值
2. `get_preset("enterprise_dev")` 返回模块,`TEAM.name` 非 None
3. `get_preset("nonexistent")` 抛 KeyError
4. 每个 preset 模块的 `METADATA["name"]` 与 `PRESET_REGISTRY` key 一致

### 5.3 集成测试 — CLI 命令

`tests/test_cli_presets.py`:
1. `list-presets` 命令调用 `main(["list-presets"])` 返回 0,stdout 含 4 个 preset name
2. `show-preset enterprise_dev` 返回 0,stdout 含 "CEO" 或 "CTO"
3. `show-preset nonexistent` 返回 1,stderr/stdout 含错误信息
4. `install-preset <name>` 用 `requests_mock` 或 `monkeypatch` mock HTTP,验证 POST 调用顺序:library → sub-teams → team
5. `install-preset` 重复安装(POST 返回 400)→ 回退 PUT,验证幂等

### 5.4 端到端测试(可选)

`tests/integration/test_preset_install_e2e.py`:
1. 启动 TestClient(create_app with tmp_path db)
2. `install_preset_to_api("enterprise_dev", api=client)` 安装
3. `GET /api/teams/enterprise_dev` 验证 200
4. `GET /api/library/agents/code_engineer` 验证 200
5. `GET /api/teams/test_subteam` 验证 200(依赖 sub-team 也注册)
6. 重复安装 → 仍 200(幂等)

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| preset name 不存在 | CLI 返回 1,打印 "Preset 'X' not found. Available: [...]" |
| API 不可达 | CLI 捕获 `requests.ConnectionError`,返回 1,打印 "无法连接到 {api}" |
| POST 返回 400(重复) | 自动回退 PUT(SPY 热更新);PUT 也失败则抛 RuntimeError |
| POST 返回 4xx/5xx(非重复) | 抛 RuntimeError,CLI 打印错误返回 1 |
| LIB_AGENTS 含 ref 但 deps_library 未声明 | 模块测试断言失败(测试期捕获,不进生产) |
| deps_teams 中 sub-team 未在模块定义 | install 时 `getattr` 失败 → RuntimeError |

## 7. 与 SP1-SP4 的协同

| SP | 协同点 |
|---|---|
| SP1 | 复用 Agent/Team/TeamRef/AgentLibrary/ApprovalPolicy/TeamCompiler;预设团队展示多级层级 + 专家库引用 + 嵌套 Team |
| SP2 | 复用 MCPServer + mcp_overrides;预设团队展示 Team/Agent/TeamRef 三层 MCP 挂载 |
| SP3 | install-preset 通过 POST /api/teams + /api/library/agents 持久化到 SQLite;重启后预置团队仍在 |
| SP4 | install-preset 用 POST-then-PUT 实现幂等;用户可用 PUT/admin-reload 热更新已安装的 preset |

## 8. 验收标准

- [ ] 4 个 preset 模块文件存在,每个导出 TEAM/LIB_AGENTS/METADATA
- [ ] `agentteam.presets.list_presets()` 返回 4 个 METADATA
- [ ] `agentteam.presets.get_preset(name)` 对 4 个 name 都返回有效模块
- [ ] 每个 preset 的 Team 能被 TeamCompiler 成功编译(用 stub provider)
- [ ] CLI `list-presets` / `show-preset` / `install-preset` 3 命令工作正常
- [ ] `install-preset` 重复调用幂等(POST→PUT 回退)
- [ ] 全套测试通过(原 331 + 新增 ~20+ = 350+)
- [ ] 向后兼容:SP1-SP4 测试无回归

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| preset 的 MCP server 命令是假想的(npx 包不存在) | 仅定义配置不启动进程;测试用 stub provider 不实际调用 MCP;文档注明"需用户自行配置 MCP server" |
| TeamRef 引用 sub-team 编译失败 | 测试中显式注册 deps_teams 到 compiler._team_registry;install_preset_to_api 先注册 sub-team |
| install_preset_to_api 中 sub-team 变量名约定(大写)易出错 | 测试覆盖大写/原名两种情况;helper 优先大写回退原名 |
| CLI HTTP 调用难测试 | 用 monkeypatch 替换 requests.post/put,验证调用参数与顺序 |

## 10. 未来扩展(不在 SP5 范围)

- Web UI 集成:`GET /api/presets` 端点 + 前端 preset 浏览页
- 用户自定义 preset:用户将自己的 Team 加入 `~/.agentteam/presets/` 目录,自动被 catalog 发现
- Preset 版本化:METADATA 加 `version` 字段,install 时支持 `--upgrade`
- Preset 依赖链:preset A 依赖 preset B 的 sub-team,catalog 解析依赖图按序安装
- YAML 配置支持:为非开发者用户提供 YAML preset 格式 + 转换工具
