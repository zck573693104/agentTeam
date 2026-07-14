# M6 示例团队 + 集成测试 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 内置「研发小队」示例团队,配套 search_web stub 技能、CLI 注册命令、全面集成测试套件,验证框架端到端可用。

**Architecture:** search_web 作为原生 @tool 技能加入 ToolRegistry;examples/dev_team.py 导出团队 JSON dict,CLI 和测试共用;集成测试用 FakeLLM + fake_mcp_loader 跑 RunManager 级 E2E 场景,不依赖真实模型/MCP 子进程。CLI 用 Python 内置 argparse,不引入新依赖。

**Tech Stack:** Python 3.10+, langchain-core @tool, argparse, pytest, FakeLLM

**Spec:** `docs/superpowers/specs/2026-07-14-m6-sample-team-design.md`

---

## 文件结构

| 文件 | 职责 | 任务 |
|------|------|------|
| `agentteam/tools/skills/search_web.py` | search_web stub 技能 | Task 1 |
| `agentteam/tools/skills/__init__.py` | 注册 search_web 到 _BUILTIN_TOOLS | Task 1 |
| `tests/tools/skills/test_search_web.py` | search_web 单元测试 | Task 1 |
| `examples/__init__.py` | 包初始化 | Task 2 |
| `examples/dev_team.py` | 研发小队 DEV_TEAM dict 定义 | Task 2 |
| `tests/integration/__init__.py` | 包初始化 | Task 2 |
| `tests/integration/conftest.py` | 集成测试共享 fixture | Task 2 |
| `tests/integration/test_dev_team_config.py` | DEV_TEAM 配置 + MCP git 配置验证 | Task 2 |
| `agentteam/cli.py` | CLI 入口(register-dev-team) | Task 3 |
| `tests/test_cli.py` | CLI 测试 | Task 3 |
| `tests/integration/test_e2e_normal.py` | E2E 正常完成 | Task 4 |
| `tests/integration/test_e2e_approval.py` | E2E step + tool 级审批 | Task 5 |
| `tests/integration/test_e2e_error.py` | E2E worker 异常 → run 失败 | Task 6 |
| `README.md` | M6 ✅ + 使用示例 | Task 6 |

---

## Task 1: search_web stub 技能 + 单元测试

**Files:**
- Create: `agentteam/tools/skills/search_web.py`
- Modify: `agentteam/tools/skills/__init__.py`
- Create: `tests/tools/skills/test_search_web.py`

- [ ] **Step 1: 编写 search_web 单元测试**

Create `tests/tools/skills/test_search_web.py`:

```python
"""search_web stub 技能单元测试。"""
from agentteam.tools.skills.search_web import search_web


def test_search_web_returns_text():
    """search_web 返回字符串。"""
    result = search_web.invoke({"query": "Python 异步编程"})
    assert isinstance(result, str)
    assert len(result) > 0


def test_search_web_echoes_query():
    """返回结果包含查询关键词。"""
    result = search_web.invoke({"query": "LangGraph 入门"})
    assert "LangGraph" in result


def test_search_web_respects_max_results():
    """max_results 控制返回条目数。"""
    result_1 = search_web.invoke({"query": "test", "max_results": 1})
    result_3 = search_web.invoke({"query": "test", "max_results": 3})
    # max_results=1 的结果条目数应少于 max_results=3
    count_1 = result_1.count("[结果")
    count_3 = result_3.count("[结果")
    assert count_1 == 1
    assert count_3 == 3


def test_search_web_registered_in_builtin_skills():
    """search_web 被注册到 register_builtin_skills。"""
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills

    reg = ToolRegistry()
    register_builtin_skills(reg)
    names = reg.list_names()
    assert "search_web" in names
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/tools/skills/test_search_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.tools.skills.search_web'`

- [ ] **Step 3: 实现 search_web 技能**

Create `agentteam/tools/skills/search_web.py`:

```python
"""search_web stub 技能：模拟网络搜索,返回基于查询的 mock 结果。

不调用真实搜索 API,仅用于演示 Worker ReAct 工具循环。
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_web(query: str, max_results: int = 3) -> str:
    """搜索网络,返回相关结果摘要。

    Args:
        query: 搜索关键词。
        max_results: 最大结果条目数,默认 3。

    Returns:
        模拟搜索结果文本,每条包含序号、标题、URL 和摘要。
    """
    lines = [f"搜索「{query}」的结果："]
    for i in range(1, max_results + 1):
        lines.append(f"[结果 {i}] {query} - 相关资料 {i}")
        lines.append(f"  URL: https://example.com/search?q={query.replace(' ', '+')}&p={i}")
        lines.append(f"  摘要: 关于「{query}」的第 {i} 条参考信息。")
    return "\n".join(lines)
```

- [ ] **Step 4: 注册 search_web 到 _BUILTIN_TOOLS**

Modify `agentteam/tools/skills/__init__.py`:

```python
from __future__ import annotations

from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills.file_ops import list_dir, read_file, write_file
from agentteam.tools.skills.search_web import search_web

_BUILTIN_TOOLS = [read_file, write_file, list_dir, search_web]


def register_builtin_skills(registry: ToolRegistry) -> None:
    """把内置原生技能注册到 registry。"""
    for t in _BUILTIN_TOOLS:
        registry.register(t)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/tools/skills/test_search_web.py -v`
Expected: 4 passed

- [ ] **Step 6: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 174 passed (170 原有 + 4 新)

- [ ] **Step 7: Commit**

```bash
git add agentteam/tools/skills/search_web.py agentteam/tools/skills/__init__.py tests/tools/skills/test_search_web.py
git commit -m "feat(tools): search_web stub 技能 + 单元测试"
```

---

## Task 2: 研发小队定义 + 配置验证测试

**Files:**
- Create: `examples/__init__.py`
- Create: `examples/dev_team.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_dev_team_config.py`

- [ ] **Step 1: 创建 examples 包**

Create `examples/__init__.py` (空文件):

```python
```

- [ ] **Step 2: 创建 dev_team.py 团队定义**

Create `examples/dev_team.py`:

```python
"""研发小队示例团队定义。

5 角色:Leader(技术主管) + 需求分析员 + 代码工程师 + 测试员 + Reviewer。
验证 Leader-Worker 编排、ReAct 工具循环、MCP+原生工具混用、声明式审批。

用法:
    from examples.dev_team import DEV_TEAM
    # 或通过 CLI: agentteam register-dev-team
"""
from __future__ import annotations

DEV_TEAM: dict = {
    "name": "dev_team",
    "description": "研发小队 — Leader + 需求分析 + 代码 + 测试 + 审查",
    "leader": {
        "name": "tech_lead",
        "role": "技术主管",
        "system_prompt": "你是技术主管,负责拆解需求、分配步骤、汇总产出。",
        "model": {"provider": "qwen", "name": "qwen-max", "temperature": 0.7, "streaming": True},
        "approval_policy": {"level": "step"},
    },
    "workers": [
        {
            "name": "analyst",
            "role": "需求分析员",
            "description": "拆用户故事、定验收标准",
            "system_prompt": "你是需求分析员,使用 search_web 搜索相关资料,拆解用户故事并定义验收标准。",
            "tools": ["search_web"],
            "max_iterations": 5,
        },
        {
            "name": "coder",
            "role": "代码工程师",
            "description": "写/改代码",
            "system_prompt": "你是代码工程师,使用 read_file/write_file 和 git 工具完成编码任务。写文件前需审批。",
            "tools": ["read_file", "write_file", "mcp:git:git_status", "mcp:git:git_diff", "mcp:git:git_log"],
            "approval_policy": {"level": "tool", "targets": ["write_file"]},
            "max_iterations": 10,
        },
        {
            "name": "tester",
            "role": "测试员",
            "description": "写测试用例",
            "system_prompt": "你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
            "tools": ["read_file", "write_file"],
            "approval_policy": {"level": "tool", "targets": ["write_file"]},
            "max_iterations": 10,
        },
        {
            "name": "reviewer",
            "role": "Reviewer",
            "description": "审查代码与测试",
            "system_prompt": "你是代码审查员,使用 read_file 审查代码与测试质量,给出改进建议。",
            "tools": ["read_file"],
            "max_iterations": 5,
        },
    ],
    "default_model": {"provider": "qwen", "name": "qwen-max", "temperature": 0.7, "streaming": True},
    "skills": ["read_file", "write_file", "list_dir", "search_web"],
    "mcp_servers": [
        {
            "name": "git",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-git", "--repository", "."],
            "transport": "stdio",
        }
    ],
}
```

- [ ] **Step 3: 创建 tests/integration 包 + conftest**

Create `tests/integration/__init__.py` (空文件):

```python
```

Create `tests/integration/conftest.py`:

```python
"""集成测试共享 fixture。"""
from __future__ import annotations

import time

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _wait_for_status(run_repo, run_id, timeout=10.0, target_statuses=None):
    """轮询 run 状态直到非 running/pending 或匹配目标状态。"""
    target = target_statuses or {"completed", "failed", "interrupted"}
    for _ in range(int(timeout * 10)):
        run = run_repo.get_run(run_id)
        if run and run["status"] in target:
            return run["status"]
        time.sleep(0.1)
    return None


@pytest.fixture
def integration_db(tmp_path):
    """集成测试用临时 SQLite 连接。"""
    conn = init_db(tmp_path / "integration.db")
    yield conn
    conn.close()


@pytest.fixture
def run_manager(integration_db):
    """RunManager + RunRepo + AuditRepo + EventBus。"""
    run_repo = RunRepo(integration_db)
    audit_repo = AuditRepo(integration_db)
    bus = EventBus()
    return RunManager(run_repo, audit_repo, bus)


@pytest.fixture
def run_repo(integration_db):
    return RunRepo(integration_db)


def make_dev_team_compiled(
    fake_llm: FakeLLM,
    conn,
    leader_policy: ApprovalPolicy | None = None,
    worker_policy: ApprovalPolicy | None = None,
    mcp_loader=None,
):
    """构建一个简化的 2-worker 研发小队 Team + 编译好的 graph。

    基于 DEV_TEAM 结构,但只用 analyst + coder 两个 worker,
    使 FakeLLM 响应编排可控。可通过参数覆盖审批策略。
    """
    leader = Leader(
        name="tech_lead",
        system_prompt="你是技术主管",
        model=ModelRef("qwen", "qwen-max"),
        approval_policy=leader_policy,
    )
    workers = [
        Worker(
            name="analyst",
            role="需求分析员",
            description="拆用户故事",
            system_prompt="你是需求分析员",
            tools=["search_web"],
            max_iterations=5,
        ),
        Worker(
            name="coder",
            role="代码工程师",
            description="写代码",
            system_prompt="你是代码工程师",
            tools=["read_file", "write_file"],
            approval_policy=worker_policy,
            max_iterations=10,
        ),
    ]
    team = Team(
        name="dev_team_test",
        description="测试用研发小队",
        leader=leader,
        workers=workers,
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file", "list_dir", "search_web"],
    )

    reg = ToolRegistry(mcp_loader=mcp_loader)
    from agentteam.tools.skills import register_builtin_skills
    register_builtin_skills(reg)

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    saver = SqliteSaver(conn)
    saver.setup()
    graph = compiler.compile(team, checkpointer=saver)
    return graph
```

- [ ] **Step 4: 编写配置验证测试**

Create `tests/integration/test_dev_team_config.py`:

```python
"""DEV_TEAM 配置验证测试。"""
from examples.dev_team import DEV_TEAM
from agentteam.api.serializer import team_from_dict
from agentteam.domain.team import Team
from agentteam.domain.worker import Worker
from agentteam.domain.mcp_server import MCPServer


def test_dev_team_parses_to_team_dataclass():
    """DEV_TEAM dict 可被 team_from_dict 解析为 Team dataclass。"""
    team = team_from_dict(DEV_TEAM)
    assert isinstance(team, Team)
    assert team.name == "dev_team"
    assert team.description.startswith("研发小队")


def test_dev_team_has_5_roles():
    """研发小队有 1 leader + 4 workers。"""
    team = team_from_dict(DEV_TEAM)
    assert team.leader.name == "tech_lead"
    assert len(team.workers) == 4
    worker_names = [w.name for w in team.workers]
    assert worker_names == ["analyst", "coder", "tester", "reviewer"]


def test_dev_team_leader_has_step_policy():
    """Leader 有 step 级审批策略。"""
    team = team_from_dict(DEV_TEAM)
    assert team.leader.approval_policy is not None
    assert team.leader.approval_policy.level == "step"


def test_dev_team_coder_has_tool_policy():
    """代码工程师有 tool 级审批策略,target 为 write_file。"""
    team = team_from_dict(DEV_TEAM)
    coder = next(w for w in team.workers if w.name == "coder")
    assert coder.approval_policy is not None
    assert coder.approval_policy.level == "tool"
    assert coder.approval_policy.targets == ["write_file"]


def test_dev_team_skills_include_search_web():
    """skills 列表包含 search_web。"""
    team = team_from_dict(DEV_TEAM)
    assert "search_web" in team.skills


def test_dev_team_mcp_git_config():
    """MCP git server 配置正确。"""
    team = team_from_dict(DEV_TEAM)
    assert len(team.mcp_servers) == 1
    server = team.mcp_servers[0]
    assert isinstance(server, MCPServer)
    assert server.name == "git"
    assert server.command == "npx"
    assert "-y" in server.args
    assert "@modelcontextprotocol/server-git" in server.args
    assert server.transport == "stdio"


def test_dev_team_coder_tools_include_mcp_git():
    """代码工程师的工具列表包含 mcp:git: 前缀工具。"""
    team = team_from_dict(DEV_TEAM)
    coder = next(w for w in team.workers if w.name == "coder")
    mcp_tools = [t for t in coder.tools if t.startswith("mcp:git:")]
    assert len(mcp_tools) == 3
    assert "mcp:git:git_status" in coder.tools
    assert "mcp:git:git_diff" in coder.tools
    assert "mcp:git:git_log" in coder.tools


def test_dev_team_can_be_compiled():
    """DEV_TEAM 可被 TeamCompiler 编译为可执行 graph(不运行)。"""
    from tests.conftest import FakeLLM, FakeModelProvider
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry

    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry()
    compiler = TeamCompiler(provider, reg)
    team = team_from_dict(DEV_TEAM)
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    assert "worker_analyst" in node_names
    assert "worker_coder" in node_names
    assert "worker_tester" in node_names
    assert "worker_reviewer" in node_names
    assert "step_gate" in node_names  # Leader 有 step 级策略
```

- [ ] **Step 5: 运行测试验证通过**

Run: `python -m pytest tests/integration/test_dev_team_config.py -v`
Expected: 8 passed

- [ ] **Step 6: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 182 passed (174 + 8 新)

- [ ] **Step 7: Commit**

```bash
git add examples/ tests/integration/__init__.py tests/integration/conftest.py tests/integration/test_dev_team_config.py
git commit -m "feat(examples): 研发小队 DEV_TEAM 定义 + 配置验证测试"
```

---

## Task 3: CLI 入口 + 测试

**Files:**
- Create: `agentteam/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 编写 CLI 测试**

Create `tests/test_cli.py`:

```python
"""CLI 入口测试。"""
from unittest.mock import patch, MagicMock

from agentteam.cli import main


def test_register_dev_team_success(capsys):
    """register-dev-team 成功注册团队。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"name": "dev_team"})
        main(["register-dev-team", "--api", "http://localhost:8000"])
    captured = capsys.readouterr()
    assert "dev_team" in captured.out
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:8000/api/teams"
    assert kwargs["json"]["name"] == "dev_team"


def test_register_dev_team_default_api(capsys):
    """默认 API 地址为 http://localhost:8000。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"name": "dev_team"})
        main(["register-dev-team"])
    args, _ = mock_post.call_args
    assert args[0] == "http://localhost:8000/api/teams"


def test_register_dev_team_api_error(capsys):
    """API 返回错误时输出错误信息。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=409,
            json=lambda: {"detail": "Team already exists"},
        )
        main(["register-dev-team"])
    captured = capsys.readouterr()
    assert "错误" in captured.out or "Team already exists" in captured.out


def test_register_dev_team_connection_error(capsys):
    """连接失败时输出错误信息。"""
    with patch("agentteam.cli.requests.post", side_effect=ConnectionError("Connection refused")):
        main(["register-dev-team"])
    captured = capsys.readouterr()
    assert "错误" in captured.out
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentteam.cli'`

- [ ] **Step 3: 实现 CLI**

Create `agentteam/cli.py`:

```python
"""AgentTeam CLI 入口。

命令:
    agentteam register-dev-team [--api URL]  注册研发小队到 API 服务
"""
from __future__ import annotations

import argparse
import sys

import requests

from examples.dev_team import DEV_TEAM


def register_dev_team(api: str = "http://localhost:8000") -> int:
    """注册研发小队到指定 API 服务,返回退出码。"""
    try:
        resp = requests.post(f"{api}/api/teams", json=DEV_TEAM, timeout=10)
        if resp.ok:
            data = resp.json()
            print(f"已注册团队: {data.get('name', 'dev_team')}")
            return 0
        else:
            err = resp.json()
            detail = err.get("detail", resp.text)
            print(f"错误: {detail}")
            return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(prog="agentteam", description="AgentTeam CLI")
    sub = parser.add_subparsers(dest="command")

    p_register = sub.add_parser("register-dev-team", help="注册研发小队到 API")
    p_register.add_argument("--api", default="http://localhost:8000", help="API 地址")

    args = parser.parse_args(argv)

    if args.command == "register-dev-team":
        return register_dev_team(args.api)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 添加 requests 依赖检查**

`requests` 已是 Python 常用库,但需要确认在 pyproject.toml 中。检查后若无,添加到 dev 依赖:

Check `pyproject.toml` — if `requests` is not in dependencies, add it to `[project.optional-dependencies] dev`:

```toml
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27", "requests>=2.28"]
```

- [ ] **Step 5: 添加 CLI 入口点到 pyproject.toml**

Modify `pyproject.toml`,在 `[project]` 段后添加:

```toml
[project.scripts]
agentteam = "agentteam.cli:main"
```

- [ ] **Step 6: 运行测试验证通过**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 4 passed

- [ ] **Step 7: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 186 passed (182 + 4 新)

- [ ] **Step 8: Commit**

```bash
git add agentteam/cli.py tests/test_cli.py pyproject.toml
git commit -m "feat(cli): register-dev-team CLI 命令 + 测试"
```

---

## Task 4: E2E 正常完成测试

**Files:**
- Create: `tests/integration/test_e2e_normal.py`

- [ ] **Step 1: 编写 E2E 正常完成测试**

Create `tests/integration/test_e2e_normal.py`:

```python
"""E2E: 研发小队正常完成 run(无审批中断)。

用简化的 2-worker 团队(analyst + coder),mock LLM 按序返回:
Leader plan → analyst 执行 → Leader review → coder 执行 → Leader review → 结束
"""
from langchain_core.messages import AIMessage

from agentteam.runtime.nodes import Plan, PlanStep
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status


def test_e2e_normal_completion(run_manager, run_repo, integration_db):
    """2-worker 研发小队正常完成 run,状态 pending→running→completed。"""
    fake_llm = FakeLLM()

    # Leader: 1 次结构化输出(plan) + 2 次 invoke(review × 2)
    fake_llm.set_structured_responses([
        Plan(steps=[
            PlanStep(worker="analyst", instruction="分析需求"),
            PlanStep(worker="coder", instruction="写代码"),
        ]),
    ])
    fake_llm.set_invoke_responses([
        AIMessage(content="analyst 干得不错"),   # review 1
        AIMessage(content="coder 代码到位,全部完成"),  # review 2
    ])

    # Worker(analyst + coder 共用一个 LLM):各 1 次直接给答案
    # 注意:analyst 和 coder 用同一个 fake_llm,invoke_responses 按序消费
    # Leader 的 structured_responses 和 invoke_responses 是分开的队列
    # Worker 的 invoke 也走 invoke_responses 队列
    # 顺序:leader_plan(structured) → analyst invoke → leader_review invoke → coder invoke → leader_review invoke
    # 但 analyst/coder 的 invoke 和 leader_review 的 invoke 共用同一队列!
    # 所以 invoke_responses 需要按实际调用顺序编排:
    # [0] analyst worker invoke (返回答案)
    # [1] leader review invoke (返回点评)
    # [2] coder worker invoke (返回答案)
    # [3] leader review invoke (返回点评)
    fake_llm.set_invoke_responses([
        AIMessage(content="需求分析完成:用户故事已拆解"),  # analyst
        AIMessage(content="analyst 干得不错"),              # leader review 1
        AIMessage(content="print('hello world')"),         # coder
        AIMessage(content="coder 代码到位,全部完成"),       # leader review 2
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发 hello world 功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发 hello world 功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # 验证 run 记录
    run = run_repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None


def test_e2e_normal_worker_outputs(run_manager, run_repo, integration_db):
    """正常完成后,worker_outputs 包含两个 worker 的产出。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[
            PlanStep(worker="analyst", instruction="分析需求"),
            PlanStep(worker="coder", instruction="写代码"),
        ]),
    ])
    fake_llm.set_invoke_responses([
        AIMessage(content="需求分析结果"),    # analyst
        AIMessage(content="review 1"),        # leader review 1
        AIMessage(content="代码实现"),        # coder
        AIMessage(content="review 2"),        # leader review 2
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    _wait_for_status(run_repo, run_id)

    # 验证 graph 状态
    state = graph.get_state(config)
    values = state.values
    assert "analyst" in values.get("worker_outputs", {})
    assert "coder" in values.get("worker_outputs", {})
    assert values["worker_outputs"]["analyst"] == "需求分析结果"
    assert values["worker_outputs"]["coder"] == "代码实现"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/integration/test_e2e_normal.py -v`
Expected: 2 passed

- [ ] **Step 3: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 188 passed (186 + 2 新)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_e2e_normal.py
git commit -m "test(integration): E2E 正常完成 — 研发小队 2-worker run"
```

---

## Task 5: E2E 审批场景测试

**Files:**
- Create: `tests/integration/test_e2e_approval.py`

- [ ] **Step 1: 编写 step 级审批 E2E 测试**

Create `tests/integration/test_e2e_approval.py`:

```python
"""E2E: 研发小队审批场景。

场景 1: Leader step 级审批 → interrupt → resume → 完成
场景 2: Coder tool 级审批(write_file) → interrupt → resume → 完成
"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.nodes import Plan, PlanStep
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status


def test_e2e_step_approval_interrupt_resume(run_manager, run_repo, integration_db):
    """Leader step 级审批:plan 后 interrupt → resume approved → worker 执行 → 完成。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    fake_llm.set_invoke_responses([
        # leader_review 在 resume 后才调用
        AIMessage(content="analyst 完成得不错"),
    ])

    graph = make_dev_team_compiled(
        fake_llm, integration_db,
        leader_policy=ApprovalPolicy(level="step"),
    )
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    # 第一次 invoke:应在 step_gate 处 interrupt
    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:批准
    run_manager.resume_run(run_id, approved=True, reason="同意")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # 验证 worker 执行了
    state = graph.get_state(config)
    assert "analyst" in state.values.get("worker_outputs", {})


def test_e2e_step_approval_rejected_terminates(run_manager, run_repo, integration_db):
    """Leader step 级审批被拒绝 → run 完成(无 worker 执行)。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    # 拒绝后不执行 worker,所以不需要 invoke_responses

    graph = make_dev_team_compiled(
        fake_llm, integration_db,
        leader_policy=ApprovalPolicy(level="step"),
    )
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:拒绝
    run_manager.resume_run(run_id, approved=False, reason="不通过")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # worker 不应执行
    state = graph.get_state(config)
    assert "analyst" not in state.values.get("worker_outputs", {})


def test_e2e_tool_approval_write_file(run_manager, run_repo, integration_db, tmp_path):
    """Coder 调用 write_file → tool 级审批 interrupt → resume approved → write_file 执行 → 完成。"""
    target = tmp_path / "output.txt"

    # 创建一个测试用 write_file 工具(写入 tmp_path)
    def write_test_file(path: str, content: str) -> str:
        p = tmp_path / path
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"

    write_tool = StructuredTool.from_function(
        name="write_file", description="写文件", func=write_test_file
    )

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="coder", instruction="写文件")]),
    ])
    fake_llm.set_invoke_responses([
        # coder 第 1 轮:调 write_file 工具
        AIMessage(
            content="",
            tool_calls=[{
                "name": "write_file",
                "args": {"path": "output.txt", "content": "hello"},
                "id": "tc1",
                "type": "tool_call",
            }],
        ),
        # coder 第 2 轮:给最终答案
        AIMessage(content="文件已写入"),
        # leader review
        AIMessage(content="coder 完成得不错"),
    ])

    # 构建带 tool 级审批的团队
    from agentteam.domain.team import Leader, Team
    from agentteam.domain.worker import Worker
    from agentteam.models.provider import ModelRef
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills
    from tests.conftest import FakeModelProvider
    from langgraph.checkpoint.sqlite import SqliteSaver

    # 覆盖 write_file 为测试版本
    reg = ToolRegistry()
    register_builtin_skills(reg)
    # 先注销内置 write_file,注册测试版本
    reg._tools.pop("write_file", None)
    reg.register(write_tool)

    team = Team(
        name="dev_team_test",
        description="测试用研发小队",
        leader=Leader(name="tech_lead", system_prompt="你是技术主管"),
        workers=[
            Worker(
                name="coder", role="代码工程师", description="写代码",
                system_prompt="你是代码工程师",
                tools=["write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
                max_iterations=10,
            ),
        ],
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file", "list_dir", "search_web"],
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, reg)
    saver = SqliteSaver(integration_db)
    saver.setup()
    graph = compiler.compile(team, checkpointer=saver)

    run_id = run_repo.create_run("dev_team_test", "写文件任务")
    config = {"configurable": {"thread_id": run_id}}

    # 第一次 invoke:应在 tool 审批处 interrupt
    run_manager.start_run(run_id, graph, config, "写文件任务")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:批准
    run_manager.resume_run(run_id, approved=True, reason="同意写文件")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # 验证文件已写入
    assert target.read_text(encoding="utf-8") == "hello"

    # 验证 worker 产出
    state = graph.get_state(config)
    assert state.values["worker_outputs"]["coder"] == "文件已写入"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/integration/test_e2e_approval.py -v`
Expected: 3 passed

- [ ] **Step 3: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 191 passed (188 + 3 新)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_e2e_approval.py
git commit -m "test(integration): E2E 审批场景 — step 级 + tool 级"
```

---

## Task 6: E2E 错误场景 + README 更新

**Files:**
- Create: `tests/integration/test_e2e_error.py`
- Modify: `README.md`

- [ ] **Step 1: 编写 E2E 错误场景测试**

Create `tests/integration/test_e2e_error.py`:

```python
"""E2E: Worker 执行异常 → run 失败。

FakeLLM 不设 invoke_responses → invoke 时 IndexError → run 状态 failed。
"""
from langchain_core.messages import AIMessage

from agentteam.runtime.nodes import Plan, PlanStep
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status


def test_e2e_worker_error_fails_run(run_manager, run_repo, integration_db):
    """Worker LLM 异常 → run 状态 failed。"""
    fake_llm = FakeLLM()
    # Leader plan 正常返回,但不设 invoke_responses
    # Worker invoke 时 IndexError → 异常 → run failed
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    # 不设 invoke_responses → analyst worker invoke 时 IndexError

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "failed"

    # 验证 run 记录
    run = run_repo.get_run(run_id)
    assert run["status"] == "failed"
    assert run["ended_at"] is not None


def test_e2e_error_events_in_audit(run_manager, run_repo, integration_db):
    """run 失败后,audit 表有 error 事件。"""
    from agentteam.storage.audit import AuditRepo

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    _wait_for_status(run_repo, run_id)

    # 验证 audit 事件
    audit_repo = AuditRepo(integration_db)
    events = audit_repo.list_events(run_id)
    event_types = [e["event_type"] for e in events]
    assert "run_start" in event_types
    assert "error" in event_types
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/integration/test_e2e_error.py -v`
Expected: 2 passed

- [ ] **Step 3: 运行全量测试确保无回归**

Run: `python -m pytest -q`
Expected: 193 passed (191 + 2 新)

- [ ] **Step 4: 更新 README.md**

Modify `README.md`:

1. M6 状态标记 `[x]`:

```markdown
- [x] M6 示例团队 + 测试
```

2. 在「快速示例」章节后,新增「研发小队」章节:

```markdown
## 研发小队示例

内置「研发小队」团队验证框架完整流程:Leader 拆解 → 需求分析 → 编码 → 测试 → 审查。

### 1. 启动 API 服务

```bash
pip install -e ".[qwen,dev]"
uvicorn agentteam.api.server:create_app --factory
```

### 2. 注册研发小队

```bash
# 通过 CLI(需 pip install -e ".[dev]")
agentteam register-dev-team

# 或通过 API
curl -X POST http://localhost:8000/api/teams -H "Content-Type: application/json" -d @examples/dev_team.json
```

### 3. 提交任务

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "dev_team", "task": "实现一个 hello world 程序"}'
```

### 4. 查看实时轨迹

- **Web UI**: 浏览器打开 http://localhost:8000
- **SSE**: `GET http://localhost:8000/api/runs/{run_id}/stream`

### 5. 审批续跑

当 Leader step 级或 Worker tool 级审批触发时,run 状态变为 `interrupted`:

```bash
curl -X POST http://localhost:8000/api/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "reason": "同意"}'
```
```

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_e2e_error.py README.md
git commit -m "test(integration): E2E 错误场景 + README 研发小队文档"
```

---

## 验证清单

- [ ] `search_web` 技能注册到 ToolRegistry,`list_names()` 包含 `search_web`
- [ ] `examples/dev_team.py` 的 `DEV_TEAM` 可被 `team_from_dict` 解析为 `Team` dataclass
- [ ] `DEV_TEAM` 可被 `TeamCompiler` 编译为可执行 graph(含 5 个 worker 节点 + step_gate)
- [ ] `agentteam register-dev-team` CLI 命令成功注册团队(测试 mock)
- [ ] E2E 正常完成:2-worker 团队 run 状态 pending→running→completed
- [ ] E2E step 级审批:interrupt → resume → completed
- [ ] E2E tool 级审批:write_file interrupt → resume → 文件写入 → completed
- [ ] E2E 错误:Worker 异常 → run failed + error 事件
- [ ] 现有 191 个测试不受影响(最终 193 passed)
- [ ] README.md M6 标记 ✅ + 研发小队使用示例
