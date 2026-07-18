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
