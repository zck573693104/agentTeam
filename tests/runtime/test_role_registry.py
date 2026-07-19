"""P3-2 节点工厂注册表测试。

验证:
- RoleRegistry 默认注册了 worker / supervisor 两个 role
- RoleRegistry.register / unregister / get / roles API 行为正确
- _validate 对未知 role 抛 "Unknown role" 错误
- _compile_agent 通过 spec.compile_fn 分派(替代硬编码 if/else)
- _compile_supervisor 通过 spec.is_subgraph 决定节点名前缀(worker_ vs agent_)
- 第三方注册新 role 后可正常编译(无需修改 TeamCompiler 源码)
"""
import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import (
    RoleRegistry,
    RoleSpec,
    TeamCompiler,
)
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeModelProvider


# ---- 默认注册表自检 ----

def test_default_roles_registered():
    """模块加载时默认注册 worker / supervisor。"""
    roles = RoleRegistry.roles()
    assert "worker" in roles
    assert "supervisor" in roles


def test_get_returns_spec_for_known_role():
    """get() 返回 RoleSpec 实例。"""
    worker_spec = RoleRegistry.get("worker")
    supervisor_spec = RoleRegistry.get("supervisor")
    assert worker_spec is not None
    assert supervisor_spec is not None
    assert isinstance(worker_spec, RoleSpec)
    assert isinstance(supervisor_spec, RoleSpec)


def test_get_returns_none_for_unknown_role():
    """get() 对未知 role 返回 None(不抛异常)。"""
    assert RoleRegistry.get("nonexistent_role_xyz") is None


def test_default_worker_spec_is_subgraph_true():
    """worker spec.is_subgraph=True(子图,直接 add_node)。"""
    assert RoleRegistry.get("worker").is_subgraph is True


def test_default_supervisor_spec_is_subgraph_false():
    """supervisor spec.is_subgraph=False(需 make_supervisor_node 包装)。"""
    assert RoleRegistry.get("supervisor").is_subgraph is False


# ---- API:register / unregister ----

def test_register_adds_new_role():
    """register 新 role 后 get() 能查到。"""
    spec = RoleSpec(
        compile_fn=lambda *a, **kw: None,
        validate_fn=lambda *a, **kw: None,
        is_subgraph=True,
    )
    RoleRegistry.register("reviewer_test", spec)
    try:
        assert RoleRegistry.get("reviewer_test") is spec
        assert "reviewer_test" in RoleRegistry.roles()
    finally:
        RoleRegistry.unregister("reviewer_test")


def test_register_overrides_existing_role():
    """重复 register 同名 role 覆盖旧 spec(便于测试 monkeypatch 恢复)。"""
    original = RoleRegistry.get("worker")
    new_spec = RoleSpec(
        compile_fn=lambda *a, **kw: None,
        validate_fn=lambda *a, **kw: None,
        is_subgraph=False,
    )
    RoleRegistry.register("worker", new_spec)
    try:
        assert RoleRegistry.get("worker") is new_spec
    finally:
        # 恢复默认 worker spec
        RoleRegistry.register("worker", original)
    assert RoleRegistry.get("worker") is original


def test_unregister_removes_role():
    """unregister 后 get() 返回 None。"""
    spec = RoleSpec(
        compile_fn=lambda *a, **kw: None,
        validate_fn=lambda *a, **kw: None,
        is_subgraph=True,
    )
    RoleRegistry.register("temp_role", spec)
    assert RoleRegistry.unregister("temp_role") is True
    assert RoleRegistry.get("temp_role") is None


def test_unregister_unknown_role_returns_false():
    """unregister 未注册 role 返回 False(不抛异常)。"""
    assert RoleRegistry.unregister("never_registered_xyz") is False


# ---- _validate 通过 RoleRegistry 分派 ----

def test_validate_rejects_unknown_role(fake_llm):
    """_validate 对未知 role 抛 ValueError("Unknown role: ...")。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    agent = Agent(name="mystery", role="mystery_role")
    with pytest.raises(ValueError, match="Unknown role: mystery_role"):
        compiler._validate(agent, depth=0, path="test")


def test_validate_worker_with_children_raises(fake_llm):
    """_validate 走 worker spec.validate_fn,worker 有 children 时报错。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    agent = Agent(
        name="w", role="worker",
        children=[Agent(name="x", role="worker")],
    )
    with pytest.raises(ValueError, match="worker cannot have children"):
        compiler._validate(agent, depth=0, path="test")


def test_validate_supervisor_without_children_raises(fake_llm):
    """_validate 走 supervisor spec.validate_fn,supervisor 无 children 时报错。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    agent = Agent(name="s", role="supervisor")
    with pytest.raises(ValueError, match="supervisor must have children"):
        compiler._validate(agent, depth=0, path="test")


def test_validate_supervisor_with_tools_raises(fake_llm):
    """_validate 走 supervisor spec.validate_fn,supervisor 有 tools 时报错。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    agent = Agent(
        name="s", role="supervisor",
        children=[Agent(name="w", role="worker")],
        tools=["read_file"],
    )
    with pytest.raises(ValueError, match="supervisor cannot have tools"):
        compiler._validate(agent, depth=0, path="test")


def test_validate_max_depth_still_enforced(fake_llm):
    """MAX_DEPTH 检查仍由 _validate 统一处理(测试可调小 compiler.MAX_DEPTH)。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    compiler.MAX_DEPTH = 2  # 调小便于测试

    # depth=3 > MAX_DEPTH=2,应抛错(无论 role 是否合法)
    agent = Agent(name="w", role="worker")
    with pytest.raises(ValueError, match="Max depth exceeded"):
        compiler._validate(agent, depth=3, path="test")


# ---- _compile_agent 通过 spec.compile_fn 分派 ----

def test_compile_agent_dispatches_via_registry(fake_llm):
    """_compile_agent 通过 spec.compile_fn 分派(默认 worker/supervisor 不变)。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    # 跟踪 worker spec.compile_fn 是否被调用
    original_worker_spec = RoleRegistry.get("worker")
    called = {"n": 0}
    original_compile_fn = original_worker_spec.compile_fn

    def tracking_compile_fn(*args, **kwargs):
        called["n"] += 1
        return original_compile_fn(*args, **kwargs)

    RoleRegistry.register("worker", RoleSpec(
        compile_fn=tracking_compile_fn,
        validate_fn=original_worker_spec.validate_fn,
        is_subgraph=True,
    ))
    try:
        team = Team(
            name="t", description="t",
            root=Agent(
                name="lead", role="supervisor",
                children=[Agent(name="w", role="worker")],
            ),
            default_model=ModelRef("qwen", "qwen-max"),
        )
        compiler.compile(team)
        assert called["n"] == 1, "worker spec.compile_fn 应被调用一次"
    finally:
        RoleRegistry.register("worker", original_worker_spec)


# ---- _compile_supervisor 通过 spec.is_subgraph 决定节点名前缀 ----

def test_compile_supervisor_uses_is_subgraph_for_node_name(fake_llm):
    """is_subgraph=True 的 child 节点名前缀为 worker_,False 为 agent_。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[
                Agent(name="coder", role="worker"),  # is_subgraph=True → worker_coder
                Agent(name="sub", role="supervisor",  # is_subgraph=False → agent_sub
                      children=[Agent(name="deep", role="worker")]),
            ],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "worker_coder" in node_names  # worker → worker_ 前缀
    assert "agent_sub" in node_names  # supervisor → agent_ 前缀


# ---- 第三方注册新 role 端到端测试 ----

def test_custom_role_can_be_registered_and_compiled(fake_llm):
    """第三方注册新 role "reviewer" 后可编译带 reviewer child 的 team。

    reviewer 视为子图型(is_subgraph=True),复用 worker 编译逻辑,
    节点名前缀应为 worker_(因 is_subgraph=True)。
    """
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    # 注册 reviewer role:复用 worker 的 compile_fn + validate_fn
    worker_spec = RoleRegistry.get("worker")
    reviewer_spec = RoleSpec(
        compile_fn=worker_spec.compile_fn,
        validate_fn=lambda agent, depth, path: None,  # reviewer 无特殊校验
        is_subgraph=True,
    )
    RoleRegistry.register("reviewer", reviewer_spec)
    try:
        team = Team(
            name="t", description="t",
            root=Agent(
                name="lead", role="supervisor",
                children=[Agent(name="rv", role="reviewer")],
            ),
            default_model=ModelRef("qwen", "qwen-max"),
        )
        graph = compiler.compile(team)
        node_names = set(graph.get_graph().nodes.keys())
        # reviewer 是子图型 → worker_ 前缀(与 worker 同)
        assert "worker_rv" in node_names
    finally:
        RoleRegistry.unregister("reviewer")


def test_custom_non_subgraph_role_uses_agent_prefix(fake_llm):
    """is_subgraph=False 的自定义 role 节点名前缀为 agent_。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    supervisor_spec = RoleRegistry.get("supervisor")
    # 注册一个 is_subgraph=False 的自定义 role "lead_sub"
    # compile_fn 复用 supervisor 的(因为需要返回编译好的 subgraph)
    RoleRegistry.register("lead_sub", RoleSpec(
        compile_fn=supervisor_spec.compile_fn,
        validate_fn=lambda agent, depth, path: None,
        is_subgraph=False,
    ))
    try:
        team = Team(
            name="t", description="t",
            root=Agent(
                name="root", role="supervisor",
                children=[Agent(
                    name="ls", role="lead_sub",
                    children=[Agent(name="w", role="worker")],
                )],
            ),
            default_model=ModelRef("qwen", "qwen-max"),
        )
        graph = compiler.compile(team)
        node_names = set(graph.get_graph().nodes.keys())
        # lead_sub is_subgraph=False → agent_ 前缀
        assert "agent_ls" in node_names
    finally:
        RoleRegistry.unregister("lead_sub")


def test_custom_role_validate_fn_invoked(fake_llm):
    """自定义 role 的 validate_fn 被调用,可拒绝非法配置。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    def strict_validate(agent, depth, path):
        if not agent.system_prompt:
            raise ValueError(f"reviewer must have system_prompt: {agent.name}")

    RoleRegistry.register("strict_reviewer", RoleSpec(
        compile_fn=RoleRegistry.get("worker").compile_fn,
        validate_fn=strict_validate,
        is_subgraph=True,
    ))
    try:
        # 无 system_prompt 的 strict_reviewer → 校验失败
        agent = Agent(name="rv", role="strict_reviewer")  # 无 system_prompt
        with pytest.raises(ValueError, match="reviewer must have system_prompt"):
            compiler._validate(agent, depth=0, path="test")
    finally:
        RoleRegistry.unregister("strict_reviewer")


def test_compile_rejects_unknown_child_role(fake_llm):
    """supervisor 的 child 用未注册的 role → _compile_supervisor 抛 "Unknown role"。"""
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())

    team = Team(
        name="t", description="t",
        root=Agent(
            name="lead", role="supervisor",
            children=[Agent(name="myst", role="mystery_role")],
        ),
        default_model=ModelRef("qwen", "qwen-max"),
    )
    with pytest.raises(ValueError, match="Unknown role: mystery_role"):
        compiler.compile(team)


# ---- RoleSpec 字段访问 ----

def test_role_spec_field_access():
    """RoleSpec 实例字段 compile_fn/validate_fn/is_subgraph 可访问。"""
    def cf(*a, **kw): pass
    def vf(*a, **kw): pass
    spec = RoleSpec(compile_fn=cf, validate_fn=vf, is_subgraph=True)
    assert spec.compile_fn is cf
    assert spec.validate_fn is vf
    assert spec.is_subgraph is True
