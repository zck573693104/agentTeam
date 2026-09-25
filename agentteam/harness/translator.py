"""Team JSON / 领域对象 → opencode v2 会话契约翻译（SP8 / v2 迁移）。

全部为纯函数：输入领域对象，输出符合 opencode v1.18.32 `/api/*` 契约的 dict
或文本（见 docs/opencode-harness-design.md §2/§8），不产生 I/O，便于测试。

映射总则：
- AgentTeam 工具名 → opencode 内置工具名（read_file→read 等），
  `mcp:{server}:{tool}` → `{server}_{tool}`（opencode 的 MCP 工具命名约定）。
- **v2 没有 per-request system / format / tools，也没有会话级 permission 规则集**
  （实测：自定义 agent 的 prompt 不被应用，v2 会话不产生 pending permission）。
  因此三级审批的 tool 级从「事前规则集」改为「事后中断 + 审计」：
  引擎观测 `session.next.tool.*` 事件，命中白名单外/审批目标即 POST interrupt。
- worker/supervisor 的 system prompt **内联进用户文本**（`prompt_with_system`）。
- 结构化计划（Plan JSON）改为「prompt 内嵌 schema + 宽容提取」（`plan_prompt`、
  `extract_json`）。
- MCPServer(stdio) → McpLocalConfig；MCPServer(http) → McpRemoteConfig（仍走 v1
  `POST /mcp`，v2 无对应端点）。
- ModelRef(provider,name) → {providerID, modelID}，客户端再转 v2 {id, providerID}。
"""
from __future__ import annotations

import json
import re
from typing import Any

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef

# AgentTeam 内置工具名 → opencode 内置工具名。
AGENTTEAM_TO_OPENCODE_TOOLS: dict[str, str] = {
    "read_file": "read",
    "write_file": "write",
    "list_dir": "list",
    "search_web": "webfetch",
    "bash": "bash",
    "edit": "edit",
}

# opencode 常见内置工具全集：用于生成「只允许声明工具」的 prompt 级白名单，
# 以及事后拦截时的名字识别域（v2 无法在 API 层面禁用工具）。
OPENCODE_BUILTIN_TOOLS: tuple[str, ...] = (
    "bash", "edit", "write", "read", "grep", "glob", "list",
    "webfetch", "task", "todowrite", "todoread",
)

# v2 事件里可能出现的“非工具”噪声名：不参与白名单判定。
NON_TOOL_NAMES: frozenset[str] = frozenset({"todowrite", "todoread", "task"})


def translate_tool_name(agentteam_tool: str) -> str:
    """AgentTeam 工具引用 → opencode 工具名。

    `mcp:{server}:{tool}` → `{server}_{tool}`；未知内置名原样返回。
    """
    if agentteam_tool.startswith("mcp:"):
        parts = agentteam_tool.split(":", 2)
        if len(parts) == 3:
            return f"{parts[1]}_{parts[2]}"
    return AGENTTEAM_TO_OPENCODE_TOOLS.get(agentteam_tool, agentteam_tool)


def worker_tool_whitelist(agentteam_tools: list[str] | None) -> set[str]:
    """worker 允许调用的 opencode 工具名集合（事后拦截的判定域）。

    AgentTeam 的 tools 字段是白名单语义：未声明 = 无工具。返回集合同时包含
    原始 AgentTeam 名（真实 opencode 名字翻译后可能一致也可能不一致，
    测试替身按原样名调用），便于两侧匹配。
    """
    names: set[str] = set()
    for t in agentteam_tools or []:
        names.add(t)
        names.add(translate_tool_name(t))
    return names


def agentteam_tool_targets(policy: ApprovalPolicy | None) -> set[str]:
    """tool 级策略 targets → 匹配用工具名集合。

    返回「翻译后的 opencode 名 ∪ 原始 AgentTeam 工具名」；
    targets 为 None（全部工具都要审批）返回 {"*"}；非 tool 级返回空集。
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


def tool_in_targets(tool_name: str, targets: set[str]) -> bool:
    """观测到的 opencode 工具名是否落在审批 targets / 白名单内。"""
    if not targets:
        return False
    if "*" in targets:
        return True
    if tool_name in targets:
        return True
    # 反向：targets 里写的是 AgentTeam 名（write_file），事件里是 opencode 名（write）
    return any(translate_tool_name(t) == tool_name for t in targets)


# ---------- prompt 组装（v2：system 内联 + 无结构化输出） ----------

def prompt_with_system(system: str, text: str) -> str:
    """v2 唯一的 system prompt 通道：把角色设定前置到用户文本。

    分隔线让模型区分「指令」与「任务」，也便于日志里定位。
    """
    system = (system or "").strip()
    if not system:
        return text
    return f"{system}\n\n════════ 以下是本次任务 ════════\n\n{text}"


def agent_system_prompt(agent: Agent, skills_content: dict[str, str]) -> str:
    """组装 worker/supervisor 基础 system prompt（SP7 skills 注入）。

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


def worker_tool_directive(tools: list[str] | None) -> str:
    """工具白名单的 prompt 级约束指令。

    v2 没有 per-request tools 参数，也没有会话级禁用开关，白名单只能
    「prompt 约束 + 引擎事后中断」两层表达。
    """
    if not tools:
        return "注意：不要调用任何工具，直接用文本完成任务。"
    names = ", ".join(translate_tool_name(t) for t in tools)
    return f"注意：你只能使用这些工具：{names}。不要调用其他任何工具。"


def worker_system_prompt(spec: dict) -> str:
    """worker 的最终角色设定 = 基础 prompt + skills + 工具白名单指令。"""
    base = system_prompt_for_spec(spec)
    directive = worker_tool_directive(spec.get("tools") or [])
    return f"{base}\n\n{directive}" if base else directive


def worker_user_prompt(spec: dict, instruction: str) -> str:
    """worker 会话的用户文本（v2：system 内联 + 任务）。"""
    return prompt_with_system(worker_system_prompt(spec), instruction)


def supervisor_user_prompt(spec: dict, text: str) -> str:
    """控制平面一次性 prompt（plan/review）：角色设定 + 禁用工具 + 任务。"""
    base = system_prompt_for_spec(spec)
    directive = worker_tool_directive([])
    return prompt_with_system(f"{base}\n\n{directive}".strip(), text)


def approved_resume_prompt(tool: str, worker: str | None = None) -> str:
    """tool 级审批放行后补发给同一会话的续跑指令。

    v2 的事后中断已经把回合掐断（不会有 pending permission 可回帖），
    所以放行必须靠新一轮 prompt 把任务推下去。
    """
    who = f"你上一步对 {tool} 的调用" if not worker else f"{worker} 上一步对 {tool} 的调用"
    return (
        f"审批已放行：{who}已被人工批准。"
        "请从中断处继续完成原任务，不要重复已经成功完成的步骤，"
        "最后按原要求输出完整结果。"
    )


# ---------- 结构化计划（v2：prompt 内嵌 schema + 宽容提取） ----------

def plan_prompt(schema: dict, roster: list[str], instruction: str) -> str:
    """计划拆解的用户文本：JSON Schema 内联 + 只输出 JSON 的硬约束。

    v2 无 format=json_schema，故契约靠 prompt 表达、由 extract_json 容错提取。
    """
    return (
        "请把以下任务拆解成可执行的步骤计划，每步指派一个 worker。\n"
        f"可用的 worker（worker 字段必须从这个列表中选择，一字不差）："
        f"{', '.join(roster)}\n"
        f"\n任务：\n{instruction}\n"
        "\n只输出一个 JSON 对象，不要输出任何解释、前后缀或代码块围栏。"
        "JSON 必须严格符合以下 Schema：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """从模型回答里宽容提取 JSON 对象。

    依次尝试：整体解析 → 剥去 ``` 围栏 → 首个平衡的花括号片段。
    失败抛 ValueError（调用方按「计划非法」处理，与 v1 结构化输出失败同形）。
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty answer, expected a JSON object")
    candidates: list[str] = [raw]
    candidates += [m.strip() for m in _FENCE_RE.findall(raw) if m.strip()]
    fragment = _first_balanced_object(raw)
    if fragment:
        candidates.append(fragment)
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"no JSON object found in answer: {raw[:200]}")


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# ---------- MCP / provider（无 v2 等价端点，仍走 v1 配置面） ----------

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
