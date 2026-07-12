from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MCPServer:
    """MCP 服务配置：command/args/env 启动 stdio 子进程，或连接 HTTP 端点。"""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio", "http"] = "stdio"
    url: str | None = None
