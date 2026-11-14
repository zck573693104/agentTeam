"""v2 API:组件容器 + 路由装配。"""
from agentteam.v2.api.deps import V2Components, create_v2_components
from agentteam.v2.api.router import build_v2_router

__all__ = ["V2Components", "create_v2_components", "build_v2_router"]
