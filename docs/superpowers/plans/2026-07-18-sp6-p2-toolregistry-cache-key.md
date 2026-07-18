# SP6-P2 ToolRegistry 缓存 key 修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正 `ToolRegistry` 的 MCP server 缓存 key,用配置 tuple `(name, command, args, transport, url)` 替代 `server.name`,使同名但 command/args/transport/url 不同的 server 各自独立触发 loader,避免第二个 server 被错误跳过导致工具漏注册。

**Architecture:** 在 `agentteam/tools/registry.py` 新增模块级 helper `_server_cache_key(server: MCPServer) -> tuple`,把 `ToolRegistry._loaded_servers` 类型注解从 `set[str]` 改为 `set[tuple]`;`register_mcp_tools` 在缓存命中检查与缓存写入两处用 `_server_cache_key(server)` 替代 `server.name`。工具名前缀仍用 `mcp:{server.name}:`(向后兼容,但同名不同配置的 server 注册的同名工具会冲突 — 这是已知限制,文档显式记录)。新增测试文件 `tests/tools/test_registry_cache_key.py`,独立于 `tests/tools/test_mcp_leak.py`,全部用 fake loader(`lambda server: [fake_tool]`),不依赖真实 npx 子进程。loader 抛异常时不入缓存的契约通过新测试显式验证(现有行为已正确,测试做 guard rail)。

**Tech Stack:** Python 3.11+, pytest, langchain_core.tools.StructuredTool, agentteam.domain.mcp_server.MCPServer

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/tools/registry.py` | ToolRegistry 工具注册表 + MCP 缓存 key 修正 | 修改 |
| `tests/tools/test_registry_cache_key.py` | P2 缓存 key 修正测试(独立于 `test_mcp_leak.py`) | 新建 |

---

## Task 1: 新增 `_server_cache_key` helper + 改 `_loaded_servers` 类型为 `set[tuple]`

**Files:**
- Create: `d:\project\agentTeam\tests\tools\test_registry_cache_key.py`
- Modify: `d:\project\agentTeam\agentteam\tools\registry.py` (新增模块级 helper + 改 `__init__` 类型注解)

- [ ] **Step 1: 写失败测试 — `_server_cache_key` 返回正确 tuple**

创建 `d:\project\agentTeam\tests\tools\test_registry_cache_key.py`:

```python
"""SP6-P2 ToolRegistry 缓存 key 修正测试。

独立于 tests/tools/test_mcp_leak.py(BUG-06 回归),本文件专门验证:
- _server_cache_key helper 返回 (name, command, args tuple, transport, url)
- 同名不同 command/args/transport/url 的 server 各自独立缓存
- 同配置二次调用命中缓存
- loader 失败不入缓存(契约 guard rail)

全部用 fake loader,不依赖真实 npx 子进程。
"""
import pytest
from langchain_core.tools import StructuredTool

from agentteam.domain.mcp_server import MCPServer


def _make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        name=name, description=f"tool {name}", func=lambda: name
    )


def test_server_cache_key_returns_tuple():
    """_server_cache_key 返回 (name, command, args tuple, transport, url) tuple。

    P2 核心:用配置 tuple 唯一标识 MCP server,而非 server.name。
    """
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(
        name="git",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "."],
        transport="stdio",
        url=None,
    )
    key = _server_cache_key(server)
    assert isinstance(key, tuple)
    assert key == (
        "git",
        "npx",
        ("-y", "@modelcontextprotocol/server-git", "--repository", "."),
        "stdio",
        None,
    )


def test_server_cache_key_http_server_with_url():
    """http transport 的 server,url 进入 key。"""
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(
        name="remote",
        command="",
        args=[],
        transport="http",
        url="http://localhost:8080/mcp",
    )
    key = _server_cache_key(server)
    assert key == ("remote", "", (), "http", "http://localhost:8080/mcp")


def test_server_cache_key_default_args_empty_tuple():
    """未传 args(默认空 list)时,key 中 args 部分为空 tuple。"""
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(name="srv", command="python")
    key = _server_cache_key(server)
    # 默认 args=[],转 tuple 后是 ()
    assert key == ("srv", "python", (), "stdio", None)


def test_server_cache_key_same_config_returns_equal_tuple():
    """两个独立构造但配置相同的 MCPServer,cache key 应相等(可命中缓存)。"""
    from agentteam.tools.registry import _server_cache_key

    server_a = MCPServer(name="git", command="npx", args=["-y", "server-git"])
    server_b = MCPServer(name="git", command="npx", args=["-y", "server-git"])
    assert _server_cache_key(server_a) == _server_cache_key(server_b)
    # hash 也应相等(可加入 set)
    assert hash(_server_cache_key(server_a)) == hash(_server_cache_key(server_b))


def test_server_cache_key_different_args_not_equal():
    """同名同 command 但 args 不同,cache key 应不同(各自独立缓存)。"""
    from agentteam.tools.registry import _server_cache_key

    server_a = MCPServer(name="git", command="npx", args=["-y", "server-git-a"])
    server_b = MCPServer(name="git", command="npx", args=["-y", "server-git-b"])
    assert _server_cache_key(server_a) != _server_cache_key(server_b)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/tools/test_registry_cache_key.py -v`
Expected: 5 个测试 FAIL,错误为 `ImportError: cannot import name '_server_cache_key' from 'agentteam.tools.registry'`

- [ ] **Step 3: 最小实现 — 新增 helper + 改类型注解**

修改 `d:\project\agentTeam\agentteam\tools\registry.py`。

在文件顶部 import 区之后(第 5 行 `from agentteam.domain.mcp_server import MCPServer` 之后、`class ToolRegistry:` 之前),新增模块级 helper:

```python
def _server_cache_key(server: MCPServer) -> tuple:
    """生成 MCP server 的缓存 key。

    用 (name, command, args, transport, url) 唯一标识一个 MCP server 配置。
    同名但配置不同(command/args/transport/url 任一不同)的 server 视为
    不同实例,应各自独立触发 loader 调用,避免第二个 server 被错误跳过
    导致工具漏注册(SP6-P2 / BUG-12 修复)。

    Args:
        server: MCP server 配置 dataclass。

    Returns:
        可哈希的 tuple,可作为 set 元素。args list 转 tuple 以保证可哈希。
    """
    return (
        server.name,
        server.command,
        tuple(server.args),
        server.transport,
        server.url,
    )
```

修改 `ToolRegistry.__init__` 中 `_loaded_servers` 的类型注解与注释(原 `set[str]` 改为 `set[tuple]`):

```python
        # BUG-06:缓存已成功加载的 MCP server 配置,避免 loader 重复调用。
        # default_mcp_loader 会 spawn npx 子进程,而 TeamCompiler.compile()
        # 每次 create_run 都会触发 register_mcp_tools,无缓存时 n×m 次 run
        # 会泄漏 n×m 个子进程。loader 调用成功后才加入此集合;失败不加入
        # (允许重试)。
        # SP6-P2 / BUG-12 修正:用 (name, command, args, transport, url) tuple
        # 作 key,而非 server.name。同名但配置不同的 server 应各自独立缓存,
        # 否则第二个同名 server 会被错误跳过 loader 调用,工具不注册。
        # 注意:工具名前缀仍用 mcp:{server.name}:,因此同名不同配置的 server
        # 注册的同名工具会冲突(第二个被跳过),这是已知限制 — 用户若需多实例
        # 应改 server.name。
        self._loaded_servers: set[tuple] = set()
```

注:本 step **不修改** `register_mcp_tools` 方法(仍用 `server.name`),留待 Task 2 修改。本 step 后,`_loaded_servers` 注解为 `set[tuple]` 但运行时仍存 `str`(Python 不强制运行时类型),现有测试(`test_mcp_leak.py` / `test_registry.py`)继续通过。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/tools/test_registry_cache_key.py -v`
Expected: 5 PASS

Run: `python -m pytest tests/tools/test_mcp_leak.py tests/tools/test_registry.py -v`
Expected: 全部 PASS(无回归 — 现有测试不直接断言 `_loaded_servers` 内容)

- [ ] **Step 5: 提交**

```powershell
git add agentteam/tools/registry.py tests/tools/test_registry_cache_key.py
git commit -m "feat(tools): 新增 _server_cache_key helper,缓存类型改 set[tuple]"
```

---

## Task 2: `register_mcp_tools` 用 `_server_cache_key` 替代 `server.name`

**Files:**
- Modify: `d:\project\agentTeam\agentteam\tools\registry.py` (`register_mcp_tools` 方法两处 cache 操作)
- Modify: `d:\project\agentTeam\tests\tools\test_registry_cache_key.py` (追加两个测试)

- [ ] **Step 1: 写失败测试 — 同名不同 command 都触发 loader + 同配置命中缓存**

在 `d:\project\agentTeam\tests\tools\test_registry_cache_key.py` 末尾追加:

```python
def test_same_name_different_command_both_loaded():
    """同名但 args 不同的两个 MCPServer,loader 应各被调用 1 次。

    P2 修复核心:原实现用 server.name 作 cache key,两个同名 server 中
    第二个被错误跳过 loader,工具不注册。修复后用配置 tuple 作 key,
    两个 server 各自独立缓存,loader 各调用 1 次(共 2 次)。
    """
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        # 每次返回不同名 tool,避免工具名冲突(前缀都是 mcp:git:)
        return [_make_tool(f"tool_{call_count['n']}")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    # 两个同名但 args 不同(模拟 git server 指向不同仓库)
    server_a = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_a"],
    )
    server_b = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_b"],
    )

    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert call_count["n"] == 2, (
        "同名不同 args 的 server 应各自触发 loader,实际触发 "
        f"{call_count['n']} 次"
    )
    # 两个 server 的工具都注册了(因 tool 名不同,无冲突)
    assert "mcp:git:tool_1" in reg.list_names()
    assert "mcp:git:tool_2" in reg.list_names()


def test_same_name_different_command_same_tool_name_second_skipped():
    """同名不同配置的 server 注册同名工具时,第二个工具被跳过(已知限制)。

    工具名前缀仍是 mcp:{server.name}:,因此同名不同配置的 server 注册
    的同名工具会冲突。这是预期行为 — 用户若需多实例应改 server.name。
    本测试显式记录此契约,避免后续修改时意外破坏。
    """
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        # 两次都返回同名 tool,触发工具名冲突
        return [_make_tool("git_status")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    server_a = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_a"],
    )
    server_b = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_b"],
    )

    registered_a = reg.register_mcp_tools(server_a)
    registered_b = reg.register_mcp_tools(server_b)

    # loader 仍各调用 1 次(cache key 是配置 tuple,不同)
    assert call_count["n"] == 2
    # 但工具名都注册为 mcp:git:git_status,第二个被二级防护跳过
    assert registered_a == ["mcp:git:git_status"]
    assert registered_b == ["mcp:git:git_status"]  # 命中已注册,跳过覆盖
    # registry 中只有 1 个 tool(同名冲突)
    assert reg.list_names() == ["mcp:git:git_status"]


def test_same_config_second_call_uses_cache():
    """配置完全相同的 MCPServer 二次调用,loader 只触发 1 次(命中缓存)。"""
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        return [_make_tool("fetch"), _make_tool("search")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    server = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git"],
    )

    # 第一次:loader 被调用,工具注册
    registered1 = reg.register_mcp_tools(server)
    assert set(registered1) == {"mcp:git:fetch", "mcp:git:search"}
    assert call_count["n"] == 1

    # 第二次:配置相同 → cache key 相同 → 命中缓存,loader 不再调用
    registered2 = reg.register_mcp_tools(server)
    assert call_count["n"] == 1, "相同配置二次调用应命中缓存,loader 不再调用"
    # 已注册工具名仍返回(保持幂等语义)
    assert set(registered2) == {"mcp:git:fetch", "mcp:git:search"}


def test_different_name_different_key():
    """不同 name 的 server 独立缓存(P2 修复后仍成立,回归 guard)。"""
    from agentteam.tools.registry import ToolRegistry

    calls = []

    def tracking_loader(server):
        calls.append(server.name)
        return [_make_tool(f"tool_{server.name}")]

    reg = ToolRegistry(mcp_loader=tracking_loader)
    server_a = MCPServer(name="server_a", command="python")
    server_b = MCPServer(name="server_b", command="python")

    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert calls == ["server_a", "server_b"]

    # 再次调用:都应命中各自缓存,不新增 loader 调用
    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert calls == ["server_a", "server_b"], "缓存命中后不应再调用 loader"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/tools/test_registry_cache_key.py -v`
Expected:
- `test_server_cache_key_*` (5 个,Task 1 已实现):PASS
- `test_same_name_different_command_both_loaded`:FAIL(loader 只被调用 1 次,因 `register_mcp_tools` 仍用 `server.name` 作 cache key,第二个 server 命中缓存被跳过)
- `test_same_name_different_command_same_tool_name_second_skipped`:FAIL(同理,loader 只调用 1 次)
- `test_same_config_second_call_uses_cache`:PASS(原实现已支持)
- `test_different_name_different_key`:PASS(原实现已支持)

- [ ] **Step 3: 修改 `register_mcp_tools` — 用 `_server_cache_key` 替代 `server.name`**

修改 `d:\project\agentTeam\agentteam\tools\registry.py` 的 `register_mcp_tools` 方法两处:

原代码(缓存命中检查):
```python
        # BUG-06:已加载的 server 直接跳过 loader 调用,避免重复 spawn 子进程。
        # 返回已注册的工具名(保持幂等语义,调用方仍能拿到工具列表)。
        if server.name in self._loaded_servers:
            prefix = f"mcp:{server.name}:"
            return [name for name in self._tools if name.startswith(prefix)]
```

改为:
```python
        # BUG-06 / SP6-P2:已加载的 server 直接跳过 loader 调用,避免重复 spawn
        # 子进程。cache key 用 (name, command, args, transport, url) tuple,
        # 同名不同配置的 server 各自独立缓存(BUG-12 修复)。
        # 返回已注册的工具名(保持幂等语义,调用方仍能拿到工具列表)。
        key = _server_cache_key(server)
        if key in self._loaded_servers:
            prefix = f"mcp:{server.name}:"
            return [name for name in self._tools if name.startswith(prefix)]
```

原代码(缓存写入):
```python
        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        # 仅在 loader 调用成功(未抛异常)后才缓存,失败时不加入 → 允许重试
        self._loaded_servers.add(server.name)
```

改为:
```python
        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        # 仅在 loader 调用成功(未抛异常)后才缓存,失败时不加入 → 允许重试
        # SP6-P2:cache key 用配置 tuple,而非 server.name
        self._loaded_servers.add(key)
```

注:工具名前缀逻辑保持不变(`f"mcp:{server.name}:{tool.name}"`),向后兼容现有调用方。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/tools/test_registry_cache_key.py -v`
Expected: 全部 PASS(5 + 4 = 9 个测试)

Run: `python -m pytest tests/tools/test_mcp_leak.py tests/tools/test_registry.py -v`
Expected: 全部 PASS(无回归 — 现有测试用不同 name / 同 name 同配置场景,均符合新 cache 语义)

- [ ] **Step 5: 提交**

```powershell
git add agentteam/tools/registry.py tests/tools/test_registry_cache_key.py
git commit -m "fix(tools): register_mcp_tools 用配置 tuple 替代 server.name 作 cache key"
```

---

## Task 3: loader 失败不入缓存(契约 guard rail 测试)

**Files:**
- Modify: `d:\project\agentTeam\tests\tools\test_registry_cache_key.py` (追加 1 个测试)

注:本 task 不修改 source — 实现已由 Task 2 完成(`self._loaded_servers.add(key)` 在 `loader(server)` 之后,异常时不会执行)。本 task 仅写测试显式验证此契约,作为 guard rail 防止后续重构破坏。

- [ ] **Step 1: 写测试 — loader 失败不入缓存,允许重试**

在 `d:\project\agentTeam\tests\tools\test_registry_cache_key.py` 末尾追加:

```python
def test_loader_failure_not_cached():
    """loader 抛异常时,cache key 不入缓存,允许重试。

    契约:register_mcp_tools 中 self._loaded_servers.add(key) 必须在
    loader(server) 成功返回之后执行。若 loader 抛异常,add 不应执行,
    下次调用同一 server 时应再次触发 loader。

    本测试为 guard rail:确保后续重构不会把 add 提前到 loader 调用前,
    或在异常路径也加入缓存。现有实现已正确(Task 2 修改后保持原顺序)。
    """
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def failing_then_success_loader(server):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("load failed")
        return [_make_tool("fetch")]

    reg = ToolRegistry(mcp_loader=failing_then_success_loader)
    server = MCPServer(name="srv", command="python")

    # 第一次:loader 抛异常
    with pytest.raises(RuntimeError, match="load failed"):
        reg.register_mcp_tools(server)

    # 第二次:loader 应再次被调用(失败未缓存)
    registered = reg.register_mcp_tools(server)
    assert call_count["n"] == 2, "失败后应允许重试,loader 应被再次调用"
    assert "mcp:srv:fetch" in registered


def test_loader_failure_then_retry_then_second_failure_still_not_cached():
    """连续两次失败,cache 始终不写入,第三次仍能触发 loader。"""
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def always_failing_loader(server):
        call_count["n"] += 1
        raise RuntimeError(f"fail #{call_count['n']}")

    reg = ToolRegistry(mcp_loader=always_failing_loader)
    server = MCPServer(name="srv", command="python")

    # 三次连续失败,每次都应触发 loader
    for i in range(3):
        with pytest.raises(RuntimeError, match=r"fail #\d"):
            reg.register_mcp_tools(server)
    assert call_count["n"] == 3, "每次失败都不应缓存,loader 应每次被调用"
```

- [ ] **Step 2: 运行测试验证通过**

Run: `python -m pytest tests/tools/test_registry_cache_key.py -v`
Expected: 全部 PASS(9 + 2 = 11 个测试 — 实现已由 Task 2 完成,本 task 仅验证契约)

- [ ] **Step 3: 提交**

```powershell
git add tests/tools/test_registry_cache_key.py
git commit -m "test(tools): 验证 loader 失败不入缓存的契约 guard rail"
```

---

## Task 4: 全量回归 + 更新现有测试断言(若有)

**Files:**
- Modify (条件性): `d:\project\agentTeam\tests\tools\test_mcp_leak.py` (仅当 Step 2 发现依赖 `server.name` 作 cache key 的断言时)
- 无 source 修改

- [ ] **Step 1: 运行现有 MCP 缓存测试**

Run: `python -m pytest tests/tools/test_mcp_leak.py -v`
Expected: 3 个测试全部 PASS
- `test_register_mcp_tools_caches_server`:验证同一 server 二次调用 loader 只触发 1 次(配置 tuple 相同 → 命中缓存)
- `test_register_mcp_tools_different_servers`:验证不同 name 的 server 各自缓存(name 不同 → tuple 不同)
- `test_register_mcp_tools_loader_failure_not_cached`:验证 loader 失败不入缓存

记录实际输出,确认无 FAIL。

- [ ] **Step 2: 检查 `test_mcp_leak.py` 是否有依赖 `server.name` 作 cache key 的直接断言**

Run: `python -c "import re; src=open('tests/tools/test_mcp_leak.py', encoding='utf-8').read(); print('\n'.join(l for l in src.splitlines() if '_loaded_servers' in l or 'cache_key' in l))"`
Expected: 无输出(说明 `test_mcp_leak.py` 不直接断言 `_loaded_servers` 内部状态,仅通过 loader 调用计数与工具名间接验证)

人工检查 `test_mcp_leak.py` 的 3 个测试断言:
- `test_register_mcp_tools_caches_server`:用 `MCPServer(name="remote", command="python")` 二次调用,断言 `call_count["n"] == 1`。P2 修改后,配置 tuple `("remote", "python", (), "stdio", None)` 二次相同 → 命中缓存 → loader 只调用 1 次。**断言无需修改**。
- `test_register_mcp_tools_different_servers`:用 `server_a = MCPServer(name="server_a", ...)` / `server_b = MCPServer(name="server_b", ...)`,断言 `calls == ["server_a", "server_b"]`(loader 调用顺序)。P2 修改后,name 不同 → tuple 不同 → 各自缓存。**断言无需修改**。
- `test_register_mcp_tools_loader_failure_not_cached`:用 `MCPServer(name="srv", command="python")`,loader 第一次抛异常,断言第二次调用 `call_count["n"] == 2`。P2 修改后,异常路径仍未执行 `add(key)` → 第二次仍触发 loader。**断言无需修改**。

结论:**无现有测试断言依赖 `server.name` 作 cache key 的内部状态,无需更新**。若 Step 1 全 PASS,跳过 Step 3;若发现 FAIL,在此 step 修复并继续。

- [ ] **Step 3: 运行 `test_registry.py` + 新增 `test_registry_cache_key.py` 联合验证**

Run: `python -m pytest tests/tools/ -v`
Expected: 全部 PASS
- `tests/tools/test_registry.py`:9 个测试(原 ToolRegistry 基础测试 + idempotent 测试)
- `tests/tools/test_mcp_leak.py`:3 个测试(BUG-06 回归)
- `tests/tools/test_registry_cache_key.py`:11 个测试(SP6-P2 新增)
- `tests/tools/skills/`:原有 skills 测试

- [ ] **Step 4: 全量回归**

Run: `python -m pytest -q`
Expected: 全部 PASS(原 418 + SP6-P2 新增 11 = 429+,无回归)

若全量回归 FAIL,定位失败测试:
- 若失败与 P2 修改相关(cache key 行为变化):回到 Task 2 检查实现
- 若失败与 P2 无关(其他模块):记录为已知问题,不阻塞 P2 验收

- [ ] **Step 5: Phase commit(仅在 Step 2 / Step 4 发现需修复时)**

若 Step 1-4 全部 PASS 且无任何 source/test 修改,跳过本 step(工作树已在 Task 1-3 提交后保持 clean)。

若 Step 2 修复了 `test_mcp_leak.py` 或其他文件,执行:

```powershell
git add tests/tools/test_mcp_leak.py
git commit -m "fix(tools): P2 ToolRegistry 缓存 key 用配置 tuple 替代 server.name"
```

- [ ] **Step 6: 验证工作树状态**

Run: `git status`
Expected: clean working tree(所有修改已在 Task 1-3 提交)

Run: `git log --oneline -5`
Expected: 看到 SP6-P2 的 3 个提交:
- `test(tools): 验证 loader 失败不入缓存的契约 guard rail` (Task 3)
- `fix(tools): register_mcp_tools 用配置 tuple 替代 server.name 作 cache key` (Task 2)
- `feat(tools): 新增 _server_cache_key helper,缓存类型改 set[tuple]` (Task 1)

---

## Self-Review

**1. Spec coverage(SP6 roadmap §4 P2 测试覆盖):**
- ✅ `test_same_name_different_command_both_loaded` — Task 2 Step 1
- ✅ `test_same_config_second_call_uses_cache` — Task 2 Step 1
- ✅ `test_loader_failure_not_cached` — Task 3 Step 1
- ✅ `test_different_name_different_key` — Task 2 Step 1
- ✅ `_server_cache_key` helper 实现 — Task 1 Step 3
- ✅ `ToolRegistry.__init__` 类型改 `set[tuple]` — Task 1 Step 3
- ✅ `register_mcp_tools` 两处 cache 操作改用 tuple key — Task 2 Step 3
- ✅ 工具名前缀仍用 `mcp:{server.name}:`(向后兼容)— Task 2 Step 3 注释显式说明
- ✅ 同名不同配置工具名冲突的已知限制 — Task 2 Step 1 `test_same_name_different_command_same_tool_name_second_skipped` 显式记录
- ✅ 全量回归 + 现有测试无回归 — Task 4
- ✅ 测试独立于 `test_mcp_leak.py` — Task 1 创建 `test_registry_cache_key.py`
- ✅ 全部用 fake loader — Task 1-3 测试均用 `lambda server: [fake_tool]` 或等价 fake

**2. Placeholder scan:**
- 无 "TBD" / "TODO" / "implement later"
- 每个 step 都有完整代码或具体命令
- Task 4 Step 2 的"条件性修复"显式给出判断标准(全 PASS 则跳过,FAIL 则修复),非 placeholder
- 所有测试代码完整可运行(含 import / fixture / assertion)
- commit 消息全部为中文 conventional commits 格式

**3. Type consistency:**
- `_server_cache_key(server: MCPServer) -> tuple` — Task 1 定义,Task 2 使用,签名一致
- `self._loaded_servers: set[tuple]` — Task 1 定义,Task 2 通过 `key = _server_cache_key(server)` + `if key in self._loaded_servers` + `self._loaded_servers.add(key)` 使用,类型一致
- `MCPServer` 字段:`name: str`, `command: str`, `args: list[str]`, `transport: Literal["stdio", "http"]`, `url: str | None` — 与 `agentteam/domain/mcp_server.py` 一致
- tuple 元素顺序 `(name, command, tuple(args), transport, url)` — spec §4.2、Task 1 helper、Task 1 测试断言三处一致
- 工具名前缀 `mcp:{server.name}:` — 修改前后一致(不破坏向后兼容)
- commit 消息格式:`<type>(tools): <description>` — 全部使用 `feat` / `fix` / `test` 三种 type

**4. 风险与缓解:**
- **风险**:Task 1 完成后但 Task 2 未完成时,`_loaded_servers` 注解为 `set[tuple]` 但运行时存 `str`(因 `register_mcp_tools` 仍用 `server.name`)。**缓解**:Python 运行时不强制类型注解,现有测试不直接断言 `_loaded_servers` 内容,Task 1 Step 4 显式运行 `test_mcp_leak.py` 验证无回归。Task 2 完成后类型一致。
- **风险**:同名不同配置的 server 注册的同名工具会冲突(第二个被跳过)。**缓解**:Task 2 Step 1 `test_same_name_different_command_same_tool_name_second_skipped` 显式记录此契约,代码注释说明用户若需多实例应改 `server.name`。
- **风险**:Task 4 Step 2 的"条件性修复"可能被误解为 placeholder。**缓解**:显式列出 3 个测试的断言分析与 P2 修改后的行为对照,结论为"无需修改",Step 5 的 commit 仅在发现需修复时执行。

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp6-p2-toolregistry-cache-key.md`。两种执行方式:

1. **Subagent-Driven(推荐)** — 每个 task 派发新 subagent,task 间 review
2. **Inline Execution** — 在当前会话执行,批量 checkpoint review

选择哪种?
