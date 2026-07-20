"""凭证安全:对称加密 + 日志脱敏 + API mask。

对标阿里云 AgentTeams 的"凭证安全"特性:
- 架构级凭证隔离:Agent 不接触明文 API key(由 ModelProvider/BaseAdapter 内部读取)
- 管控面统一加密托管:MCP server env(可能含 GITHUB_PERSONAL_ACCESS_TOKEN 等)加密入库
- Anti-Log:日志/API 输出自动 mask 敏感字段

设计:
- AES-GCM 对称加密,主密钥从环境变量 AGENTTEAM_SECRET_KEY 读取(32 字节 hex/base64)
- 未配置主密钥时回退到"明文模式"(开发态),不破坏现有部署
- 加密 payload 自带 nonce + 版本前缀,可向前演进算法
- mask_secret() 同时用于日志过滤与 API 响应脱敏

典型用法:
    from agentteam.security.crypto import get_crypto, mask_secret
    enc = get_crypto().encrypt('{"TOKEN": "abc"}')  # str -> str
    dec = get_crypto().decrypt(enc)                  # str -> str
    mask_secret('token=abc123xyz')                   # -> 'token=***'
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import threading
from typing import Any

# 加密 payload 前缀,标识算法版本,便于未来升级(v2 可换 ChaCha20-Poly1305 等)
_CIPHER_VERSION = b"v1:"

# 敏感字段名模式(用于 dict 递归 mask):包含这些子串的 key 视为敏感
_SENSITIVE_KEY_PATTERNS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "credential", "private_key", "access_key",
)

# 敏感值模式(用于日志 message 脱敏)
# 1. key=value 形式: token=xxx / api_key=xxx / Authorization: Bearer xxx
# 2. 长度 ≥ 16 的 hex/base64 token(避免误杀短 id)
_VALUE_PATTERNS = [
    # key=value 形式(key 含 token/secret/password/api_key 等)
    re.compile(
        r"(?i)(token|secret|password|passwd|api_key|apikey|authorization|bearer|credential|access_key)[\s:=]+(\S{4,})",
    ),
    # GitHub PAT: ghp_xxx / github_pat_xxx
    re.compile(r"\b(ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b"),
    # OpenAI/Anthropic key 前缀
    re.compile(r"\b(sk-[A-Za-z0-9]{16,}|sk-ant-[A-Za-z0-9_\-]{16,})\b"),
    # DashScope: sk- 开头已被上面捕获,这里捕获 DASHSCOPE_API_KEY=xxx 形式
]


def _generate_default_key() -> bytes:
    """生成 32 字节随机密钥(开发态 fallback,进程重启后变化,加密数据不可解密)。

    生产环境必须显式设置 AGENTTEAM_SECRET_KEY,否则进程重启后历史加密数据无法解密。
    """
    return secrets.token_bytes(32)


class CryptoService:
    """AES-GCM 对称加密服务。

    线程安全:__init__ 一次性派生 key,encrypt/decrypt 无共享可变状态。
    """

    def __init__(self, key: bytes | None = None) -> None:
        # 关键:key=None 表示"未配置主密钥",此时退化为明文模式(enabled=False),
        # 不生成随机密钥(否则会误启用加密,且进程重启后历史数据无法解密)。
        if key is None:
            self._key = b""
            self._enabled = False
            return
        if len(key) not in (16, 24, 32):
            raise ValueError(
                f"AES key length must be 16/24/32 bytes, got {len(key)}"
            )
        self._key = key
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """是否启用加密(主密钥已显式配置)。

        False 时 encrypt/decrypt 退化为 identity,便于开发态零配置启动。
        """
        return self._enabled

    def encrypt(self, plaintext: str) -> str:
        """加密字符串,返回 base64(nonce + ciphertext + tag)。

        无主密钥(enabled=False)时直接返回明文,保持向后兼容。
        """
        if not self._enabled:
            return plaintext
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            # cryptography 未安装:退化为明文,记录一次警告(避免日志洪泛)
            return plaintext

        nonce = secrets.token_bytes(12)  # AES-GCM 推荐 96-bit nonce
        aesgcm = AESGCM(self._key)
        ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        payload = _CIPHER_VERSION + nonce + ct
        return base64.b64encode(payload).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """解密 encrypt() 的输出。

        无主密钥时直接返回输入。
        输入非本服务加密(无版本前缀/解密失败)时返回原值,保证向后兼容
        (旧库明文数据可正常读取)。
        """
        if not self._enabled:
            return ciphertext
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            return ciphertext

        try:
            payload = base64.b64decode(ciphertext)
        except Exception:
            return ciphertext  # 非 base64,认为是明文

        if not payload.startswith(_CIPHER_VERSION):
            return ciphertext  # 旧明文数据,原样返回

        payload = payload[len(_CIPHER_VERSION):]
        if len(payload) < 12 + 16:  # nonce + GCM tag
            return ciphertext
        nonce, ct = payload[:12], payload[12:]
        aesgcm = AESGCM(self._key)
        try:
            return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
        except Exception:
            # 主密钥不匹配或数据损坏:返回原值,调用方决定如何处理
            return ciphertext


_crypto_instance: CryptoService | None = None
_crypto_lock = threading.Lock()


def get_crypto() -> CryptoService:
    """获取全局 CryptoService 单例。

    主密钥来源(优先级):
    1. AGENTTEAM_SECRET_KEY 环境变量(hex 或 base64 编码的 16/24/32 字节)
    2. 未设置:生成临时密钥(enabled=False,加密退化为明文)

    生产部署强烈建议显式设置 AGENTTEAM_SECRET_KEY:
        # 生成 32 字节随机密钥
        python -c "import secrets; print(secrets.token_hex(32))"
        export AGENTTEAM_SECRET_KEY=<上面的输出>
    """
    global _crypto_instance
    if _crypto_instance is not None:
        return _crypto_instance
    with _crypto_lock:
        if _crypto_instance is not None:
            return _crypto_instance
        key_str = os.environ.get("AGENTTEAM_SECRET_KEY", "").strip()
        key: bytes | None = None
        if key_str:
            # 先尝试 hex,再尝试 base64,最后尝试 raw bytes
            try:
                key = bytes.fromhex(key_str)
            except ValueError:
                try:
                    key = base64.b64decode(key_str, validate=True)
                except Exception:
                    key = key_str.encode("utf-8")
            if len(key) not in (16, 24, 32):
                # 长度非法:fallback 到 None(明文模式)而非抛异常,
                # 避免配置错误导致服务无法启动
                key = None
        _crypto_instance = CryptoService(key)
    return _crypto_instance


def reset_crypto_for_testing() -> None:
    """测试用:重置全局 CryptoService 单例,下次 get_crypto() 重新读环境变量。"""
    global _crypto_instance
    with _crypto_lock:
        _crypto_instance = None


def mask_secret(value: str, visible_prefix: int = 0, visible_suffix: int = 0) -> str:
    """脱敏单个字符串值,保留首尾少量字符用于辨识。

    mask_secret('sk-abcdef1234567890', visible_prefix=3, visible_suffix=2)
        -> 'sk-***90'
    mask_secret('short')  # 短串直接返回 ***
        -> '***'
    """
    if not value:
        return value
    if len(value) <= visible_prefix + visible_suffix + 2:
        return "***"
    prefix = value[:visible_prefix] if visible_prefix > 0 else ""
    suffix = value[-visible_suffix:] if visible_suffix > 0 else ""
    return f"{prefix}***{suffix}"


def mask_secrets_in_text(text: str) -> str:
    """扫描文本中的敏感模式并脱敏,用于日志 message 过滤。

    覆盖:
    - key=value 形式(token=xxx / api_key=xxx / Authorization: Bearer xxx)
    - 已知厂商 key 前缀(ghp_ / github_pat_ / sk- / sk-ant-)
    """
    if not text:
        return text
    masked = text
    for pattern in _VALUE_PATTERNS:
        def _replace(match: re.Match) -> str:
            groups = match.groups()
            if len(groups) >= 2:
                # key=value 形式:保留 key,值脱敏
                return f"{groups[0]}={mask_secret(groups[1])}"
            # 纯值形式:整体脱敏
            return mask_secret(match.group(0))
        masked = pattern.sub(_replace, masked)
    return masked


def mask_secrets_in_dict(data: Any, _depth: int = 0) -> Any:
    """递归遍历 dict/list,对敏感 key 对应的值做脱敏,返回新对象。

    用于 API 响应序列化前过滤(如 GET /api/teams 返回时 mask 掉 mcp.env 中的 token)。

    敏感 key 判定:键名(转小写)包含 _SENSITIVE_KEY_PATTERNS 中任一子串。
    """
    if _depth > 20:  # 防御性深度上限,避免循环引用
        return data
    if isinstance(data, dict):
        result: dict = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(p in key_lower for p in _SENSITIVE_KEY_PATTERNS):
                # 敏感 key:值脱敏
                if isinstance(v, str):
                    result[k] = mask_secret(v)
                else:
                    result[k] = "***"
            else:
                result[k] = mask_secrets_in_dict(v, _depth + 1)
        return result
    if isinstance(data, list):
        return [mask_secrets_in_dict(item, _depth + 1) for item in data]
    return data
