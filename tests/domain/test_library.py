"""AgentLibrary 专家库单元测试。"""
import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelRef


def test_register_and_get():
    lib = AgentLibrary()
    a = Agent(name="coder", role="worker", system_prompt="code")
    lib.register(a)
    assert lib.get("coder") is a
    assert lib.get("nonexistent") is None


def test_register_duplicate_raises():
    lib = AgentLibrary()
    lib.register(Agent(name="coder", role="worker"))
    with pytest.raises(ValueError, match="already in library"):
        lib.register(Agent(name="coder", role="worker"))


def test_resolve_no_ref_returns_copy_with_resolved_children():
    """无 ref 时返回等价 Agent，children 递归 resolve。"""
    lib = AgentLibrary()
    a = Agent(
        name="lead", role="supervisor",
        children=[Agent(name="w", role="worker")],
    )
    resolved = lib.resolve(a)
    assert resolved.name == "lead"
    assert resolved.role == "supervisor"
    assert resolved.ref is None
    assert len(resolved.children) == 1
    assert resolved.children[0].name == "w"
    # 深拷贝验证：修改 resolved 不影响原对象
    resolved.children[0].name = "changed"
    assert a.children[0].name == "w"


def test_resolve_with_ref_deep_copies_template():
    """ref 指向库时深拷贝库定义。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt",
        tools=["read_file"], max_iterations=10,
    ))
    a = Agent(name="eng", role="worker", ref="library:code_engineer")
    resolved = lib.resolve(a)
    assert resolved.name == "eng"  # name 用调用处的
    assert resolved.system_prompt == "template prompt"
    assert resolved.tools == ["read_file"]
    assert resolved.max_iterations == 10
    assert resolved.ref is None


def test_resolve_with_ref_overrides_non_empty_fields():
    """调用处非空字段覆盖模板。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt",
        tools=["read_file"], max_iterations=10,
    ))
    m = ModelRef("qwen", "qwen-max")
    ap = ApprovalPolicy(level="tool", targets=["write_file"])
    a = Agent(
        name="eng", role="worker", ref="library:code_engineer",
        system_prompt="override prompt",
        model=m, tools=["write_file"], max_iterations=5,
        approval_policy=ap,
    )
    resolved = lib.resolve(a)
    assert resolved.system_prompt == "override prompt"
    assert resolved.model is m
    assert resolved.tools == ["write_file"]
    assert resolved.max_iterations == 5
    assert resolved.approval_policy is ap


def test_resolve_with_ref_overrides_children():
    """调用处 children 覆盖模板 children。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="team_lead", role="supervisor",
        system_prompt="template lead",
        children=[Agent(name="template_child", role="worker")],
    ))
    override_child = Agent(name="override_child", role="worker")
    a = Agent(
        name="lead", role="supervisor", ref="library:team_lead",
        children=[override_child],
    )
    resolved = lib.resolve(a)
    assert len(resolved.children) == 1
    assert resolved.children[0].name == "override_child"


def test_resolve_recursive_children():
    """库中 Agent 的 children 若也带 ref，递归 resolve。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="junior_eng", role="worker",
        system_prompt="junior template",
    ))
    lib.register(Agent(
        name="senior_eng", role="supervisor",
        system_prompt="senior template",
        children=[Agent(name="j", role="worker", ref="library:junior_eng")],
    ))
    a = Agent(name="s", role="supervisor", ref="library:senior_eng")
    resolved = lib.resolve(a)
    assert resolved.name == "s"
    assert resolved.role == "supervisor"
    assert len(resolved.children) == 1
    child = resolved.children[0]
    assert child.name == "j"  # name 用调用处（模板中是 "j"）
    assert child.system_prompt == "junior template"
    assert child.ref is None  # 递归解析后 ref 置空


def test_resolve_invalid_scheme_raises():
    lib = AgentLibrary()
    a = Agent(name="x", role="worker", ref="http://example.com/agent")
    with pytest.raises(ValueError, match="Unsupported ref scheme"):
        lib.resolve(a)


def test_resolve_unknown_library_agent_raises():
    lib = AgentLibrary()
    a = Agent(name="x", role="worker", ref="library:nonexistent")
    with pytest.raises(KeyError, match="not found in library"):
        lib.resolve(a)


def test_resolve_circular_ref_raises():
    """直接循环引用 A→A 抛 ValueError，错误信息含 "Circular"。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="A", role="supervisor",
        system_prompt="template A",
        children=[Agent(name="a_child", role="worker", ref="library:A")],
    ))
    a = Agent(name="caller", role="supervisor", ref="library:A")
    with pytest.raises(ValueError, match="Circular"):
        lib.resolve(a)


def test_resolve_circular_ref_indirect():
    """间接循环引用 A→B→A 抛 ValueError，错误信息展示链路。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="A", role="supervisor",
        system_prompt="template A",
        children=[Agent(name="b_ref", role="worker", ref="library:B")],
    ))
    lib.register(Agent(
        name="B", role="supervisor",
        system_prompt="template B",
        children=[Agent(name="a_ref", role="worker", ref="library:A")],
    ))
    a = Agent(name="caller", role="supervisor", ref="library:A")
    with pytest.raises(ValueError, match="Circular"):
        lib.resolve(a)
    # 进一步验证错误信息包含完整链路
    try:
        lib.resolve(a)
    except ValueError as e:
        assert "A" in str(e)
        assert "B" in str(e)


def test_resolve_role_override_from_caller():
    """调用处 role 始终覆盖模板 role（与 name 平行）。"""
    lib = AgentLibrary()
    lib.register(Agent(
        name="worker_tmpl", role="worker",
        system_prompt="worker template",
    ))
    # 调用处用 supervisor 实例化 worker 模板
    a = Agent(name="lead", role="supervisor", ref="library:worker_tmpl")
    resolved = lib.resolve(a)
    assert resolved.role == "supervisor"  # 调用处 role 覆盖模板 worker
    assert resolved.name == "lead"
    assert resolved.system_prompt == "worker template"  # 其他字段仍来自模板


def test_resolve_no_ref_passes_visited_through_children():
    """无 ref 节点的 children 也能触发循环检测（visited 透传给无 ref 子树）。

    构造链路：caller -> A -> mid(无 ref) -> B -> A
    验证 visited 在无 ref 的 mid 节点处原样透传，最终在 B 的 child 处检出环。
    """
    lib = AgentLibrary()
    lib.register(Agent(
        name="A", role="supervisor",
        system_prompt="template A",
        children=[Agent(
            name="mid", role="supervisor",  # 无 ref 的中间节点
            children=[Agent(name="b_ref", role="worker", ref="library:B")],
        )],
    ))
    lib.register(Agent(
        name="B", role="supervisor",
        system_prompt="template B",
        children=[Agent(name="a_ref", role="worker", ref="library:A")],
    ))
    a = Agent(name="caller", role="supervisor", ref="library:A")
    with pytest.raises(ValueError, match="Circular"):
        lib.resolve(a)


def test_resolve_sibling_refs_to_same_library_no_false_positive():
    """兄弟节点引用同一库 Agent 不应误报循环引用。

    回归守护：保证 _visited 使用 "_visited + [lib_name]" 新建列表（不可变），
    而非 _visited.append(lib_name) 共享可变列表。若未来误改为后者，
    此测试会失败（兄弟 c2 解析时 c1 的 lib_name 仍留在 visited 中）。
    """
    lib = AgentLibrary()
    lib.register(Agent(
        name="worker_tmpl", role="worker",
        system_prompt="shared template",
    ))
    parent = Agent(
        name="lead", role="supervisor",
        children=[
            Agent(name="w1", role="worker", ref="library:worker_tmpl"),
            Agent(name="w2", role="worker", ref="library:worker_tmpl"),
        ],
    )
    resolved = lib.resolve(parent)
    assert len(resolved.children) == 2
    assert resolved.children[0].name == "w1"
    assert resolved.children[1].name == "w2"
    # 两个兄弟都应正确复用模板内容
    assert resolved.children[0].system_prompt == "shared template"
    assert resolved.children[1].system_prompt == "shared template"
    assert resolved.children[0].ref is None
    assert resolved.children[1].ref is None


def test_resolve_mcp_servers_from_template():
    """ref 模式：调用方未传 mcp_servers，保留模板的 mcp_servers。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    ))
    caller = Agent(name="eng", role="worker", ref="library:code_engineer")
    resolved = lib.resolve(caller)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "git"


def test_resolve_mcp_servers_override_from_caller():
    """ref 模式：调用方传了 mcp_servers，覆盖模板的。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    ))
    caller = Agent(
        name="eng", role="worker", ref="library:code_engineer",
        mcp_servers=[MCPServer(name="custom", command="custom-mcp")],
    )
    resolved = lib.resolve(caller)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "custom"


def test_resolve_no_ref_preserves_mcp_servers():
    """无 ref 模式：mcp_servers 原样传递。"""
    from agentteam.domain.mcp_server import MCPServer
    lib = AgentLibrary()
    a = Agent(
        name="w", role="worker",
        mcp_servers=[MCPServer(name="git", command="git-mcp")],
    )
    resolved = lib.resolve(a)
    assert len(resolved.mcp_servers) == 1
    assert resolved.mcp_servers[0].name == "git"
