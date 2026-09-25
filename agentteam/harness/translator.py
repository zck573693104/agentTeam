"""Team JSON / 领域对象 → opencode 配置翻译。

全部为纯函数：输入领域对象，输出符合 opencode v1.18.32 契约的 dict
（见 docs/opencode-harness-design.md §2/§4），不产生 I/O，便于测试。

映射总则：
- AgentTeam 工具名 → opencode 内置工具名（read_file→read 等），
  `mcp:{server}:{tool}` → `{server}_{tool}`（opencode 的 MCP 工具前缀约定）。
- `ApprovalPolicy(level="tool", targets=[...])` → 会话 PermissionRuleset：
  targets 命中的工具类别 `ask`，其余工具类别 `allow`（三级审批中只有
  tool 级落进 opencode permission 体系；step/worker 级由控制平面门实现）。
- MCPServer(stdio) → McpLocalConfig；MCPServer(http) → McpRemoteConfig。
- ModelRef(provider,name) → {providerID, modelID}，并给出对应 provider
  的 opencode 配置补丁（经 PATCH /config 注入）。
"""
from __future__ import annotations

from typing import Any

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef

# AgentTeam 内置工具名 → opencode 内置工具名。
# opencode 常用内置工具：bash/edit/write/read/grep/glob/list/webfetch/task/todo*。
AGENTTEAM_TO_OPENCODE_TOOLS: dict[str, str] = {
    "read_file": "read",
    "write_file": "write",
    "list_dir": "list",
    "search_web": "webfetch",
    "bash": "bash",
    "edit": "edit",
}

# opencode 常见内置工具全集：空 tools 白名单时全部禁用（AgentTeam worker
# 未声明工具 = 无工具，与 LangGraph 引擎 bind_tools([]) 对齐）。
OPENCODE_BUILTIN_TOOLS: tuple[str, ...] = (
    "bash", "edit", "write", "read", "grep", "glob", "list",
    "webfetch", "task", "todowrite", "todoread",
)

# opencode 权限类别 → 该类别下的工具名（用于反向构造 tool 级规则集）。
# MCP 工具运行时才知道，不在静态表内，translator 对 `mcp:` 前缀按 server 名映射。
OPENCODE_PERMISSION_CATEGORIES: tuple[str, ...] = (
    "bash", "edit", "webfetch", "external_directory", "doom_loop",
)


def translate_tool_name(agentteam_tool: str) -> str:
    """AgentTeam 工具引用 → opencode 工具名。

    `mcp:{server}:{tool}` → `{server}_{tool}`；未知内置名原样返回
    （保持与 opencode 工具名一致时无需翻译）。
    """
    if agentteam_tool.startswith("mcp:"):
        parts = agentteam_tool.split(":", 2)
        if len(parts) == 3:
            return f"{parts[1]}_{parts[2]}"
    return AGENTTEAM_TO_OPENCODE_TOOLS.get(agentteam_tool, agentteam_tool)


def tools_enable_map(agentteam_tools: list[str]) -> dict[str, bool]:
    """AgentTeam worker tools → prompt 的 tools 白名单（启用映射）。

    语义：只启用声明的工具，内置工具全集其余全部关闭（AgentTeam worker 的
    tools 字段即白名单语义，与 LangGraph 引擎 `bind_tools(tools)` 对齐）。
    空 tools 列表 → 全部内置工具禁用（worker 无工具可调）。
    MCP 工具（mcp: 前缀声明）映射为 `{server}_{tool}` 并显式启用。
    """
    enabled = {translate_tool_name(t): True for t in (agentteam_tools or [])}
    return {name: enabled.get(name, False) for name in OPENCODE_BUILTIN_TOOLS} | {
        name: True for name in enabled if name not in OPENCODE_BUILTIN_TOOLS
    }


def policy_to_permission_rules(
    policy: ApprovalPolicy | None,
    tools: list[str],
) -> list[dict[str, str]] | None:
    """tool 级审批策略 → opencode 会话 PermissionRuleset。

    规则求值顺序敏感（opencode 顺序匹配）：先写 ask（targets 命中），
    再写其余类别的默认动作。默认动作：
    - `allow`（默认）：v1.18.32 的 deny 动作会静默终止回合（设计文档 §7）
    - `deny`：设 AGENTTEAM_OC_STRICT_TOOLS=1 时下发（供修复后的版本启用）
    无策略 → None（沿用 opencode 默认权限）。
    非 tool 级策略 → None（step/worker 级在控制平面门处理）。

    targets 支持两种写法（与 _should_approve 对齐）：
    - AgentTeam 工具名（如 "write_file"、"mcp:git:git_status"）
    - opencode 工具名（如 "bash"）原样匹配类别
    """
    if policy is None or policy.level != "tool":
        return None

    import os
    default_action = (
        "deny" if os.environ.get("AGENTTEAM_OC_STRICT_TOOLS") == "1" else "allow"
    )

    target_categories: set[str] = set()
    for t in policy.targets or []:
        target_categories.add(translate_tool_name(t))

    rules: list[dict[str, str]] = []
    for category in OPENCODE_PERMISSION_CATEGORIES:
        action = "ask" if category in target_categories else default_action
        rules.append({"permission": category, "pattern": "*", "action": action})
    # worker 声明的 MCP 工具：命中 targets 的逐工具 ask，其余按默认动作
    for t in tools:
        if t.startswith("mcp:"):
            oc_name = translate_tool_name(t)
            action = "ask" if oc_name in target_categories else default_action
            rules.append({"permission": oc_name, "pattern": "*", "action": action})
    return rules


def agentteam_tool_targets(policy: ApprovalPolicy | None) -> set[str]:
    """tool 级策略 targets → 匹配用工具名集合。

    返回「翻译后的 opencode 类别名 ∪ 原始 AgentTeam 工具名」：
    真实 opencode 内置工具按类别命中（write_file→write），MCP 工具与
    测试替身按原样名命中。targets 为 None（全部工具都要审批）返回 {"*"}。
    """
    if policy is None or policy.level != "tool":
        return set()
    if policy.targets is None:
        return {"*"}
    result: set[str] = set()
    for t in policy.targets:
        result.add(t)
        result.add(translate_tool_name(t))
    return result


def mcp_to_opencode(server: MCPServer) -> dict[str, Any]:
    """MCPServer → POST /mcp 的 config（McpLocalConfig / McpRemoteConfig）。"""
    if server.transport == "http" or (server.url and not server.command):
        cfg: dict[str, Any] = {"type": "remote", "url": server.url}
        if server.env:
            cfg["headers"] = dict(server.env)
        return cfg
    command = [server.command] + list(server.args)
    cfg = {"type": "local", "command": command}
    if server.env:
        cfg["environment"] = dict(server.env)
    return cfg


def model_ref_to_opencode(ref: ModelRef | None, default: str) -> dict[str, str]:
    """ModelRef → opencode model 引用 {providerID, modelID}。

    AgentTeam provider 名与 opencode providerID 不完全一致（qwen 走
    openai-compatible 网关），经 PROVIDER_ID_MAP 映射；未映射的原样传递。
    ref 为 None 时使用默认值（形如 "providerID/modelID"）。
    """
    provider_id, model_id = default.split("/", 1)
    if ref is not None:
        provider_id = PROVIDER_ID_MAP.get(ref.provider, ref.provider)
        model_id = ref.name
    return {"providerID": provider_id, "modelID": model_id}


PROVIDER_ID_MAP: dict[str, str] = {
    # qwen 经 DashScope OpenAI 兼容端点接入，opencode 侧统一叫 qwen（自建 provider）
    "qwen": "qwen",
    "openai": "openai",
    "anthropic": "anthropic",
    "ollama": "ollama",
}


def provider_patch_for(ref: ModelRef | None, default: str) -> dict[str, Any]:
    """计算 opencode provider 配置补丁（PATCH /config 的 provider 增量）。

    只为 AgentTeam 声明的 provider 生成 opencode 侧定义；api key 一律从
    环境变量引用（{env:VAR}），不落盘明文。
    """
    provider = ref.provider if ref is not None else default.split("/", 1)[0]
    provider = PROVIDER_ID_MAP.get(provider, provider)

    if provider == "qwen":
        return {
            "qwen": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Qwen (DashScope compatible)",
                "options": {
                    "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "apiKey": "{env:DASHSCOPE_API_KEY}",
                },
            }
        }
    if provider == "ollama":
        return {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (local)",
                "options": {
                    "baseURL": "{env:OLLAMA_BASE_URL|http://127.0.0.1:11434/v1}",
                    "apiKey": "{env:OLLAMA_API_KEY|ollama}",
                },
            }
        }
    # openai / anthropic / deepseek 等 opencode 内置 provider：模型列表自带，
    # 无需补丁（auth 用 opencode auth login 或 env key）。
    return {}


def agent_system_prompt(agent: Agent, skills_content: dict[str, str]) -> str:
    """组装 worker/supervisor 的 system prompt（SP7 skills 注入，与 LangGraph 引擎对齐）。

    skills_content: {skill_name: markdown 内容}（由 SkillLoader.load 预先取出）。
    """
    parts = [agent.system_prompt]
    for name in agent.skills:
        content = skills_content.get(name)
        if content:
            parts.append(f'<skill name="{name}">\n{content}\n</skill>')
    return "\n\n".join(p for p in parts if p)


def system_prompt_for_spec(spec: dict) -> str:
    """同 agent_system_prompt，但作用于可序列化 spec/frame dict
    （键：system_prompt / skills_content，见 engine._child_spec）。"""
    parts = [spec.get("system_prompt", "")]
    for name, content in (spec.get("skills_content") or {}).items():
        parts.append(f'<skill name="{name}">\n{content}\n</skill>')
    return "\n\n".join(p for p in parts if p)


def worker_tool_directive(tools: list[str]) -> str:
    """工具白名单的 prompt 级约束指令。

    背景：opencode v1.18.32 的 per-request `tools` 参数与 permission
    `deny` 动作均有缺陷（会静默终止回合，见设计文档 §7），工具白名单
    只能退化为 prompt 级约束；`ask` 规则（审批语义）不受影响。
    """
    if not tools:
        return "注意：不要调用任何工具，直接用文本完成任务。"
    names = ", ".join(translate_tool_name(t) for t in tools)
    return f"注意：你只能使用这些工具：{names}。不要调用其他任何工具。"


def worker_system_prompt(spec: dict) -> str:
    """worker 会话的最终 system prompt = 基础 prompt + skills + 工具约束。"""
    base = system_prompt_for_spec(spec)
    directive = worker_tool_directive(spec.get("tools") or [])
    return f"{base}\n\n{directive}" if base else directive
