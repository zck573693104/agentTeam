"""统一 Agent 节点 + TeamRef 引用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.models.provider import ModelRef


@dataclass
class TeamRef:
    """引用另一个 Team 作为本节点的 child。

    编译时由 TeamCompiler 从 _team_registry 取出目标 Team，
    编译其 root 作为本节点。alias 用于在父 Team 内重命名，防重名。

    mcp_overrides：引用 sub-Team 时追加注册的 MCP 服务（扩展语义，
    不替换 sub-Team 自身的 mcp_servers）。
    """
    name: str
    alias: str | None = None
    mcp_overrides: list[MCPServer] = field(default_factory=list)


@dataclass
class Agent:
    """统一智能体节点。

    - role="supervisor"：派活给 children，跑 plan→children→review 循环
    - role="worker"：叶子节点，跑 ReAct 工具循环

    约束（编译期校验，见 TeamCompiler._validate）：
    - supervisor 必须有 children，tools 必须为空
    - worker 必须无 children，可有 tools
    - ref 与 children 可同时存在：ref 指向库时作为模板，调用处 children 覆盖模板 children

    mcp_servers：本 Agent 级别挂载的 MCP 服务，编译期由 TeamCompiler 注册到
    ToolRegistry。Worker 在 tools 中用 `mcp:{server.name}:{tool.name}` 引用。
    """
    name: str
    role: Literal["supervisor", "worker"]
    system_prompt: str = ""
    model: ModelRef | None = None

    # supervisor 专属
    children: list[Union["Agent", "TeamRef"]] = field(default_factory=list)
    approval_policy: ApprovalPolicy | None = None

    # worker 专属
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 10

    # 专家库引用（解析前填充；解析后由 AgentLibrary.resolve 置空）
    ref: str | None = None  # 格式："library:agent_name"

    # MCP 挂载（任意角色可挂，worker 在 tools 中引用注册后的工具名）
    mcp_servers: list[MCPServer] = field(default_factory=list)

    # SP7a: 装备的 skill 名列表(对应 skills/ 目录下 .md 文件的 stem)
    # 编译期由 SkillLoader.load 解析;缺失抛 KeyError(编译期 fail-fast)。
    skills: list[str] = field(default_factory=list)

    # SP7b: 进化代数,默认 1(每次 EvolutionEngine.trigger 任一维度成功后 +=1)
    version: int = 1
