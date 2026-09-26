"""acceptance_criteria 机器验证 — Graph Engineering P1 落地。

文章原话:"测试真跑过、钱真到账、用户真留下"。意思是验收标准得能被代码兜底执行,
而不是再问一遍 LLM"你觉得合格吗"。

本模块把 acceptance_criteria 从纯 str(自然语言描述,只能喂 LLM)升级为
支持结构化形式,让机器先跑一遍,失败直接 reject,通过再走 LLM 做语义判断。

支持四种类型(用 dict 表达,type 字段区分):
    {"type": "contains", "field": "artifact", "pattern": "..."}
        子串包含:worker_output.<field> 包含 pattern 子串
    {"type": "regex", "field": "artifact", "pattern": "..."}
        正则匹配:re.search(pattern, worker_output.<field>) 非空
    {"type": "test", "command": "..."}
        shell 命令:returncode==0 视为通过(子进程隔离,超时 30s)
    {"type": "llm_judge", "prompt": "..."}
        LLM 语义判断:返回 None 表示"交给 LLM",由 leader_review 处理

兼容旧形式:acceptance_criteria 为 str 时,返回 None(走 LLM 判断,向后兼容)。

安全:command 在子进程跑,无 shell=True,无 cwd 注入;pattern 经 re.compile 异常捕获。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from agentteam.logging_config import get_logger
from agentteam.runtime.nodes import WorkerOutput

logger = get_logger("runtime.criteria")

# test 类型命令执行超时(秒)。LLM 产出验收不应阻塞太久。
_TEST_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class CriteriaVerdict:
    """机器验证结论。

    passed=True 表示机器判定通过(可跳过 LLM);
    passed=False 表示机器判定不通过(直接 reject);
    返回 None(而非 CriteriaVerdict)表示机器无结论,交给 LLM 判断。
    """

    passed: bool
    reason: str


def evaluate_criteria(
    criteria: Any, worker_output: WorkerOutput
) -> CriteriaVerdict | None:
    """评估 acceptance_criteria,返回机器验证结论。

    返回值:
        CriteriaVerdict(passed=True): 机器判定通过,跳过 LLM
        CriteriaVerdict(passed=False): 机器判定不通过,直接 reject
        None: 机器无结论(str 形式或 llm_judge 类型),交给 LLM
    """
    if criteria is None:
        return None

    # 兼容旧形式:纯 str → 交给 LLM
    if isinstance(criteria, str):
        return None

    # 新形式:dict 带 type 字段
    if not isinstance(criteria, dict):
        return CriteriaVerdict(
            passed=False,
            reason=f"acceptance_criteria 格式非法: {type(criteria).__name__},期望 str 或 dict",
        )

    crit_type = criteria.get("type")
    if crit_type is None:
        return CriteriaVerdict(
            passed=False,
            reason="acceptance_criteria dict 缺少 type 字段",
        )

    if crit_type == "contains":
        return _eval_contains(criteria, worker_output)
    if crit_type == "regex":
        return _eval_regex(criteria, worker_output)
    if crit_type == "test":
        return _eval_test(criteria, worker_output)
    if crit_type == "llm_judge":
        # 显式声明交给 LLM,返回 None
        return None

    return CriteriaVerdict(
        passed=False,
        reason=f"未知 acceptance_criteria type: {crit_type}",
    )


def _get_field(worker_output: WorkerOutput, field: str) -> str:
    """从 WorkerOutput 取字段值,失败返回空串。"""
    if field == "artifact":
        return worker_output.artifact or ""
    if field == "failure":
        return worker_output.failure or ""
    if field == "evidence":
        # evidence 是 list[str],拼成一段文本
        return "\n".join(worker_output.evidence) if worker_output.evidence else ""
    # state_delta 是 dict,转成 str
    if field == "state_delta":
        return str(worker_output.state_delta) if worker_output.state_delta else ""
    return ""


def _eval_contains(
    criteria: dict, worker_output: WorkerOutput
) -> CriteriaVerdict:
    """子串包含验证。"""
    field = criteria.get("field", "artifact")
    pattern = criteria.get("pattern", "")
    if not pattern:
        return CriteriaVerdict(passed=False, reason="contains 类型缺少 pattern")
    value = _get_field(worker_output, field)
    if pattern in value:
        return CriteriaVerdict(
            passed=True,
            reason=f"字段 {field} 包含 {pattern!r}",
        )
    return CriteriaVerdict(
        passed=False,
        reason=f"字段 {field} 不包含 {pattern!r}(实际值: {value[:100]!r})",
    )


def _eval_regex(
    criteria: dict, worker_output: WorkerOutput
) -> CriteriaVerdict:
    """正则匹配验证。"""
    field = criteria.get("field", "artifact")
    pattern = criteria.get("pattern", "")
    if not pattern:
        return CriteriaVerdict(passed=False, reason="regex 类型缺少 pattern")
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return CriteriaVerdict(
            passed=False,
            reason=f"正则编译失败: {e}",
        )
    value = _get_field(worker_output, field)
    if regex.search(value):
        return CriteriaVerdict(
            passed=True,
            reason=f"字段 {field} 匹配正则 {pattern!r}",
        )
    return CriteriaVerdict(
        passed=False,
        reason=f"字段 {field} 不匹配正则 {pattern!r}",
    )


def _eval_test(
    criteria: dict, worker_output: WorkerOutput
) -> CriteriaVerdict:
    """shell 命令验证:returncode==0 视为通过。

    安全:
    - 不用 shell=True,用 shlex.split + subprocess.run
    - 超时 30s,超时视为失败
    - artifact 通过环境变量 AGENTTEAM_ARTIFACT 传入命令,避免命令注入
    """
    import os
    import shlex

    command = criteria.get("command", "")
    if not command:
        return CriteriaVerdict(passed=False, reason="test 类型缺少 command")

    env = os.environ.copy()
    env["AGENTTEAM_ARTIFACT"] = worker_output.artifact or ""
    env["AGENTTEAM_FAILURE"] = worker_output.failure or ""

    try:
        # shlex.split 避免 shell=True 的注入风险
        args = shlex.split(command)
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return CriteriaVerdict(
            passed=False,
            reason=f"命令执行超时(>{_TEST_TIMEOUT_SECONDS}s): {command}",
        )
    except FileNotFoundError as e:
        return CriteriaVerdict(
            passed=False,
            reason=f"命令不存在: {e}",
        )
    except Exception as e:
        return CriteriaVerdict(
            passed=False,
            reason=f"命令执行异常: {type(e).__name__}: {e}",
        )

    if result.returncode == 0:
        return CriteriaVerdict(
            passed=True,
            reason=f"命令退出码 0: {command}",
        )
    stderr_tail = (result.stderr or "")[-200:]
    return CriteriaVerdict(
        passed=False,
        reason=f"命令退出码 {result.returncode}: {command} stderr: {stderr_tail}",
    )
