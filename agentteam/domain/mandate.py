"""Mandate:授权模型(spec SP12 §5,借鉴 dsh-superagent 的 Mandate 设计)。

授权与执行分离:每个 run 携带一份授权凭据(谁、依据什么授权规则版本、
允许做什么),向下只读继承——PlanStep/worker 不放大范围,
worker 越界 = failure 语义,leader_review 判定。

- basis:explicit(人经 API 提交)| inferred(事件 Trigger 命中规则触发)
- submitted_by:提交者身份(API 用户名 / "trigger:<规则名>" / 缺省 "api")
- policy_version:授权规则版本(首版 = 团队配置内容哈希)。团队配置热更新
  → 版本变更 → 进行中 run 记「授权依据已过期」注记(不中断运行,对齐 dsh
  「不撤销在途 Mandate、变更通知到在途工单」)
- scope:自然语言范围描述,可空

存储:runs.mandate_json(SP12 迁移 v16);审批历史 approvals.mandate_version
记录决策时的授权版本(审批门 = 授权管理:通过 = 授权延续,拒绝 = 授权终止)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

# 允许的授权依据:explicit 人经 API 提交;inferred 事件 Trigger 自动触发
_ALLOWED_BASES = ("explicit", "inferred")

# scope/policy_version 长度上限,防止外部输入把授权凭据撑爆
_MAX_FIELD_CHARS = 4000


@dataclass
class Mandate:
    """一个 run 的授权凭据(不可变语义:创建后不放大范围)。"""

    basis: str                 # explicit | inferred
    submitted_by: str          # 提交者身份
    policy_version: str        # 授权规则版本哈希
    scope: str | None = None   # 自然语言范围描述,可空

    def __post_init__(self) -> None:
        if self.basis not in _ALLOWED_BASES:
            raise ValueError(
                f"mandate.basis must be one of {list(_ALLOWED_BASES)}: {self.basis!r}"
            )
        if not self.submitted_by:
            self.submitted_by = "api"
        if not self.policy_version:
            raise ValueError("mandate.policy_version must be non-empty")
        for name in ("submitted_by", "policy_version", "scope"):
            value = getattr(self, name)
            if value is not None and len(value) > _MAX_FIELD_CHARS:
                setattr(self, name, value[:_MAX_FIELD_CHARS])

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "basis": self.basis,
            "submitted_by": self.submitted_by,
            "policy_version": self.policy_version,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, d: dict | str | None) -> "Mandate | None":
        """JSON → Mandate;旧数据无 mandate(None/空)返回 None 向后兼容。"""
        if d is None or d == "":
            return None
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"corrupt mandate JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError(f"mandate must be a JSON object: {d!r}")
        return cls(
            basis=str(d.get("basis") or "explicit"),
            submitted_by=str(d.get("submitted_by") or "api"),
            policy_version=str(d.get("policy_version") or ""),
            scope=d.get("scope"),
        )


def policy_version_for_team(team) -> str:
    """团队配置内容哈希 → 授权规则版本。

    覆盖审批策略与成员模型等全部团队配置(serializer 往返后的稳定序列);
    团队热更新后版本变化,进行中 run 据此记 policy 过期注记。
    """
    from agentteam.domain.serializer import team_to_dict

    blob = json.dumps(team_to_dict(team), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def default_mandate(team, task: str, triggered_by_user: str | None) -> Mandate:
    """按触发身份推断默认授权:Trigger 规则命中 = inferred,否则 explicit。

    triggered_by_user 约定(SP12 Trigger):"trigger:<规则名>" 为规则触发,
    其余(API 用户名 / None)为人工提交。
    """
    user = triggered_by_user or "api"
    basis = "inferred" if user.startswith("trigger:") else "explicit"
    return Mandate(
        basis=basis,
        submitted_by=user,
        policy_version=policy_version_for_team(team),
        scope=task or None,
    )
