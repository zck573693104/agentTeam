"""领域层:仅保留跨内核共享的模型(Ticket/Worker 等 v2 领域在 agentteam/v2/domain)。"""
from agentteam.domain.mcp_server import MCPServer

__all__ = ["MCPServer"]
