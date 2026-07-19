from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MCPServer:
    """MCP 服务配置：command/args/env 启动 stdio 子进程，或连接 HTTP 端点。

    namespace(P3-4 新增):工具名前缀,默认 None 时回退到 name。
    显式设置可解决同名 MCP server(如两个 filesystem server 但 mount 不同路径)
    的工具名冲突——两者工具名都形如 `mcp:filesystem:read_file`,第二个会被
    静默跳过。设置 namespace="fs_project_a"/"fs_project_b" 后工具名变为
    `mcp:fs_project_a:read_file` / `mcp:fs_project_b:read_file`,各自独立注册。
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio", "http"] = "stdio"
    url: str | None = None
    # namespace=None → 用 name 作工具名前缀(向后兼容)
    namespace: str | None = None

    @property
    def tool_prefix(self) -> str:
        """工具名前缀:`mcp:{namespace or name}:`。"""
        return f"mcp:{self.namespace or self.name}:"
