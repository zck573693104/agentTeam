# SP5 预置企业级团队实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 4 个开箱即用的企业级预置团队(研发/客服/数据分析/内容营销),通过 CLI 一键安装到 API 服务,展示 SP1-SP4 全部能力。

**Architecture:** `agentteam/presets/` Python 包,每 preset 一个模块导出 `TEAM`/`LIB_AGENTS`/`METADATA`;catalog `__init__.py` 维护 `PRESET_REGISTRY`并提供 `list_presets`/`get_preset`/`install_preset_to_api`;CLI 新增 3 命令(list-presets/show-preset/install-preset)。install 顺序:LIB_AGENTS → deps_teams → TEAM,POST 失败回退 PUT(SP4 热更新能力)实现幂等。

**Tech Stack:** Python 3.11+, FastAPI, pytest, requests, langgraph MemorySaver

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/presets/__init__.py` | catalog + install helper | 新建 |
| `agentteam/presets/enterprise_dev.py` | 研发团队(CEO→CTO→eng/tester/reviewer + 测试小队) | 新建 |
| `agentteam/presets/customer_support.py` | 客服团队(主管→一线→升级→投诉) | 新建 |
| `agentteam/presets/data_analysis.py` | 数据分析团队(总监→SQL→可视化) | 新建 |
| `agentteam/presets/content_marketing.py` | 内容营销团队(主编→策划→写手→SEO) | 新建 |
| `agentteam/cli.py` | CLI 入口 | 修改:+3 命令 |
| `tests/presets/__init__.py` | 测试包标记 | 新建(空) |
| `tests/presets/test_catalog.py` | catalog 测试 | 新建 |
| `tests/presets/test_enterprise_dev.py` | 研发 preset 测试 | 新建 |
| `tests/presets/test_customer_support.py` | 客服 preset 测试 | 新建 |
| `tests/presets/test_data_analysis.py` | 数据分析 preset 测试 | 新建 |
| `tests/presets/test_content_marketing.py` | 内容营销 preset 测试 | 新建 |
| `tests/presets/test_install.py` | install_preset_to_api 测试(mocked HTTP) | 新建 |
| `tests/test_cli_presets.py` | CLI preset 命令测试 | 新建 |
| `tests/integration/test_preset_install_e2e.py` | E2E 安装测试(TestClient) | 新建 |

---

## Task 1: Catalog 包骨架(list_presets + get_preset)

**Files:**
- Create: `agentteam/presets/__init__.py`
- Create: `tests/presets/__init__.py` (空文件)
- Create: `tests/presets/test_catalog.py`

- [ ] **Step 1: 写失败测试 — catalog 基础接口**

创建 `d:\project\agentTeam\tests\presets\__init__.py`(空文件)。

创建 `d:\project\agentTeam\tests\presets\test_catalog.py`:

```python
"""Preset catalog 基础接口测试。"""
import pytest


def test_list_presets_returns_list():
    """list_presets() 返回 list(初始可为空)。"""
    from agentteam.presets import list_presets
    result = list_presets()
    assert isinstance(result, list)


def test_get_preset_nonexistent_raises_keyerror():
    """get_preset 不存在名字抛 KeyError,错误信息含可用列表。"""
    from agentteam.presets import get_preset
    with pytest.raises(KeyError) as exc_info:
        get_preset("nonexistent")
    assert "nonexistent" in str(exc_info.value)
    assert "Available" in str(exc_info.value)


def test_preset_registry_is_dict():
    """PRESET_REGISTRY 是 dict(初始可空,后续 task 填充)。"""
    from agentteam.presets import PRESET_REGISTRY
    assert isinstance(PRESET_REGISTRY, dict)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/presets/test_catalog.py -v`
Expected: 3 个测试 FAIL(ModuleNotFoundError: No module named 'agentteam.presets')

- [ ] **Step 3: 实现 — 创建 catalog 骨架**

创建 `d:\project\agentTeam\agentteam\presets\__init__.py`:

```python
"""预置企业级团队目录与安装 helper。

每个 preset 模块导出 3 个模块级变量:
- TEAM: Team — 主团队定义
- LIB_AGENTS: list[Agent] — 依赖的专家库 agent(可空列表)
- METADATA: dict — name/title/description/category/tags/deps_teams/deps_library

catalog 接口:
- list_presets() -> list[dict]: 返回所有 preset 的 METADATA 列表
- get_preset(name) -> ModuleType: 按 name 获取 preset 模块
- install_preset_to_api(name, api): 安装到 API 服务(见 Task 6)
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

# Preset 注册表:name → 模块路径。后续 task 逐步填充。
PRESET_REGISTRY: dict[str, str] = {}


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
        raise KeyError(
            f"Preset '{name}' not found. Available: {sorted(PRESET_REGISTRY)}"
        )
    return importlib.import_module(PRESET_REGISTRY[name])
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/presets/test_catalog.py -v`
Expected: 3 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/presets/__init__.py tests/presets/__init__.py tests/presets/test_catalog.py
git commit -m "feat(presets): catalog 骨架 list_presets/get_preset"
```

---

## Task 2: enterprise_dev 预置团队(最复杂 — 含 sub-team + library + MCP + 审批)

**Files:**
- Create: `agentteam/presets/enterprise_dev.py`
- Modify: `agentteam/presets/__init__.py` (PRESET_REGISTRY 注册)
- Create: `tests/presets/test_enterprise_dev.py`
- Modify: `tests/presets/test_catalog.py` (新增 enterprise_dev 验证)

- [ ] **Step 1: 写失败测试 — enterprise_dev preset 结构与编译验证**

创建 `d:\project\agentTeam\tests\presets\test_enterprise_dev.py`:

```python
"""enterprise_dev 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.library import AgentLibrary
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """构造 fake mcp_loader,产出 git_status/git_diff/git_log 工具。"""
    def _git_status() -> str:
        return "clean"

    def _git_diff() -> str:
        return ""

    def _git_log() -> str:
        return "commit log"

    fake_tools = [
        StructuredTool.from_function(name="git_status", description="git status", func=_git_status),
        StructuredTool.from_function(name="git_diff", description="git diff", func=_git_diff),
        StructuredTool.from_function(name="git_log", description="git log", func=_git_log),
    ]
    return lambda server: fake_tools


def test_enterprise_dev_module_exports():
    """enterprise_dev 模块导出 TEAM/LIB_AGENTS/METADATA/TEST_SUBTEAM。"""
    from agentteam.presets import enterprise_dev
    assert isinstance(enterprise_dev.TEAM, Team)
    assert isinstance(enterprise_dev.LIB_AGENTS, list)
    assert isinstance(enterprise_dev.METADATA, dict)
    assert isinstance(enterprise_dev.TEST_SUBTEAM, Team)


def test_enterprise_dev_metadata_required_keys():
    """METADATA 包含所有必需 keys。"""
    from agentteam.presets import enterprise_dev
    meta = enterprise_dev.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta, f"METADATA 缺少 key: {key}"
    assert meta["name"] == "enterprise_dev"
    assert meta["category"] == "research"


def test_enterprise_dev_lib_agents_match_metadata():
    """LIB_AGENTS 中每个 agent.name 出现在 deps_library 中。"""
    from agentteam.presets import enterprise_dev
    meta = enterprise_dev.METADATA
    lib_names = {a.name for a in enterprise_dev.LIB_AGENTS}
    assert lib_names == set(meta["deps_library"]), \
        f"LIB_AGENTS names {lib_names} != deps_library {meta['deps_library']}"


def test_enterprise_dev_deps_teams_variable_exists():
    """deps_teams 中每个 name 在模块中有对应 Team 变量(大写优先)。"""
    from agentteam.presets import enterprise_dev
    for team_name in enterprise_dev.METADATA["deps_teams"]:
        var_upper = team_name.upper()
        var_orig = team_name
        sub_team = getattr(enterprise_dev, var_upper, None) or getattr(enterprise_dev, var_orig, None)
        assert sub_team is not None, \
            f"deps_teams 声明 {team_name!r} 但模块未定义 {var_upper!r} 或 {var_orig!r}"
        assert isinstance(sub_team, Team)


def test_enterprise_dev_has_mcp():
    """至少 1 个 MCP 挂载(Team 级或 Agent 级)。"""
    from agentteam.presets import enterprise_dev
    team = enterprise_dev.TEAM
    has_team_mcp = len(team.mcp_servers) > 0
    # 递归检查 agent 级 MCP
    def _has_agent_mcp(agent):
        if agent.mcp_servers:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_agent_mcp(child):
                return True
        return False
    assert has_team_mcp or _has_agent_mcp(team.root), "enterprise_dev 应至少挂载 1 个 MCP"


def test_enterprise_dev_has_approval_policy():
    """至少 1 个声明式审批策略。"""
    from agentteam.presets import enterprise_dev
    team = enterprise_dev.TEAM

    def _has_approval(agent):
        if agent.approval_policy is not None:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_approval(child):
                return True
        return False
    assert _has_approval(team.root), "enterprise_dev 应至少有 1 个 approval_policy"


def test_enterprise_dev_team_compiles():
    """TEAM 可被 TeamCompiler 成功编译(注册 library + sub-team 后)。"""
    from agentteam.presets import enterprise_dev
    mod = enterprise_dev

    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)

    # 注册专家库
    lib = AgentLibrary()
    for a in mod.LIB_AGENTS:
        lib.register(a)

    compiler = TeamCompiler(provider, reg, library=lib)
    # 注册 sub-team(供 TeamRef 解析)
    for team_name in mod.METADATA["deps_teams"]:
        sub_team = getattr(mod, team_name.upper(), None) or getattr(mod, team_name)
        compiler.register_team(sub_team)

    # 编译主 TEAM(不 invoke)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    # CEO 是 root supervisor,应有 leader_plan 节点
    assert "leader_plan" in node_names
    # eng 是 worker
    assert "worker_eng" in node_names


def test_enterprise_dev_subteam_compiles():
    """TEST_SUBTEAM 可独立编译。"""
    from agentteam.presets import enterprise_dev
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(enterprise_dev.TEST_SUBTEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "worker_tester" in node_names
```

- [ ] **Step 2: 在 catalog 测试中新增 enterprise_dev 验证**

在 `d:\project\agentTeam\tests\presets\test_catalog.py` 末尾追加:

```python
def test_list_presets_includes_enterprise_dev():
    """list_presets 包含 enterprise_dev 条目。"""
    from agentteam.presets import list_presets
    result = list_presets()
    names = [p["name"] for p in result]
    assert "enterprise_dev" in names


def test_get_preset_enterprise_dev_returns_module():
    """get_preset('enterprise_dev') 返回有效模块。"""
    from agentteam.presets import get_preset
    mod = get_preset("enterprise_dev")
    assert hasattr(mod, "TEAM")
    assert hasattr(mod, "LIB_AGENTS")
    assert hasattr(mod, "METADATA")
    assert mod.METADATA["name"] == "enterprise_dev"
```

- [ ] **Step 3: 运行测试验证失败**

Run: `python -m pytest tests/presets/ -v`
Expected: enterprise_dev 相关测试 FAIL(ModuleNotFoundError: No module named 'agentteam.presets.enterprise_dev')

- [ ] **Step 4: 实现 — 创建 enterprise_dev preset 模块**

创建 `d:\project\agentTeam\agentteam\presets\enterprise_dev.py`:

```python
"""企业级研发团队预置 — CEO→CTO→工程师+测试+审查。

展示 SP1-SP4 全部能力:
- SP1:3 级 supervisor 链 + 专家库引用 + TeamRef 嵌套 + 声明式审批
- SP2:Team 级 git MCP 挂载
- SP3/SP4:通过 install-preset 持久化与幂等安装
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


# —— 专家库 agents ——
LIB_AGENTS: list[Agent] = [
    Agent(
        name="code_engineer", role="worker",
        system_prompt=(
            "你是代码工程师,使用 read_file/write_file 完成编码任务,"
            "使用 git MCP 工具(git_status/git_diff/git_log)查看仓库状态。"
            "写文件前需审批。"
        ),
        tools=["read_file", "write_file",
               "mcp:git:git_status", "mcp:git:git_diff", "mcp:git:git_log"],
        max_iterations=10,
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
    ),
]


# —— 子 Team:测试小队(供主 Team 通过 TeamRef 引用) ——
TEST_SUBTEAM: Team = Team(
    name="test_subteam",
    description="测试小队 — 主管 + 测试员",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管,派活给 tester。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
            tools=["read_file", "write_file"], max_iterations=5,
            approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)


# —— 主 Team:企业级研发团队 ——
TEAM: Team = Team(
    name="enterprise_dev",
    description="企业级研发团队 — CEO→CTO→工程师+测试+审查,含 git MCP、写文件审批、嵌套测试小队",
    default_model=ModelRef("qwen", "qwen-max"),
    # Team 级 MCP:全队共享 git 服务
    mcp_servers=[
        MCPServer(
            name="git", command="npx",
            args=["-y", "@modelcontextprotocol/server-git", "--repository", "."],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO,派活给技术副总裁 CTO,审核 CTO 产出。",
        children=[Agent(
            name="cto", role="supervisor",
            system_prompt=(
                "你是 CTO,派活给工程师(eng)、审查员(reviewer)和测试小队(qa_team),"
                "汇总各方产出回复 CEO。"
            ),
            children=[
                # 专家库引用:复用 code_engineer 模板,覆盖 name 为 eng
                Agent(name="eng", role="worker", ref="library:code_engineer"),
                # 审查员
                Agent(
                    name="reviewer", role="worker",
                    system_prompt="你是代码审查员,使用 read_file 审查代码与测试质量,给出改进建议。",
                    tools=["read_file"], max_iterations=5,
                ),
                # Team 嵌套:引用测试小队,重命名为 qa_team
                TeamRef(name="test_subteam", alias="qa_team"),
            ],
        )],
    ),
)


METADATA: dict = {
    "name": "enterprise_dev",
    "title": "企业级研发团队",
    "description": "CEO→CTO→工程师+测试+审查,含 git MCP、写文件审批、嵌套测试小队",
    "category": "research",
    "tags": ["研发", "MCP", "审批", "多层级", "专家库"],
    "deps_teams": ["test_subteam"],
    "deps_library": ["code_engineer"],
}
```

- [ ] **Step 5: 实现 — 在 catalog 注册 enterprise_dev**

修改 `d:\project\agentTeam\agentteam\presets\__init__.py` 的 `PRESET_REGISTRY`:

```python
PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
}
```

- [ ] **Step 6: 运行测试验证通过**

Run: `python -m pytest tests/presets/ -v`
Expected: 全部 PASS(原 3 catalog + 新 2 catalog + 新 8 enterprise_dev = 13)

- [ ] **Step 7: 提交**

```powershell
git add agentteam/presets/enterprise_dev.py agentteam/presets/__init__.py tests/presets/test_enterprise_dev.py tests/presets/test_catalog.py
git commit -m "feat(presets): enterprise_dev 预置研发团队"
```

---

## Task 3: customer_support 预置团队

**Files:**
- Create: `agentteam/presets/customer_support.py`
- Modify: `agentteam/presets/__init__.py` (PRESET_REGISTRY 注册)
- Create: `tests/presets/test_customer_support.py`

- [ ] **Step 1: 写失败测试 — customer_support preset**

创建 `d:\project\agentTeam\tests\presets\test_customer_support.py`:

```python
"""customer_support 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 ticket 工具。"""
    def _list_tickets() -> str:
        return "[]"

    def _get_ticket() -> str:
        return "{}"

    def _create_note() -> str:
        return "ok"

    def _resolve_ticket() -> str:
        return "resolved"

    def _escalate_complaint() -> str:
        return "escalated"

    fake_tools = [
        StructuredTool.from_function(name=n, description=f"fake {n}", func=f)
        for n, f in [
            ("list_tickets", _list_tickets),
            ("get_ticket", _get_ticket),
            ("create_note", _create_note),
            ("resolve_ticket", _resolve_ticket),
            ("escalate_complaint", _escalate_complaint),
        ]
    ]
    return lambda server: fake_tools


def test_customer_support_module_exports():
    from agentteam.presets import customer_support
    assert isinstance(customer_support.TEAM, Team)
    assert isinstance(customer_support.LIB_AGENTS, list)
    assert isinstance(customer_support.METADATA, dict)


def test_customer_support_metadata_required_keys():
    from agentteam.presets import customer_support
    meta = customer_support.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "customer_support"
    assert meta["category"] == "support"


def test_customer_support_lib_agents_empty():
    """客服团队无专家库依赖。"""
    from agentteam.presets import customer_support
    assert customer_support.LIB_AGENTS == []
    assert customer_support.METADATA["deps_library"] == []


def test_customer_support_has_mcp():
    from agentteam.presets import customer_support
    team = customer_support.TEAM
    assert len(team.mcp_servers) > 0, "应挂载 ticket MCP"


def test_customer_support_has_approval_policy():
    """escalation(supervisor)与 complaint_handler(worker)应有审批策略。"""
    from agentteam.presets import customer_support
    team = customer_support.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    escalation = _find_agent(team.root, "escalation")
    complaint_handler = _find_agent(team.root, "complaint_handler")
    assert escalation is not None and escalation.approval_policy is not None
    assert complaint_handler is not None and complaint_handler.approval_policy is not None


def test_customer_support_team_compiles():
    from agentteam.presets import customer_support
    mod = customer_support
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # support_manager 是 root supervisor
    assert "worker_frontline" in node_names
    assert "worker_complaint_handler" in node_names
    # escalation 是 supervisor,有自己的 leader_plan(节点名含 escalation)
    assert any("escalation" in n for n in node_names)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/presets/test_customer_support.py -v`
Expected: 6 个测试 FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 — 创建 customer_support preset 模块**

创建 `d:\project\agentTeam\agentteam\presets\customer_support.py`:

```python
"""客户支持团队预置 — 主管→一线→升级专员→投诉处理。

展示能力:
- SP1:supervisor→supervisor→worker 多级 + 声明式审批(升级 step 级,投诉 tool 级)
- SP2:Team 级 ticket MCP 挂载
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="customer_support",
    description="客户支持团队 — 主管→一线→升级专员→投诉处理,挂接工单 MCP,升级与投诉需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="ticket", command="npx",
            args=["-y", "@modelcontextprotocol/server-ticket"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="support_manager", role="supervisor",
        system_prompt=(
            "你是客服主管,派活给一线客服(frontline)、升级专员(escalation)"
            "和投诉处理员(complaint_handler),汇总处理结果。"
        ),
        children=[
            # 一线客服:处理常规工单
            Agent(
                name="frontline", role="worker",
                system_prompt=(
                    "你是一线客服,使用 ticket MCP 列出/查看工单、创建内部备注,"
                    "处理常规客户咨询。无法解决时升级给 escalation。"
                ),
                tools=["mcp:ticket:list_tickets", "mcp:ticket:get_ticket",
                       "mcp:ticket:create_note"],
                max_iterations=10,
            ),
            # 升级专员:supervisor,每步审批(防止升级决策失控)
            Agent(
                name="escalation", role="supervisor",
                system_prompt="你是升级专员,处理一线无法解决的复杂问题,派活给 specialist。",
                approval_policy=ApprovalPolicy(level="step"),
                children=[Agent(
                    name="specialist", role="worker",
                    system_prompt="你是技术专家,使用 ticket MCP 解决工单,必要时 read_file 查看文档。",
                    tools=["mcp:ticket:resolve_ticket", "read_file"],
                    max_iterations=8,
                )],
            ),
            # 投诉处理员:升级投诉前需审批
            Agent(
                name="complaint_handler", role="worker",
                system_prompt="你是投诉处理员,使用 ticket MCP 升级投诉工单。升级前需审批。",
                tools=["mcp:ticket:escalate_complaint"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:ticket:escalate_complaint"],
                ),
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "customer_support",
    "title": "客户支持团队",
    "description": "主管→一线→升级专员→投诉处理,挂接工单 MCP,升级与投诉需审批",
    "category": "support",
    "tags": ["客服", "MCP", "审批", "工单"],
    "deps_teams": [],
    "deps_library": [],
}
```

- [ ] **Step 4: 实现 — 在 catalog 注册 customer_support**

修改 `d:\project\agentTeam\agentteam\presets\__init__.py` 的 `PRESET_REGISTRY`:

```python
PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
    "customer_support": "agentteam.presets.customer_support",
}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/presets/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```powershell
git add agentteam/presets/customer_support.py agentteam/presets/__init__.py tests/presets/test_customer_support.py
git commit -m "feat(presets): customer_support 预置客服团队"
```

---

## Task 4: data_analysis 预置团队

**Files:**
- Create: `agentteam/presets/data_analysis.py`
- Modify: `agentteam/presets/__init__.py` (PRESET_REGISTRY 注册)
- Create: `tests/presets/test_data_analysis.py`

- [ ] **Step 1: 写失败测试 — data_analysis preset**

创建 `d:\project\agentTeam\tests\presets\test_data_analysis.py`:

```python
"""data_analysis 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 db 与 chart 工具。"""
    def _query() -> str:
        return "rows"

    def _schema() -> str:
        return "schema"

    def _render() -> str:
        return "chart"

    def _export() -> str:
        return "exported"

    fake_tools = [
        StructuredTool.from_function(name=n, description=f"fake {n}", func=f)
        for n, f in [
            ("query", _query), ("schema", _schema),
            ("render", _render), ("export", _export),
        ]
    ]
    return lambda server: fake_tools


def test_data_analysis_module_exports():
    from agentteam.presets import data_analysis
    assert isinstance(data_analysis.TEAM, Team)
    assert isinstance(data_analysis.LIB_AGENTS, list)
    assert isinstance(data_analysis.METADATA, dict)


def test_data_analysis_metadata_required_keys():
    from agentteam.presets import data_analysis
    meta = data_analysis.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "data_analysis"
    assert meta["category"] == "analytics"


def test_data_analysis_has_two_team_level_mcp():
    """Team 级挂载 2 个 MCP:db + chart。"""
    from agentteam.presets import data_analysis
    team = data_analysis.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"db", "chart"} == mcp_names


def test_data_analysis_no_approval_policy():
    """数据分析无破坏性操作,不应有审批策略。"""
    from agentteam.presets import data_analysis
    team = data_analysis.TEAM

    def _has_approval(agent):
        if agent.approval_policy is not None:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_approval(child):
                return True
        return False
    assert not _has_approval(team.root), "data_analysis 不应有审批策略"


def test_data_analysis_team_compiles():
    from agentteam.presets import data_analysis
    mod = data_analysis
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # analytics_director 是 root supervisor
    assert "worker_sql_engineer" in node_names
    assert "worker_visualizer" in node_names
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/presets/test_data_analysis.py -v`
Expected: 5 个测试 FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 — 创建 data_analysis preset 模块**

创建 `d:\project\agentTeam\agentteam\presets\data_analysis.py`:

```python
"""数据分析团队预置 — 总监→SQL工程师→可视化师。

展示能力:
- SP1:supervisor→worker 平级编排(无审批,数据查询只读)
- SP2:Team 级 2 个 MCP 挂载(db + chart)
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="data_analysis",
    description="数据分析团队 — 总监→SQL工程师→可视化师,挂接数据库 MCP + 图表 MCP",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="db", command="npx",
            args=["-y", "@modelcontextprotocol/server-postgres",
                  "--connection-string", "postgresql://localhost/analytics"],
            transport="stdio",
        ),
        MCPServer(
            name="chart", command="npx",
            args=["-y", "@modelcontextprotocol/server-chart"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="analytics_director", role="supervisor",
        system_prompt=(
            "你是分析总监,派活给 SQL 工程师(sql_engineer)和可视化师(visualizer),"
            "汇总数据分析结果。"
        ),
        children=[
            Agent(
                name="sql_engineer", role="worker",
                system_prompt=(
                    "你是 SQL 工程师,使用 db MCP 执行查询(query)与查看 schema,"
                    "必要时 read_file 查看数据字典。"
                ),
                tools=["mcp:db:query", "mcp:db:schema", "read_file"],
                max_iterations=10,
            ),
            Agent(
                name="visualizer", role="worker",
                system_prompt=(
                    "你是可视化师,使用 chart MCP 渲染图表(render)并导出(export),"
                    "使用 write_file 保存图表配置。"
                ),
                tools=["mcp:chart:render", "mcp:chart:export", "write_file"],
                max_iterations=8,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "data_analysis",
    "title": "数据分析团队",
    "description": "总监→SQL工程师→可视化师,挂接数据库 MCP + 图表 MCP",
    "category": "analytics",
    "tags": ["数据分析", "MCP", "数据库", "可视化"],
    "deps_teams": [],
    "deps_library": [],
}
```

- [ ] **Step 4: 实现 — 在 catalog 注册 data_analysis**

修改 `d:\project\agentTeam\agentteam\presets\__init__.py` 的 `PRESET_REGISTRY`:

```python
PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
    "customer_support": "agentteam.presets.customer_support",
    "data_analysis": "agentteam.presets.data_analysis",
}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/presets/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```powershell
git add agentteam/presets/data_analysis.py agentteam/presets/__init__.py tests/presets/test_data_analysis.py
git commit -m "feat(presets): data_analysis 预置数据分析团队"
```

---

## Task 5: content_marketing 预置团队

**Files:**
- Create: `agentteam/presets/content_marketing.py`
- Modify: `agentteam/presets/__init__.py` (PRESET_REGISTRY 注册)
- Create: `tests/presets/test_content_marketing.py`

- [ ] **Step 1: 写失败测试 — content_marketing preset**

创建 `d:\project\agentTeam\tests\presets\test_content_marketing.py`:

```python
"""content_marketing 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 search 与 social 工具。"""
    def _query() -> str:
        return "results"

    def _trends() -> str:
        return "trends"

    def _keywords() -> str:
        return "keywords"

    def _schedule_post() -> str:
        return "scheduled"

    fake_tools = [
        StructuredTool.from_function(name=n, description=f"fake {n}", func=f)
        for n, f in [
            ("query", _query), ("trends", _trends),
            ("keywords", _keywords), ("schedule_post", _schedule_post),
        ]
    ]
    return lambda server: fake_tools


def test_content_marketing_module_exports():
    from agentteam.presets import content_marketing
    assert isinstance(content_marketing.TEAM, Team)
    assert isinstance(content_marketing.LIB_AGENTS, list)
    assert isinstance(content_marketing.METADATA, dict)


def test_content_marketing_metadata_required_keys():
    from agentteam.presets import content_marketing
    meta = content_marketing.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "content_marketing"
    assert meta["category"] == "marketing"


def test_content_marketing_has_two_team_level_mcp():
    """Team 级挂载 2 个 MCP:search + social。"""
    from agentteam.presets import content_marketing
    team = content_marketing.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"search", "social"} == mcp_names


def test_content_marketing_has_approval_policies():
    """writer 与 seo 应有 tool 级审批。"""
    from agentteam.presets import content_marketing
    team = content_marketing.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    writer = _find_agent(team.root, "writer")
    seo = _find_agent(team.root, "seo")
    assert writer is not None and writer.approval_policy is not None
    assert seo is not None and seo.approval_policy is not None


def test_content_marketing_team_compiles():
    from agentteam.presets import content_marketing
    mod = content_marketing
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # editor_in_chief 是 root supervisor
    assert "worker_planner" in node_names
    assert "worker_writer" in node_names
    assert "worker_seo" in node_names
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/presets/test_content_marketing.py -v`
Expected: 5 个测试 FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 — 创建 content_marketing preset 模块**

创建 `d:\project\agentTeam\agentteam\presets\content_marketing.py`:

```python
"""内容营销团队预置 — 主编→策划→写手→SEO。

展示能力:
- SP1:supervisor→worker 平级编排 + tool 级审批(写文件/社媒发布)
- SP2:Team 级 2 个 MCP 挂载(search + social)
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


LIB_AGENTS: list[Agent] = []


TEAM: Team = Team(
    name="content_marketing",
    description="内容营销团队 — 主编→策划→写手→SEO,挂接搜索 MCP + 社媒 MCP,写文与发布需审批",
    default_model=ModelRef("qwen", "qwen-max"),
    mcp_servers=[
        MCPServer(
            name="search", command="npx",
            args=["-y", "@modelcontextprotocol/server-search"],
            transport="stdio",
        ),
        MCPServer(
            name="social", command="npx",
            args=["-y", "@modelcontextprotocol/server-social-media"],
            transport="stdio",
        ),
    ],
    root=Agent(
        name="editor_in_chief", role="supervisor",
        system_prompt=(
            "你是主编,派活给选题策划(planner)、写手(writer)和 SEO 优化师(seo),"
            "汇总内容产出。"
        ),
        children=[
            Agent(
                name="planner", role="worker",
                system_prompt=(
                    "你是选题策划,使用 search MCP 搜索热点(query)与趋势(trends),"
                    "输出选题清单。"
                ),
                tools=["mcp:search:query", "mcp:search:trends"],
                max_iterations=5,
            ),
            Agent(
                name="writer", role="worker",
                system_prompt=(
                    "你是写手,使用 read_file/write_file 撰写文章。"
                    "写文件前需审批。"
                ),
                tools=["read_file", "write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
                max_iterations=10,
            ),
            Agent(
                name="seo", role="worker",
                system_prompt=(
                    "你是 SEO 优化师,使用 search MCP 查关键词(keywords),"
                    "使用 social MCP 安排发布(schedule_post)。发布前需审批。"
                ),
                tools=["mcp:search:keywords", "mcp:social:schedule_post"],
                approval_policy=ApprovalPolicy(
                    level="tool", targets=["mcp:social:schedule_post"],
                ),
                max_iterations=5,
            ),
        ],
    ),
)


METADATA: dict = {
    "name": "content_marketing",
    "title": "内容营销团队",
    "description": "主编→策划→写手→SEO,挂接搜索 MCP + 社媒 MCP,写文与发布需审批",
    "category": "marketing",
    "tags": ["内容", "营销", "MCP", "审批", "SEO"],
    "deps_teams": [],
    "deps_library": [],
}
```

- [ ] **Step 4: 实现 — 在 catalog 注册 content_marketing**

修改 `d:\project\agentTeam\agentteam\presets\__init__.py` 的 `PRESET_REGISTRY`:

```python
PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
    "customer_support": "agentteam.presets.customer_support",
    "data_analysis": "agentteam.presets.data_analysis",
    "content_marketing": "agentteam.presets.content_marketing",
}
```

- [ ] **Step 5: 运行测试验证通过 + 全部 4 个 preset 在 catalog**

Run: `python -m pytest tests/presets/ -v`
Expected: 全部 PASS,catalog 测试覆盖 4 个 preset

补充验证(在 `tests/presets/test_catalog.py` 末尾追加):

```python
def test_list_presets_returns_all_four():
    """list_presets 返回 4 个 preset(完成 Task 5 后)。"""
    from agentteam.presets import list_presets
    result = list_presets()
    names = sorted(p["name"] for p in result)
    assert names == ["content_marketing", "customer_support",
                     "data_analysis", "enterprise_dev"]
```

Run: `python -m pytest tests/presets/test_catalog.py::test_list_presets_returns_all_four -v`
Expected: PASS

- [ ] **Step 6: 提交**

```powershell
git add agentteam/presets/content_marketing.py agentteam/presets/__init__.py tests/presets/test_content_marketing.py tests/presets/test_catalog.py
git commit -m "feat(presets): content_marketing 预置内容营销团队,4 个 preset 完成"
```

---

## Task 6: install_preset_to_api helper(POST→PUT 幂等)

**Files:**
- Modify: `agentteam/presets/__init__.py` (新增 install_preset_to_api 函数)
- Create: `tests/presets/test_install.py`

- [ ] **Step 1: 写失败测试 — install_preset_to_api**

创建 `d:\project\agentTeam\tests\presets\test_install.py`:

```python
"""install_preset_to_api 测试(mocked HTTP)。"""
from unittest.mock import MagicMock, patch


def _fake_response(status_code: int, json_data: dict | None = None):
    """构造 fake requests.Response。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def test_install_preset_nonexistent_raises_keyerror():
    """install 不存在的 preset 抛 KeyError。"""
    from agentteam.presets import install_preset_to_api
    try:
        install_preset_to_api("nonexistent")
        assert False, "应抛 KeyError"
    except KeyError as e:
        assert "nonexistent" in str(e)


def test_install_preset_no_deps_posts_only_team():
    """无 deps 的 preset(customer_support):只 POST 1 次 team,无 library/sub-team。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "customer_support"})
        mock_req.ConnectionError = Exception  # patch class attr
        result = install_preset_to_api("customer_support", api="http://api")
    # 仅 POST team 1 次,无 library/sub-team POST
    assert mock_req.post.call_count == 1
    called_url = mock_req.post.call_args[0][0]
    assert called_url == "http://api/api/teams"
    assert result["teams"] == ["customer_support"]
    assert result["library"] == []


def test_install_preset_with_library_posts_library_then_team():
    """enterprise_dev:先 POST library(code_engineer),再 POST sub-team,再 POST team。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "x"})
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("enterprise_dev", api="http://api")
    # POST 顺序:library(1) + sub-team(1) + team(1) = 3 次
    assert mock_req.post.call_count == 3
    # 第 1 次:library
    first_url = mock_req.post.call_args_list[0][0][0]
    assert first_url == "http://api/api/library/agents"
    # 第 2 次:sub-team (test_subteam)
    second_url = mock_req.post.call_args_list[1][0][0]
    assert second_url == "http://api/api/teams"
    second_payload = mock_req.post.call_args_list[1][1]["json"]
    assert second_payload["name"] == "test_subteam"
    # 第 3 次:主 team
    third_payload = mock_req.post.call_args_list[2][1]["json"]
    assert third_payload["name"] == "enterprise_dev"
    assert result["library"] == ["code_engineer"]
    assert "test_subteam" in result["teams"]
    assert "enterprise_dev" in result["teams"]


def test_install_preset_duplicate_falls_back_to_put():
    """POST 返回 400(重复)→ 回退 PUT,实现幂等。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.requests") as mock_req:
        # POST 返回 400,PUT 返回 200
        mock_req.post.return_value = _fake_response(400, {"detail": "already exists"})
        mock_req.put.return_value = _fake_response(200, {"name": "customer_support"})
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("customer_support", api="http://api")
    # POST 1 次 + PUT 1 次
    assert mock_req.post.call_count == 1
    assert mock_req.put.call_count == 1
    put_url = mock_req.put.call_args[0][0]
    assert put_url == "http://api/api/teams/customer_support"
    assert result["teams"] == ["customer_support(updated)"]


def test_install_preset_post_5xx_raises_runtimeerror():
    """POST 返回 500(非重复)→ 抛 RuntimeError,不回退 PUT。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.requests") as mock_req:
        mock_req.post.return_value = _fake_response(500, {"detail": "server error"})
        mock_req.ConnectionError = Exception
        try:
            install_preset_to_api("customer_support", api="http://api")
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "500" in str(e) or "server error" in str(e)
    # 不应回退 PUT
    assert mock_req.put.call_count == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/presets/test_install.py -v`
Expected: 5 个测试 FAIL(AttributeError: module has no attribute 'install_preset_to_api')

- [ ] **Step 3: 实现 — 在 catalog 新增 install_preset_to_api**

修改 `d:\project\agentTeam\agentteam\presets\__init__.py`,在 `get_preset` 函数之后追加:

```python
def install_preset_to_api(name: str, api: str = "http://localhost:8000") -> dict[str, Any]:
    """安装预置团队到 API 服务。

    安装顺序(确保依赖先就位):
    1. 注册 LIB_AGENTS 到 /api/library/agents(POST 失败为 400 重复 → PUT 更新)
    2. 注册 deps_teams 中的 sub-team 到 /api/teams(POST 失败为 400 重复 → PUT 更新)
       sub-team 需在 preset 模块中定义为模块级变量(变量名 = team.name.upper() 优先)
    3. 注册 TEAM 到 /api/teams(POST 失败为 400 重复 → PUT 更新)

    返回 {"library": [...], "teams": [...]} 记录每步注册结果。
    幂等:重复安装时 POST→PUT 回退,不会因重复而失败。
    """
    import requests
    from dataclasses import asdict
    from agentteam.api.serializer import team_to_dict

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
        raise RuntimeError(
            f"注册 {label} '{payload.get('name')}' 失败: "
            f"{resp.status_code} {resp.text}"
        )

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

**注意:** 由于 `install_preset_to_api` 内部 `import requests`,测试中 `patch("agentteam.presets.requests")` 需要在函数被调用时才生效。为支持 patch,需在 `__init__.py` 顶部也 import requests 作为模块级引用。修改 `__init__.py` 顶部,在 `import importlib` 之后追加:

```python
import requests
```

并把 `install_preset_to_api` 内部的 `import requests` 删除(改用模块级引用)。同时函数内 `requests.post`/`requests.put`/`requests.ConnectionError` 通过模块级 `requests` 访问,patch 时替换 `agentteam.presets.requests` 即可。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/presets/test_install.py -v`
Expected: 5 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/presets/__init__.py tests/presets/test_install.py
git commit -m "feat(presets): install_preset_to_api POST→PUT 幂等安装"
```

---

## Task 7: CLI 3 命令(list-presets / show-preset / install-preset)

**Files:**
- Modify: `agentteam/cli.py`
- Create: `tests/test_cli_presets.py`

- [ ] **Step 1: 写失败测试 — CLI preset 命令**

创建 `d:\project\agentTeam\tests\test_cli_presets.py`:

```python
"""CLI preset 命令测试。"""
from unittest.mock import MagicMock, patch

from agentteam.cli import main


def test_cli_list_presets_returns_zero(capsys):
    """list-presets 命令返回 0,stdout 含 4 个 preset name。"""
    rc = main(["list-presets"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("enterprise_dev", "customer_support",
                 "data_analysis", "content_marketing"):
        assert name in out


def test_cli_show_preset_existing_returns_zero(capsys):
    """show-preset enterprise_dev 返回 0,stdout 含主团队名。"""
    rc = main(["show-preset", "enterprise_dev"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "enterprise_dev" in out
    assert "code_engineer" in out  # LIB_AGENTS 中的 agent


def test_cli_show_preset_nonexistent_returns_one(capsys):
    """show-preset nonexistent 返回 1,stdout 含错误信息。"""
    rc = main(["show-preset", "nonexistent"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "nonexistent" in out
    assert "Available" in out or "错误" in out


def test_cli_install_preset_calls_install_helper():
    """install-preset 调用 install_preset_to_api,成功返回 0。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.return_value = {"library": ["code_engineer"], "teams": ["enterprise_dev"]}
        rc = main(["install-preset", "enterprise_dev", "--api", "http://test"])
    assert rc == 0
    mock_install.assert_called_once_with("enterprise_dev", api="http://test")


def test_cli_install_preset_keyerror_returns_one(capsys):
    """install-preset 不存在 preset 返回 1。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.side_effect = KeyError("not found")
        rc = main(["install-preset", "nonexistent"])
    assert rc == 1


def test_cli_install_preset_runtimeerror_returns_one(capsys):
    """install-preset 安装失败(RuntimeError)返回 1。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.side_effect = RuntimeError("connection failed")
        rc = main(["install-preset", "enterprise_dev"])
    assert rc == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_cli_presets.py -v`
Expected: 6 个测试 FAIL(argparse 无 list-presets 子命令 → SystemExit)

- [ ] **Step 3: 实现 — 修改 cli.py 新增 3 命令**

Read `d:\project\agentTeam\agentteam\cli.py` 当前结构后,做以下修改:

#### 3a. 顶部追加 import(在 `from examples.dev_team import DEV_TEAM` 之后)

```python
from agentteam.presets import list_presets, get_preset, install_preset_to_api
```

#### 3b. 在 `register_library` 函数之后、`main` 函数之前,新增 3 个命令函数

```python
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
```

#### 3c. 在 `main()` 函数的 subparser 定义区,追加 3 个子命令

在 `p_lib = sub.add_parser("register-library", ...)` 之后追加:

```python
    p_list_p = sub.add_parser("list-presets", help="列出所有预置团队")
    p_show_p = sub.add_parser("show-preset", help="显示预置团队详情")
    p_show_p.add_argument("name", help="预置团队名称")
    p_install_p = sub.add_parser("install-preset", help="安装预置团队到 API")
    p_install_p.add_argument("name", help="预置团队名称")
    p_install_p.add_argument("--api", default="http://localhost:8000", help="API 地址")
```

#### 3d. 在 `main()` 的命令分支区,追加 3 个 elif

在 `elif args.command == "register-library":` 分支之后、`else:` 之前追加:

```python
    elif args.command == "list-presets":
        return list_presets_cmd()
    elif args.command == "show-preset":
        return show_preset(args.name)
    elif args.command == "install-preset":
        return install_preset(args.name, args.api)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_cli_presets.py -v`
Expected: 6 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/cli.py tests/test_cli_presets.py
git commit -m "feat(cli): list-presets/show-preset/install-preset 3 命令"
```

---

## Task 8: E2E 集成测试(TestClient + 真实 install_preset_to_api)

**Files:**
- Create: `tests/integration/test_preset_install_e2e.py`

- [ ] **Step 1: 写 E2E 测试**

创建 `d:\project\agentTeam\tests\integration\test_preset_install_e2e.py`:

```python
"""预置团队安装 E2E 测试 — TestClient + 真实 API + 真实 DB。

验证:
- install_preset_to_api 通过 HTTP 安装到 TestClient 启动的 API
- 安装后 GET /api/teams/{name} 返回 200
- 依赖的 library agent 与 sub-team 也注册成功
- 重复安装幂等(POST→PUT 回退)
"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app
from agentteam.presets import install_preset_to_api


def _install_via_testclient(name: str, client: TestClient) -> dict:
    """用 TestClient 的 transport 替代 requests,调用 install_preset_to_api。"""
    # TestClient 的 base_url 默认为 http://testserver
    # install_preset_to_api 内部用 requests.post/put,需 patch 为 client.post/put
    from unittest.mock import MagicMock, patch

    def _post(url, json=None, timeout=None):
        path = url.replace("http://testserver", "")
        return client.post(path, json=json)

    def _put(url, json=None, timeout=None):
        path = url.replace("http://testserver", "")
        return client.put(path, json=json)

    with patch("agentteam.presets.requests") as mock_req:
        mock_req.post.side_effect = _post
        mock_req.put.side_effect = _put
        mock_req.ConnectionError = Exception
        return install_preset_to_api(name, api="http://testserver")


def test_install_enterprise_dev_e2e(tmp_path: Path):
    """安装 enterprise_dev:library + sub-team + 主 team 都注册成功。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    result = _install_via_testclient("enterprise_dev", client)
    assert "code_engineer" in result["library"]
    assert "test_subteam" in result["teams"]
    assert "enterprise_dev" in result["teams"]

    # 验证 GET
    assert client.get("/api/teams/enterprise_dev").status_code == 200
    assert client.get("/api/teams/test_subteam").status_code == 200
    assert client.get("/api/library/agents").status_code == 200
    agents = client.get("/api/library/agents").json()
    assert any(a["name"] == "code_engineer" for a in agents)


def test_install_customer_support_e2e(tmp_path: Path):
    """安装 customer_support(无 deps):只注册主 team。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    result = _install_via_testclient("customer_support", client)
    assert result["library"] == []
    assert "customer_support" in result["teams"]
    assert client.get("/api/teams/customer_support").status_code == 200


def test_install_is_idempotent(tmp_path: Path):
    """重复安装 enterprise_dev:第二次 POST→PUT 回退,仍成功。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 第一次安装
    result1 = _install_via_testclient("enterprise_dev", client)
    assert "enterprise_dev" in result1["teams"]

    # 第二次安装(应触发 PUT 回退)
    result2 = _install_via_testclient("enterprise_dev", client)
    # 第二次 teams 列表中应有 "(updated)" 标记
    assert any("updated" in t for t in result2["teams"])

    # GET 仍正常
    assert client.get("/api/teams/enterprise_dev").status_code == 200


def test_install_all_four_presets(tmp_path: Path):
    """安装全部 4 个 preset,各自 GET 200。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    for name in ("enterprise_dev", "customer_support",
                 "data_analysis", "content_marketing"):
        result = _install_via_testclient(name, client)
        assert name in result["teams"], f"{name} 安装失败: {result}"
        assert client.get(f"/api/teams/{name}").status_code == 200
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/integration/test_preset_install_e2e.py -v`
Expected: 4 PASS

- [ ] **Step 3: 提交**

```powershell
git add tests/integration/test_preset_install_e2e.py
git commit -m "test(presets): E2E 安装测试(TestClient + 4 个 preset 幂等)"
```

---

## Task 9: 全套测试验证与清理

**Files:** 无修改,仅验证

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -v`
Expected: 全部 PASS(原 331 + SP5 新增 ~35+ = 366+)

- [ ] **Step 2: 检查工作树状态**

Run: `git status`
Expected: clean working tree

- [ ] **Step 3: 检查最近提交历史**

Run: `git log --oneline -10`
Expected: 看到 SP5 的 8 个 feat/test 提交

- [ ] **Step 4: 验证向后兼容 — 运行原 SP1-SP4 测试**

Run: `python -m pytest tests/integration/ tests/api/ tests/domain/ tests/runtime/ tests/presets/ -q`
Expected: 全部 PASS(无回归)

- [ ] **Step 5: 手动验证 CLI(可选)**

Run: `python -m agentteam.cli list-presets`
Expected: 输出 4 个 preset 列表

Run: `python -m agentteam.cli show-preset enterprise_dev`
Expected: 输出 enterprise_dev 详情

- [ ] **Step 6: 如有未提交的修复,提交**

```powershell
git add -A
git commit -m "test: SP5 全套测试验证通过"
```

(若无修改则跳过)

---

## Self-Review

**1. Spec coverage:**
- ✅ 4 个预置团队模块 — Task 2-5
- ✅ catalog `PRESET_REGISTRY` + `list_presets` + `get_preset` — Task 1
- ✅ `install_preset_to_api` POST→PUT 幂等 — Task 6
- ✅ CLI 3 命令 — Task 7
- ✅ 每个 preset 的 Team 可被 TeamCompiler 编译 — Task 2-5 编译测试
- ✅ 每个 preset 至少 1 个 MCP + 至少 1 个审批(data_analysis 例外:无审批,测试显式验证) — Task 2-5
- ✅ E2E 安装 + 幂等 — Task 8
- ✅ 向后兼容:Task 9 Step 4 验证
- ✅ 全套测试通过 — Task 9 Step 1

**2. Placeholder scan:**
- 无 "TBD"/"TODO"
- 每个步骤都有完整代码或命令
- 测试代码完整可运行

**3. Type consistency:**
- `PRESET_REGISTRY: dict[str, str]` — name → module path
- `list_presets() -> list[dict[str, Any]]` — 返回 METADATA 列表
- `get_preset(name) -> ModuleType` — 抛 KeyError
- `install_preset_to_api(name, api) -> dict[str, list[str]]` — 返回 {"library": [...], "teams": [...]}
- preset 模块契约:TEAM/LIB_AGENTS/METADATA + 可选 `<TEAM_NAME_UPPER>` sub-team 变量 — 一致
- CLI 函数签名:`list_presets_cmd() -> int` / `show_preset(name) -> int` / `install_preset(name, api) -> int` — 一致

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp5-preset-enterprise-teams.md`。两种执行方式:

1. **Subagent-Driven(推荐)** — 每个任务派发新 subagent,任务间 review
2. **Inline Execution** — 在当前会话执行,批量 checkpoint review

选择哪种?
