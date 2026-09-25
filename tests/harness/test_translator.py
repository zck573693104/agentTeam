"""translator 纯函数测试：工具映射 / 白名单判定 / prompt 内联 / 计划提取 / MCP / provider。"""
from __future__ import annotations

import pytest

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


def test_worker_tool_whitelist_covers_both_namespaces():
    # 白名单同时收录 AgentTeam 名与翻译后的 opencode 名：v2 事件里的工具名
    # 可能是任意一侧（真实 opencode 用内置名，测试替身常用声明原样）
    w = translator.worker_tool_whitelist(["write_file"])
    assert w == {"write_file", "write"}


def test_worker_tool_whitelist_empty_means_no_tools():
    assert translator.worker_tool_whitelist([]) == set()
    assert translator.worker_tool_whitelist(None) == set()


def test_worker_tool_whitelist_mcp():
    assert translator.worker_tool_whitelist(["mcp:git:git_status"]) == {
        "mcp:git:git_status", "git_git_status"}


# ---------- 事后拦截的目标匹配 ----------


def test_tool_in_targets_matches_agentteam_and_opencode_names():
    # targets/whitelist 由 agentteam_tool_targets / worker_tool_whitelist 生成，
    # 两个命名空间都在，因此事件里的名字无论哪一侧都能命中
    targets = translator.agentteam_tool_targets(
        ApprovalPolicy(level="tool", targets=["write_file"])
    )
    assert translator.tool_in_targets("write", targets) is True
    assert translator.tool_in_targets("write_file", targets) is True
    assert translator.tool_in_targets("bash", targets) is False
    # 手工传单侧名字时按翻译匹配（opencode 名 → AgentTeam 名）
    assert translator.tool_in_targets("write", {"write_file"}) is True


def test_tool_in_targets_wildcard_and_empty():
    assert translator.tool_in_targets("bash", {"*"}) is True
    assert translator.tool_in_targets("bash", set()) is False


def test_mcp_targets_match_translated_name():
    targets = translator.agentteam_tool_targets(
        ApprovalPolicy(level="tool", targets=["mcp:git:git_push"])
    )
    assert translator.tool_in_targets("git_git_push", targets) is True
    assert translator.tool_in_targets("git_git_status", targets) is False


def test_non_tool_names_are_not_enforced():
    # 计划簿记类噪声名不参与白名单执法
    assert "todowrite" in translator.NON_TOOL_NAMES
    assert translator.tool_in_targets(
        "todowrite", translator.worker_tool_whitelist(["write_file"])
    ) is False


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


# ---------- prompt 内联（v2 无 per-request system） ----------


def test_prompt_with_system_prepends_role_and_separator():
    s = translator.prompt_with_system("你是执行者", "做A")
    assert s.startswith("你是执行者")
    assert "以下是本次任务" in s
    assert s.rstrip().endswith("做A")


def test_prompt_with_system_blank_returns_text():
    assert translator.prompt_with_system("   ", "做A") == "做A"


def test_worker_tool_directive():
    assert translator.worker_tool_directive([]) == \
        "注意：不要调用任何工具，直接用文本完成任务。"
    d = translator.worker_tool_directive(["write_file", "bash"])
    assert "write" in d and "bash" in d
    assert "只能使用" in d


def test_worker_user_prompt_includes_whitelist():
    p = translator.worker_user_prompt(
        {"system_prompt": "你是执行者", "skills_content": {},
         "tools": ["write_file"]},
        "写一个文件",
    )
    assert "你是执行者" in p
    assert "只能使用这些工具：write" in p
    assert "写一个文件" in p


def test_supervisor_user_prompt_forbids_tools():
    p = translator.supervisor_user_prompt(
        {"system_prompt": "你是主管", "skills_content": {}}, "拆解任务"
    )
    assert "你是主管" in p
    assert "不要调用任何工具" in p
    assert "拆解任务" in p


def test_approved_resume_prompt_mentions_tool_and_worker():
    p = translator.approved_resume_prompt("write", "w2")
    assert "w2" in p and "write" in p
    assert "继续" in p


# ---------- 计划 prompt + 宽容 JSON 提取 ----------


def test_plan_prompt_carries_schema_roster_and_constraint():
    p = translator.plan_prompt(
        {"type": "object", "required": ["steps"]}, ["w1", "w2"], "做个网站"
    )
    assert '"required": ["steps"]' in p
    assert "w1, w2" in p
    assert "做个网站" in p
    assert "只输出一个 JSON 对象" in p


def test_extract_json_plain():
    assert translator.extract_json('{"steps": []}') == {"steps": []}


def test_extract_json_from_prose_and_fence():
    # v2 无结构化输出通道：模型常在 JSON 前后夹带说明文字
    assert translator.extract_json(
        "好的，计划如下：\n```json\n{\"steps\": [{\"worker\": \"w1\"}]}\n```\n以上。"
    ) == {"steps": [{"worker": "w1"}]}
    assert translator.extract_json(
        "计划：{\"steps\": [{\"worker\": \"w2\"}], \"m\": {\"a\": 1}} 完毕"
    ) == {"steps": [{"worker": "w2"}], "m": {"a": 1}}


def test_extract_json_keeps_string_braces():
    assert translator.extract_json('前缀 {"instruction": "a}b"} 后缀') == \
        {"instruction": "a}b"}


@pytest.mark.parametrize("bad", ["", "   ", "没有任何 json", "[1, 2]", "{\"a\": "])
def test_extract_json_raises_value_error(bad):
    with pytest.raises(ValueError):
        translator.extract_json(bad)


# ---------- 事后中断判定（ApprovalBroker 纯函数部分） ----------


def test_inspect_tool_call_clean_when_whitelisted():
    from agentteam.harness.approval import ApprovalBroker
    w = translator.worker_tool_whitelist(["write_file"])
    assert ApprovalBroker.inspect_tool_call(None, "write", w) is None
    assert ApprovalBroker.inspect_tool_call(None, "write_file", w) is None


def test_inspect_tool_call_not_whitelisted():
    from agentteam.harness.approval import (
        VIOLATION_NOT_WHITELISTED, ApprovalBroker)
    assert ApprovalBroker.inspect_tool_call(
        None, "bash", translator.worker_tool_whitelist(["write_file"])
    ) == VIOLATION_NOT_WHITELISTED


def test_inspect_tool_call_requires_approval_beats_whitelist():
    from agentteam.harness.approval import (
        VIOLATION_REQUIRES_APPROVAL, ApprovalBroker)
    policy = ApprovalPolicy(level="tool", targets=["bash"])
    assert ApprovalBroker.inspect_tool_call(
        policy, "bash", translator.worker_tool_whitelist(["bash"])
    ) == VIOLATION_REQUIRES_APPROVAL


def test_inspect_tool_call_ignores_bookkeeping_and_empty_names():
    from agentteam.harness.approval import ApprovalBroker
    assert ApprovalBroker.inspect_tool_call(None, "todowrite", set()) is None
    assert ApprovalBroker.inspect_tool_call(None, "", set()) is None


def test_tool_guard_mode_defaults_strict(monkeypatch):
    from agentteam.harness import approval
    monkeypatch.delenv("AGENTTEAM_OC_TOOL_GUARD", raising=False)
    assert approval.tool_guard_mode() == "strict"
    monkeypatch.setenv("AGENTTEAM_OC_TOOL_GUARD", "nonsense")
    assert approval.tool_guard_mode() == "strict"
    monkeypatch.setenv("AGENTTEAM_OC_TOOL_GUARD", "audit")
    assert approval.tool_guard_mode() == "audit"
