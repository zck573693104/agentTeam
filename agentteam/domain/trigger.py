"""TriggerRule:事件驱动触发规则(对标 DeepSeek Super-agent 的 Trigger 设计)。

一条规则描述「什么环境事件 → 触发哪个团队的 run」:
- Fast Path(确定性条件):conditions 里的规则全部命中才触发,程序求值零成本
- Slow Path(LLM 语义判断):Fast Path 未命中时,若规则开启 slow_path,
  交给 LLM 按自然语言策略判断是否行动(需模型 Key;无 Key 静默跳过)
- Action:命中后按 task_from(事件字段路径)或 task_template(模板)生成任务文本,
  在 team_name 团队上创建 run,triggered_by_user 记 "trigger:<规则名>"

两级过滤思想出自 DeepSeek《Super-agent 设计探索》第 3.1 节:
高噪音低触发率的事件流先用确定性代码(Fast Path)筛除,
只有需要语义理解的候选才走模型(Slow Path)。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# 允许的条件算子(全部对 event 的 dot-path 取值后比较)
_ALLOWED_OPS = {"eq", "ne", "contains", "in", "exists", "regex"}

# 模板占位符/任务文本的单次替换与总长上限,防止外部事件把任务撑爆
_SUB_VALUE_MAX = 2000
_TASK_MAX_CHARS = 4000

_SECRET_MASK = "****"


def resolve_path(obj: Any, path: str) -> tuple[bool, Any]:
    """按点路径取嵌套字段。返回 (存在, 值)。路径段只支持 dict 键导航。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


@dataclass
class TriggerRule:
    """一条入站事件触发规则。"""

    name: str
    team_name: str
    source: str = "webhook"
    secret: str | None = None          # 入站校验(X-Trigger-Secret);None 不校验
    conditions: list[dict] = field(default_factory=list)  # Fast Path,AND 语义
    task_from: str | None = None       # 事件里的任务文本路径(如 payload.text)
    task_template: str | None = None   # 任务模板,{a.b.c} 占位符做事件字段替换
    slow_path: dict | None = None      # {"enabled": bool, "prompt": str}
    enabled: bool = True

    # ---------- 校验与序列化 ----------

    @staticmethod
    def from_dict(d: dict) -> "TriggerRule":
        if not isinstance(d, dict):
            raise ValueError("trigger rule must be a JSON object")
        name = d.get("name")
        team_name = d.get("team_name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("'name' must be a non-empty string")
        if not isinstance(team_name, str) or not team_name.strip():
            raise ValueError("'team_name' must be a non-empty string")
        conditions = d.get("conditions", [])
        if not isinstance(conditions, list):
            raise ValueError("'conditions' must be a list")
        for cond in conditions:
            if not isinstance(cond, dict):
                raise ValueError("each condition must be an object")
            if not isinstance(cond.get("field"), str) or not cond["field"]:
                raise ValueError("condition.field must be a non-empty dot path")
            if cond.get("op") not in _ALLOWED_OPS:
                raise ValueError(
                    f"condition.op must be one of {sorted(_ALLOWED_OPS)}: {cond.get('op')!r}"
                )
        task_from = d.get("task_from")
        task_template = d.get("task_template")
        if task_from is not None and not isinstance(task_from, str):
            raise ValueError("'task_from' must be a dot path string")
        if task_template is not None and not isinstance(task_template, str):
            raise ValueError("'task_template' must be a string")
        if task_from is None and task_template is None:
            raise ValueError("one of 'task_from' / 'task_template' is required")
        slow_path = d.get("slow_path")
        if slow_path is not None:
            if not isinstance(slow_path, dict):
                raise ValueError("'slow_path' must be an object")
            if not isinstance(slow_path.get("enabled"), bool):
                raise ValueError("slow_path.enabled must be a boolean")
            if slow_path.get("prompt") is not None and not isinstance(
                slow_path.get("prompt"), str
            ):
                raise ValueError("slow_path.prompt must be a string")
        return TriggerRule(
            name=name.strip(),
            team_name=team_name.strip(),
            source=str(d.get("source") or "webhook"),
            secret=d.get("secret"),
            conditions=conditions,
            task_from=task_from,
            task_template=task_template,
            slow_path=slow_path,
            enabled=bool(d.get("enabled", True)),
        )

    def to_dict(self, mask_secret: bool = False) -> dict:
        return {
            "name": self.name,
            "team_name": self.team_name,
            "source": self.source,
            "secret": _SECRET_MASK if (self.secret and mask_secret) else self.secret,
            "conditions": self.conditions,
            "task_from": self.task_from,
            "task_template": self.task_template,
            "slow_path": self.slow_path,
            "enabled": self.enabled,
        }

    # ---------- Fast Path 求值 ----------

    def matches_fast_path(self, event: dict) -> bool:
        """Fast Path:全部确定性条件命中(AND)才放行;空条件 = 全放行。"""
        for cond in self.conditions:
            exists, value = resolve_path(event, cond["field"])
            op = cond["op"]
            expected = cond.get("value")
            if op == "exists":
                if bool(exists) != bool(expected):
                    return False
                continue
            if not exists:
                return False
            try:
                if op == "eq" and not (value == expected):
                    return False
                if op == "ne" and not (value != expected):
                    return False
                if op == "contains" and str(expected) not in str(value):
                    return False
                if op == "in" and value not in (
                    expected if isinstance(expected, (list, tuple, set)) else [expected]
                ):
                    return False
                if op == "regex" and not re.search(str(expected), str(value)):
                    return False
            except Exception:
                return False
        return True

    # ---------- Slow Path 配置 ----------

    def slow_path_enabled(self) -> bool:
        return bool(self.slow_path and self.slow_path.get("enabled"))

    def slow_path_prompt(self) -> str:
        return str((self.slow_path or {}).get("prompt") or "")

    # ---------- Action:任务文本生成 ----------

    def build_task(self, event: dict) -> str | None:
        """命中后生成任务文本。模板优先;字段替换失败/超限有兜底,不抛。"""
        if self.task_template:
            def _sub(match: re.Match) -> str:
                exists, value = resolve_path(event, match.group(1))
                if not exists:
                    return ""
                return str(value)[:_SUB_VALUE_MAX]

            try:
                task = re.sub(r"\{([\w][\w.\-]*)\}", _sub, self.task_template)
            except Exception:
                return None
            task = task.strip()
            return task[:_TASK_MAX_CHARS] or None
        if self.task_from:
            exists, value = resolve_path(event, self.task_from)
            if not exists or value is None:
                return None
            task = str(value).strip()
            return task[:_TASK_MAX_CHARS] or None
        return None


def rule_from_row(row: dict) -> TriggerRule:
    """triggers 表行 → TriggerRule(rules JSON 列损坏时抛 ValueError)。"""
    try:
        rules = json.loads(row["rules"] or "{}")
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"corrupt trigger rules JSON for '{row['name']}': {e}")
    return TriggerRule(
        name=row["name"],
        team_name=row["team_name"],
        source=row["source"] or "webhook",
        secret=row["secret"],
        conditions=rules.get("conditions", []),
        task_from=rules.get("task_from"),
        task_template=rules.get("task_template"),
        slow_path=rules.get("slow_path"),
        enabled=bool(rules.get("enabled", True)),
    )
