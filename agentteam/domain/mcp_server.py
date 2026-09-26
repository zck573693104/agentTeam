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

    P-B6 鉴权字段(对标阿里云 AgentTeams "MCP Server 鉴权"):
    - auth_type: 鉴权方式
        'none'      = 无鉴权(默认,本地 stdio 场景)
        'api_key'   = API Key header(用 auth_credential 作 key 值)
        'bearer'    = OAuth2 Bearer token(auth_credential 作 access_token)
        'basic'     = HTTP Basic(auth_credential 形如 "user:pass")
        'oauth2'    = OAuth2 客户端凭证(后续扩展,需配 token_url)
    - auth_credential: 加密存储的凭证值(由 CryptoService 加密/解密)
        写入 SQLite 前必须 encrypt,从 DB 读取后 decrypt
        API 响应必须 mask(永远不返回明文)
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: Literal["stdio", "http"] = "stdio"
    url: str | None = None
    # namespace=None → 用 name 作工具名前缀(向后兼容)
    namespace: str | None = None
    # P-B6: MCP Server 鉴权
    auth_type: Literal["none", "api_key", "bearer", "basic", "oauth2"] = "none"
    auth_credential: str | None = None  # 加密后的凭证值
    auth_header_name: str = "Authorization"  # api_key 模式可定制 header 名

    @property
    def tool_prefix(self) -> str:
        """工具名前缀:`mcp:{namespace or name}:`。"""
        return f"mcp:{self.namespace or self.name}:"

    @property
    def requires_auth(self) -> bool:
        """是否需要鉴权(auth_type != 'none')。"""
        return self.auth_type != "none"

    def build_auth_headers(self, decrypted_credential: str | None = None) -> dict[str, str]:
        """构建鉴权 header(用于 http transport)。

        参数:
            decrypted_credential: 已解密的凭证值(None 时用 self.auth_credential,
            假设它已是明文,适用于 stdio env 注入场景)

        返回:可合并到 HTTP request headers 的 dict
        """
        if not self.requires_auth:
            return {}
        cred = decrypted_credential if decrypted_credential is not None else (self.auth_credential or "")
        if self.auth_type == "api_key":
            return {self.auth_header_name: cred}
        if self.auth_type == "bearer":
            return {"Authorization": f"Bearer {cred}"}
        if self.auth_type == "basic":
            import base64
            encoded = base64.b64encode(cred.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        # oauth2 留待后续扩展(需要 token_url + client_id + client_secret)
        return {}

