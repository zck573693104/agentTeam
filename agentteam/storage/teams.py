"""teams 表的读写:Team 配置持久化。"""
from __future__ import annotations

import json

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.domain.team import Team
from agentteam.security.crypto import get_crypto
from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


def _encrypt_mcp_env(team_dict: dict) -> dict:
    """对 team_dict 中所有 mcp_servers[].env 做 AES-GCM 加密。

    对标阿里云 AgentTeams "管控面统一加密托管":
    MCP server env 可能含 GITHUB_PERSONAL_ACCESS_TOKEN / API_KEY 等敏感凭证,
    明文入库会导致 SQLite 文件泄漏时凭证同时泄漏。

    策略:整段 env dict 序列化为 JSON 后加密,存储为 {"_encrypted": "<ciphertext>"}。
    读取时 _decrypt_mcp_env 解密还原。Agent 运行时拿到的是明文 env(零接触明文 key
    仍由 BaseAdapter 在内部读取),与历史行为完全兼容。
    """
    crypto = get_crypto()
    if not crypto.enabled:
        return team_dict
    for server in team_dict.get("mcp_servers", []):
        env = server.get("env")
        if env and isinstance(env, dict) and not env.get("_encrypted"):
            plaintext = json.dumps(env, ensure_ascii=False, sort_keys=True)
            server["env"] = {"_encrypted": crypto.encrypt(plaintext)}
    # 递归处理 agent.mcp_servers(在 root 树中)
    _encrypt_mcp_env_in_agent(team_dict.get("root"))
    return team_dict


def _encrypt_mcp_env_in_agent(agent: dict | None) -> None:
    """递归处理 Agent 树中各节点的 mcp_servers[].env。"""
    if not agent:
        return
    crypto = get_crypto()
    for server in agent.get("mcp_servers", []):
        env = server.get("env")
        if env and isinstance(env, dict) and not env.get("_encrypted"):
            plaintext = json.dumps(env, ensure_ascii=False, sort_keys=True)
            server["env"] = {"_encrypted": crypto.encrypt(plaintext)}
    for child in agent.get("children", []):
        if child.get("_type") == "TeamRef":
            for server in child.get("mcp_overrides", []):
                env = server.get("env")
                if env and isinstance(env, dict) and not env.get("_encrypted"):
                    plaintext = json.dumps(env, ensure_ascii=False, sort_keys=True)
                    server["env"] = {"_encrypted": crypto.encrypt(plaintext)}
        else:
            _encrypt_mcp_env_in_agent(child)


def _decrypt_mcp_env(team_dict: dict) -> dict:
    """解密 _encrypt_mcp_env 的输出,还原明文 env。

    无加密标记(_encrypted 不存在)时原样返回,兼容历史明文数据。
    解密失败(主密钥不匹配/数据损坏)时返回空 env,避免泄漏半截密文。
    """
    crypto = get_crypto()
    if not crypto.enabled:
        return team_dict
    for server in team_dict.get("mcp_servers", []):
        env = server.get("env")
        if env and isinstance(env, dict) and env.get("_encrypted"):
            plaintext = crypto.decrypt(env["_encrypted"])
            try:
                server["env"] = json.loads(plaintext)
            except (json.JSONDecodeError, TypeError):
                server["env"] = {}
    _decrypt_mcp_env_in_agent(team_dict.get("root"))
    return team_dict


def _decrypt_mcp_env_in_agent(agent: dict | None) -> None:
    if not agent:
        return
    for server in agent.get("mcp_servers", []):
        env = server.get("env")
        if env and isinstance(env, dict) and env.get("_encrypted"):
            plaintext = get_crypto().decrypt(env["_encrypted"])
            try:
                server["env"] = json.loads(plaintext)
            except (json.JSONDecodeError, TypeError):
                server["env"] = {}
    for child in agent.get("children", []):
        if child.get("_type") == "TeamRef":
            for server in child.get("mcp_overrides", []):
                env = server.get("env")
                if env and isinstance(env, dict) and env.get("_encrypted"):
                    plaintext = get_crypto().decrypt(env["_encrypted"])
                    try:
                        server["env"] = json.loads(plaintext)
                    except (json.JSONDecodeError, TypeError):
                        server["env"] = {}
        else:
            _decrypt_mcp_env_in_agent(child)


class TeamRepo(BaseSqliteRepo):
    """teams 表的读写。

    当与 SqliteSaver / RunRepo / AuditRepo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问。

    凭证安全(P-A1):mcp_servers[].env 在入库前 AES-GCM 加密,
    读取时自动解密。未配置 AGENTTEAM_SECRET_KEY 时退化为明文(开发态兼容)。
    """

    def upsert(self, team: Team) -> None:
        """INSERT OR REPLACE,序列化为 JSON(含 MCP env 加密)。"""
        team_dict = team_to_dict(team)
        _encrypt_mcp_env(team_dict)
        config = json.dumps(team_dict, ensure_ascii=False)
        now = _now()
        self._execute(
            "INSERT INTO teams (name, description, config, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "description=excluded.description, config=excluded.config, updated_at=excluded.updated_at",
            (team.name, team.description, config, now, now),
        )

    def get(self, name: str) -> Team | None:
        """SELECT config,反序列化为 Team(含 MCP env 解密)。"""
        row = self._fetchone("SELECT config FROM teams WHERE name = ?", (name,))
        if row is None:
            return None
        team_dict = json.loads(row["config"])
        _decrypt_mcp_env(team_dict)
        return team_from_dict(team_dict)

    def list_all(self) -> list[Team]:
        """SELECT all,反序列化为 Team 列表(含 MCP env 解密)。"""
        rows = self._fetchall("SELECT config FROM teams ORDER BY name")
        result: list[Team] = []
        for r in rows:
            team_dict = json.loads(r["config"])
            _decrypt_mcp_env(team_dict)
            result.append(team_from_dict(team_dict))
        return result

    def delete(self, name: str) -> bool:
        """DELETE,返回是否删除成功。"""
        cur = self._execute("DELETE FROM teams WHERE name = ?", (name,))
        return cur.rowcount > 0
