"""translator 纯函数测试：工具映射 / 权限规则集 / MCP / provider / system prompt。"""
from __future__ import annotations

from agentteam.domain.approval import ApprovalPolicy
from agentteam.harness import translator


# ---------- 工具名映射 ----------


def test_translate_builtin_tools():
    assert translator.translate_tool_name("read_file") == "read"
    assert translator.translate_tool_name("write_file") == "write"
    assert translator.translate_tool_name("list_dir") == "list"
    assert translator.translate_tool_name("search_web") == "webfetch"
    assert translator.translate_tool_name("bash") == "bash"


def test_translate_mcp_tool():
    assert translator.translate_tool_name("mcp:git:git_status") == "git_git_status"


def test_tools_enable_map_whitelist_semantics():
    # 声明 write_file → 只启用 write，其余内置工具全关
    m = translator.tools_enable_map(["write_file"])
    assert m["write"] is True
    assert m["bash"] is False and m["read"] is False and m["edit"] is False


def test_tools_enable_map_empty_disables_all():
    m = translator.tools_enable_map([])
    assert m and all(v is False for v in m.values())


def test_tools_enable_map_mcp_enabled():
    m = translator.tools_enable_map(["mcp:git:git_status"])
    assert m["git_git_status"] is True
    assert m["bash"] is False


# ---------- 权限规则集 ----------


def test_policy_rules_none_when_no_policy():
    assert translator.policy_to_permission_rules(None, ["write_file"]) is None
    assert translator.policy_to_permission_rules(
        ApprovalPolicy(level="step"), ["write_file"]
    ) is None
    assert translator.policy_to_permission_rules(
        ApprovalPolicy(level="worker"), []
    ) is None


def test_policy_rules_ask_targets_allow_rest():
    rules = translator.policy_to_permission_rules(
        ApprovalPolicy(level="tool", targets=["bash"]), []
    )
    by_perm = {r["permission"]: r["action"] for r in rules}
    assert by_perm["bash"] == "ask"
    assert by_perm["edit"] == "allow"
    assert by_perm["webfetch"] == "allow"


def test_policy_rules_mcp_targets():
    rules = translator.policy_to_permission_rules(
        ApprovalPolicy(level="tool", targets=["mcp:git:git_push"]),
        ["mcp:git:git_status", "mcp:git:git_push"],
    )
    by_perm = {r["permission"]: r["action"] for r in rules}
    assert by_perm["git_git_push"] == "ask"
    assert by_perm["git_git_status"] == "allow"


def test_targets_none_means_wildcard():
    assert translator.agentteam_tool_targets(
        ApprovalPolicy(level="tool")
    ) == {"*"}
    assert translator.agentteam_tool_targets(
        ApprovalPolicy(level="tool", targets=["read_file"])
    ) == {"read", "read_file"}


# ---------- MCP / provider ----------


def test_mcp_stdio():
    from agentteam.domain.mcp_server import MCPServer
    cfg = translator.mcp_to_opencode(MCPServer(
        name="git", command="npx", args=["-y", "mcp-git"], env={"G": "1"},
    ))
    assert cfg == {"type": "local",
                   "command": ["npx", "-y", "mcp-git"],
                   "environment": {"G": "1"}}


def test_mcp_http():
    from agentteam.domain.mcp_server import MCPServer
    cfg = translator.mcp_to_opencode(MCPServer(
        name="remote", command="", transport="http", url="http://x/mcp",
        env={"Authorization": "Bearer t"},
    ))
    assert cfg == {"type": "remote", "url": "http://x/mcp",
                   "headers": {"Authorization": "Bearer t"}}


def test_provider_patch_qwen_uses_env_key():
    from agentteam.models.provider import ModelRef
    patch = translator.provider_patch_for(ModelRef("qwen", "qwen-max"), "oc/m")
    assert patch["qwen"]["options"]["apiKey"] == "{env:DASHSCOPE_API_KEY}"
    assert "baseURL" in patch["qwen"]["options"]


def test_provider_patch_builtin_empty():
    from agentteam.models.provider import ModelRef
    assert translator.provider_patch_for(ModelRef("openai", "gpt-4o"), "oc/m") == {}


def test_model_ref_mapping():
    from agentteam.models.provider import ModelRef
    m = translator.model_ref_to_opencode(ModelRef("qwen", "qwen-max"), "oc/fallback")
    assert m == {"providerID": "qwen", "modelID": "qwen-max"}
    # ref=None → 默认值解析
    m2 = translator.model_ref_to_opencode(None, "opencode/ling-3.0-flash-fin-free")
    assert m2 == {"providerID": "opencode", "modelID": "ling-3.0-flash-fin-free"}


# ---------- system prompt 组装 ----------


def test_system_prompt_with_skills():
    s = translator.system_prompt_for_spec({
        "system_prompt": "你是执行者",
        "skills_content": {"code_review": "# Skill: 审查\n做审查"},
    })
    assert s.startswith("你是执行者")
    assert '<skill name="code_review">' in s
    assert "# Skill: 审查" in s


def test_system_prompt_no_skills():
    assert translator.system_prompt_for_spec(
        {"system_prompt": "x", "skills_content": {}}
    ) == "x"
