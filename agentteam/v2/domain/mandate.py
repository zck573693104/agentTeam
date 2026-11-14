"""Mandate 授权模型(移植自 v1 domain/mandate.py,策略版本改按 Worker Pool 配置)。

dsh 语义:授权与执行分离 —— basis(派活依据) + scope(范围) + policy_version(规则版本),
可撤回、可审计;worker 范围放大 = failure(由 session 结果判定,不做运行时沙箱)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

_MAX_FIELD_CHARS = 4000
_VALID_BASIS = {"explicit", "inferred"}


@dataclass
class Mandate:
    """授权凭据:Ticket 携带,向下只读继承(worker 不放大范围)。"""

    basis: str            # explicit(人经 API 提交) | inferred(Source 入站研判立项)
    submitted_by: str = "api"
    policy_version: str = ""
    scope: str | None = None

    def __post_init__(self) -> None:
        if self.basis not in _VALID_BASIS:
            raise ValueError(f"mandate.basis 必须是 {_VALID_BASIS},得到 {self.basis!r}")
        self.submitted_by = (self.submitted_by or "api")[:_MAX_FIELD_CHARS]
        self.scope = self.scope[:_MAX_FIELD_CHARS] if self.scope else None

    def to_dict(self) -> dict:
        return {
            "basis": self.basis,
            "submitted_by": self.submitted_by,
            "policy_version": self.policy_version,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, d: dict | str | None) -> "Mandate | None":
        """宽容反序列化:None/空 → None;dict 或 JSON 字符串均可。"""
        if d is None:
            return None
        if isinstance(d, str):
            d = json.loads(d)
        return cls(
            basis=d["basis"],
            submitted_by=d.get("submitted_by", "api"),
            policy_version=d.get("policy_version", ""),
            scope=d.get("scope"),
        )


def policy_version_for_pool(workers: list[dict]) -> str:
    """Worker Pool 配置 → 授权规则版本(sha256 前 16 位)。

    配置(成员 prompt/工具/审批目标)热更新 → policy_version 变更 →
    在途 Ticket 记「授权依据已过期」注记(不中断,对齐 dsh)。
    """
    canonical = json.dumps(workers, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
