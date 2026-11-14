"""Webhook 入站源(push 型;秘钥校验,身份分离)。"""
from __future__ import annotations

import hashlib

from agentteam.v2.sources.protocol import Trigger


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:32]


def verify_secret(config: dict, secret_hash: str | None, provided: str | None) -> bool:
    """webhook 秘钥校验:未配置秘钥 = 开放(开发态);配置了则必须匹配。"""
    if not secret_hash:
        return True
    if not provided:
        return False
    return hash_secret(provided) == secret_hash


class WebhookSource:
    """push 型源:外部系统 POST 触发。事件经 InboundEngine 处理。"""

    kind = "webhook"

    def __init__(self, name: str, config: dict) -> None:
        self.name = name
        self.config = config or {}

    @staticmethod
    def parse(body: dict) -> Trigger:
        return Trigger(
            actor=(body.get("actor") or "unknown").strip(),
            thread_key=(body.get("thread_key") or "").strip(),
            text=(body.get("text") or "").strip(),
            raw=body.get("raw") or {},
        )
