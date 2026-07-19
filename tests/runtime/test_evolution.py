"""SP7b EvolutionEngine 测试。"""
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelRef
from agentteam.runtime.evolution import EvolutionEngine, EvolutionResult


# 测试用 default_model(C1 修复后 EvolutionEngine 必须传入 ModelRef)
_DEFAULT_MODEL = ModelRef(provider="qwen", name="qwen-max")


def _make_engine(tmp_path=None, skill_loader=None, skills_dir=None):
    """构造测试用 EvolutionEngine,所有 repo 用 MagicMock。"""
    return EvolutionEngine(
        model_provider=MagicMock(),
        agent_library=MagicMock(),
        evolution_repo=MagicMock(),
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        default_model=_DEFAULT_MODEL,
        skill_loader=skill_loader,
        skills_dir=skills_dir,
    )


def test_evolution_result_dataclass():
    """EvolutionResult 数据类字段。"""
    r = EvolutionResult(success=True, dimension="prompt", reason="ok")
    assert r.success is True
    assert r.dimension == "prompt"
    assert r.error is None


def test_trigger_unknown_run_does_nothing(tmp_path):
    """trigger 未知 run_id(get_run 返回 None):不调用任何进化。"""
    engine = _make_engine()
    engine._run_repo.get_run.return_value = None
    engine.trigger("nonexistent-run")
    # 不应访问 audit_repo.list_events
    engine._audit.list_events.assert_not_called()


def test_trigger_no_agents_in_trace_does_nothing():
    """trace 中无 agent(空 trace):不调用 _evolve_agent。"""
    engine = _make_engine()
    engine._run_repo.get_run.return_value = {"status": "completed"}
    engine._audit.list_events.return_value = []  # 空 trace
    engine.trigger("r1")
    engine._agent_library.get.assert_not_called()


def test_collect_agents_from_trace_extracts_unique_names():
    """_collect_agents_from_trace 从 worker_start / leader_plan 事件提取 agent 名(去重)。"""
    engine = _make_engine()
    trace = [
        {"event_type": "worker_start", "actor": "coder"},
        {"event_type": "worker_end", "actor": "coder"},
        {"event_type": "leader_plan", "actor": "ceo"},
        {"event_type": "worker_start", "actor": "reviewer"},
        {"event_type": "tool_call", "actor": "system"},
    ]
    agents = engine._collect_agents_from_trace(trace)
    assert set(agents) == {"coder", "ceo", "reviewer"}


def test_collect_agents_from_trace_handles_sqlite3_row():
    """_load_trace 将生产 sqlite3.Row 转为 dict,_collect_agents_from_trace 能正确处理。

    回归测试:修复前 isinstance(ev, dict) 守卫会跳过所有 Row,导致生产环境
    _collect_agents_from_trace 恒返回 []。
    """
    engine = _make_engine()
    # 模拟生产 AuditRepo.list_events 返回 sqlite3.Row
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events (event_type TEXT, actor TEXT)")
    conn.execute("INSERT INTO events VALUES ('worker_start', 'coder')")
    conn.execute("INSERT INTO events VALUES ('tool_call', 'system')")
    conn.execute("INSERT INTO events VALUES ('leader_plan', 'ceo')")
    conn.commit()
    rows = conn.execute("SELECT * FROM events").fetchall()
    engine._audit.list_events.return_value = rows

    trace = engine._load_trace("r1")
    # 验证 trace 已转为 dict
    assert all(isinstance(ev, dict) for ev in trace)
    agents = engine._collect_agents_from_trace(trace)
    assert set(agents) == {"coder", "ceo"}
    conn.close()


def test_evolve_agent_debounce_blocks_within_5_minutes():
    """防抖:5 分钟内同 agent 不重复触发。

    用 _agent_library.get.call_count 验证(防抖通过后会调到 get,
    防抖拦截则不会到达 get)。
    """
    engine = _make_engine()
    engine._agent_library.get.return_value = Agent(name="coder", role="worker", version=1)
    trace = []
    # 第一次触发:防抖通过,到达 _agent_library.get
    engine._evolve_agent("coder", "r1", trace)
    assert engine._agent_library.get.call_count == 1
    # 第二次触发:应被防抖拦截,未到达 _agent_library.get
    engine._evolve_agent("coder", "r2", trace)
    assert engine._agent_library.get.call_count == 1


def test_evolve_agent_debounce_allows_after_5_minutes(monkeypatch):
    """防抖:5 分钟后允许再次触发。"""
    engine = _make_engine()
    engine._agent_library.get.return_value = Agent(name="coder", role="worker", version=1)

    # mock time.time():第一次调用返回 1000,第二次返回 1000 + 301
    fake_time = [1000]
    def mock_time():
        return fake_time[0]
    monkeypatch.setattr("agentteam.runtime.evolution.time.time", mock_time)

    trace = []
    engine._evolve_agent("coder", "r1", trace)
    # 推进时间到 5 分钟后
    fake_time[0] = 1000 + 301
    engine._evolve_agent("coder", "r2", trace)
    # 第二次应执行(防抖通过)
    # 验证:_agent_library.get 被调用 2 次
    assert engine._agent_library.get.call_count == 2


def test_evolve_agent_unknown_agent_does_nothing():
    """_evolve_agent 未知 agent(library.get 返回 None):不抛异常。"""
    engine = _make_engine()
    engine._agent_library.get.return_value = None
    engine._evolve_agent("nonexistent", "r1", [])
    engine._evolution_repo.add_record.assert_not_called()


def test_evolve_agent_version_increments_on_success():
    """任一维度成功 → Agent.version += 1。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    engine._agent_library.get.return_value = agent

    # mock 4 维度:1 个成功,3 个跳过
    engine._optimize_prompt = MagicMock(return_value=EvolutionResult(True, "prompt", "ok"))
    engine._tune_params = MagicMock(return_value=EvolutionResult(True, "params", "skip"))
    engine._generate_skill = MagicMock(return_value=EvolutionResult(True, "skill_gen", "skip"))
    engine._select_skills = MagicMock(return_value=EvolutionResult(True, "skill_select", "skip"))

    engine._evolve_agent("coder", "r1", [])
    engine._agent_library.update_version.assert_called_once_with("coder", 2)


def test_evolve_agent_version_not_incremented_on_all_fail():
    """4 维度全部失败 → version 不递增。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    engine._agent_library.get.return_value = agent

    engine._optimize_prompt = MagicMock(return_value=EvolutionResult(False, "prompt", "err", "x"))
    engine._tune_params = MagicMock(return_value=EvolutionResult(False, "params", "err", "x"))
    engine._generate_skill = MagicMock(return_value=EvolutionResult(False, "skill_gen", "err", "x"))
    engine._select_skills = MagicMock(return_value=EvolutionResult(False, "skill_select", "err", "x"))

    engine._evolve_agent("coder", "r1", [])
    engine._agent_library.update_version.assert_not_called()


from agentteam.runtime.evolution import (
    _summarize_trace, _is_successful_run, _extract_task,
    _extract_tool_calls, _extract_final_answer, _compute_diff,
    _compute_stats, _parse_prompt, _parse_params, _parse_skill_list,
    _parse_skill_response,
)


def test_summarize_trace_includes_key_events():
    """_summarize_trace 包含 worker_start/tool_call/error/worker_end 等关键事件。"""
    trace = [
        {"event_type": "run_start", "actor": "system"},
        {"event_type": "worker_start", "actor": "coder"},
        {"event_type": "tool_call", "actor": "coder", "payload": {"tool": "read_file"}},
        {"event_type": "worker_end", "actor": "coder"},
        {"event_type": "run_end", "actor": "system"},
    ]
    summary = _summarize_trace(trace)
    assert "worker_start" in summary
    assert "tool_call" in summary
    assert "coder" in summary


def test_is_successful_run_true_when_run_end_no_error():
    """run_end 存在且无 error 事件 → 成功。"""
    trace = [
        {"event_type": "worker_start"},
        {"event_type": "run_end"},
    ]
    assert _is_successful_run(trace) is True


def test_is_successful_run_false_when_error_event():
    """有 error 事件 → 失败。"""
    trace = [
        {"event_type": "error", "payload": {"error": "x"}},
        {"event_type": "run_end"},
    ]
    assert _is_successful_run(trace) is False


def test_is_successful_run_false_when_no_run_end():
    """无 run_end → 失败(被 cancel 或中断)。"""
    trace = [{"event_type": "worker_start"}]
    assert _is_successful_run(trace) is False


def test_extract_task_from_run_start_payload():
    """_extract_task 从 run_start 事件的 payload 提取 task。"""
    trace = [
        {"event_type": "run_start", "payload": {"task": "审查代码"}},
        {"event_type": "worker_start"},
    ]
    assert _extract_task(trace) == "审查代码"


def test_extract_task_returns_empty_when_no_run_start():
    """无 run_start → 返回空字符串。"""
    trace = [{"event_type": "worker_start"}]
    assert _extract_task(trace) == ""


def test_extract_tool_calls_returns_tool_names():
    """_extract_tool_calls 返回所有 tool_call 事件的 tool 名列表。"""
    trace = [
        {"event_type": "tool_call", "payload": {"tool": "read_file", "args": {}}},
        {"event_type": "tool_call", "payload": {"tool": "write_file", "args": {}}},
        {"event_type": "worker_end"},
    ]
    tools = _extract_tool_calls(trace)
    assert tools == ["read_file", "write_file"]


def test_extract_final_answer_from_worker_end_payload():
    """_extract_final_answer 从 worker_end 事件的 payload 提取 answer。"""
    trace = [
        {"event_type": "worker_end", "payload": {"answer": "最终答案"}},
    ]
    assert _extract_final_answer(trace) == "最终答案"


def test_compute_diff_shows_changes():
    """_compute_diff 返回 unified diff 文本(含 - / + 标记)。"""
    old = "line1\nline2\nline3"
    new = "line1\nline2-modified\nline3"
    diff = _compute_diff(old, new)
    assert "-line2" in diff
    assert "+line2-modified" in diff


def test_compute_stats_empty_history_returns_empty_dict():
    """空历史 → _compute_stats 返回空 dict。"""
    assert _compute_stats([]) == {}


def test_compute_stats_with_history():
    """有历史 → 返回 record_count / success_rate / has_params_dimension 三个字段。"""
    history = [
        {"dimension": "params", "success": 1},
        {"dimension": "params", "success": 1},
        {"dimension": "prompt", "success": 0},
    ]
    stats = _compute_stats(history)
    assert stats == {
        "record_count": 3,
        "success_rate": 2 / 3,
        "has_params_dimension": True,
    }


def test_compute_stats_no_params_dimension():
    """history 中无 params 维度 → has_params_dimension=False。"""
    history = [
        {"dimension": "prompt", "success": 1},
        {"dimension": "skill_gen", "success": 1},
    ]
    stats = _compute_stats(history)
    assert stats["has_params_dimension"] is False
    assert stats["record_count"] == 2
    assert stats["success_rate"] == 1.0


def test_parse_prompt_extracts_from_code_block():
    """_parse_prompt 从 ```...``` 代码块提取 prompt。"""
    response = "分析:\n```\nYou are a better coder.\n```\n结束"
    prompt = _parse_prompt(response)
    assert "better coder" in prompt


def test_parse_prompt_returns_original_if_no_code_block():
    """无代码块 → 返回原文(trim)。"""
    response = "You are a coder."
    assert _parse_prompt(response) == "You are a coder."


def test_parse_params_extracts_json():
    """_parse_params 从 JSON 代码块提取参数 dict。"""
    response = '建议:\n```json\n{"max_iterations": 15}\n```'
    params = _parse_params(response)
    assert params == {"max_iterations": 15}


def test_parse_params_invalid_json_returns_empty():
    """无效 JSON → 返回空 dict。"""
    response = "no json here"
    assert _parse_params(response) == {}


def test_parse_skill_list_extracts_names():
    """_parse_skill_list 从逗号分隔或 list 格式提取 skill 名。"""
    response = "推荐: code_review, testing"
    skills = _parse_skill_list(response)
    assert "code_review" in skills
    assert "testing" in skills


def test_parse_skill_list_extracts_single_skill():
    """单个 skill(整个 stripped 响应为一个 ASCII 标识符)→ 返回单元素 list。"""
    assert _parse_skill_list("code_review") == ["code_review"]


def test_parse_skill_list_extracts_json_array():
    """JSON 数组格式 → 提取数组元素。"""
    response = '推荐: ["code_review", "testing", "deploy"]'
    skills = _parse_skill_list(response)
    assert skills == ["code_review", "testing", "deploy"]


def test_parse_skill_list_empty_returns_empty():
    """无推荐 → 返回空 list。"""
    response = "no recommendation"
    assert _parse_skill_list(response) == []


def test_parse_skill_list_whitespace_returns_empty():
    """纯空白 → 返回空 list。"""
    assert _parse_skill_list("   ") == []
    assert _parse_skill_list("") == []


def test_parse_skill_response_extracts_name_and_content():
    """_parse_skill_response 从 markdown 代码块提取 skill 名 + 内容。"""
    response = """分析:
```markdown
# Skill: auto_retry_pattern
描述:错误重试 3 次
```
"""
    name, content = _parse_skill_response(response)
    assert name == "auto_retry_pattern"
    assert "# Skill: auto_retry_pattern" in content
    assert "错误重试 3 次" in content


def test_parse_skill_response_strips_md_suffix_from_name():
    """skill 名若包含 .md 后缀应被正则排除,避免后续 auto_x.md.md 双扩展。

    回归测试:Issue 5 — 旧正则 \\S+ 会捕获 "auto_pattern.md",
    导致 SkillGenerator 构造文件路径时产生 auto_pattern.md.md。
    """
    response = """```markdown
# Skill: auto_pattern.md
内容
```"""
    name, _ = _parse_skill_response(response)
    # [\w-]+ 不匹配 . , 所以 .md 后缀被排除
    assert name == "auto_pattern"
    assert ".md" not in name


def test_parse_skill_response_no_code_block_returns_original():
    """无代码块 → 内容为原文 trim,skill 名从 # Skill: 行提取。"""
    response = "# Skill: auto_simple\n描述"
    name, content = _parse_skill_response(response)
    assert name == "auto_simple"
    assert content == response.strip()


def test_parse_skill_response_no_skill_header_returns_default_name():
    """无 # Skill: 行 → skill_name 为 auto_unknown。"""
    response = """```
只有内容,没有 # Skill: 标注
```"""
    name, content = _parse_skill_response(response)
    assert name == "auto_unknown"
    assert "只有内容" in content


from langchain_core.messages import AIMessage


def test_optimize_prompt_no_change_skips_history():
    """LLM 返回相同 prompt → 不写 history,不更新 Agent。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="You are a coder.", version=1)
    trace = [{"event_type": "run_start", "payload": {"task": "x"}},
             {"event_type": "run_end"}]
    # mock LLM 返回相同 prompt
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nYou are a coder.\n```"
    )
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is True
    assert "no change" in result.reason.lower()
    engine._evolution_repo.add_record.assert_not_called()
    engine._agent_library.update_prompt.assert_not_called()


def test_optimize_prompt_change_writes_history_and_updates_agent():
    """LLM 返回新 prompt → 写 history + 更新 Agent。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="old prompt", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nnew prompt with detail\n```"
    )
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is True
    engine._evolution_repo.add_record.assert_called_once()
    call_kwargs = engine._evolution_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "prompt"
    assert call_kwargs.kwargs["before_value"] == "old prompt"
    assert call_kwargs.kwargs["after_value"] == "new prompt with detail"
    assert call_kwargs.kwargs["success"] is True
    engine._agent_library.update_prompt.assert_called_once_with("coder", "new prompt with detail")


def test_optimize_prompt_llm_failure_records_error():
    """LLM 调用失败 → 写 success=False 的 history(保留 old_prompt 上下文),不更新 Agent。

    I3 强化:不再用 `or` tautology,改为精确断言 error / reason / before_value。
    I1 强化:验证 except 分支的 before_value 保留 old_prompt 上下文。
    """
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="original prompt", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("LLM timeout")
    result = engine._optimize_prompt(agent, trace, "r1")
    assert result.success is False
    # I3: 精确断言 error 字段,不再用恒真的 or
    assert result.error == "LLM timeout"
    assert result.reason == "error"  # EvolutionResult.reason 是简短标签
    engine._evolution_repo.add_record.assert_called_once()
    call_args = engine._evolution_repo.add_record.call_args.kwargs
    assert call_args["success"] is False
    assert call_args["error"] == "LLM timeout"
    # add_record 的 reason 字段含完整错误信息
    assert call_args["reason"] == "error: LLM timeout"
    # I1: before_value 应保留 old_prompt 上下文,不再是空串
    assert call_args["before_value"] == "original prompt"
    assert call_args["after_value"] == ""
    engine._agent_library.update_prompt.assert_not_called()


def test_optimize_prompt_uses_agent_model_when_present():
    """agent.model 不为 None → get_llm 用 agent.model(C1 回归测试)。"""
    engine = _make_engine()
    agent_model = ModelRef(provider="openai", name="gpt-4")
    agent = Agent(name="coder", role="worker", system_prompt="p", version=1, model=agent_model)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nnew\n```"
    )
    engine._optimize_prompt(agent, trace, "r1")
    # 验证 get_llm 用的是 agent.model,而非 default_model
    engine._mp.get_llm.assert_called_once_with(agent_model)


def test_optimize_prompt_falls_back_to_default_model_when_agent_model_is_none():
    """agent.model 为 None → get_llm fallback 到 default_model(C1 回归测试)。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", system_prompt="p", version=1, model=None)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```\nnew\n```"
    )
    engine._optimize_prompt(agent, trace, "r1")
    # 验证 get_llm fallback 到 default_model
    engine._mp.get_llm.assert_called_once_with(_DEFAULT_MODEL)


def test_tune_params_no_change_skips_history():
    """LLM 返回与当前相同参数 → 不写 history,不更新 Agent。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    # LLM 返回的 max_iterations 与当前相同 → no change
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 5}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is True
    assert "no change" in result.reason.lower()
    engine._evolution_repo.add_record.assert_not_called()
    engine._agent_library.update_params.assert_not_called()


def test_tune_params_change_writes_history_and_updates_agent():
    """LLM 返回新参数 → 写 history + 更新 Agent。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 15}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is True
    engine._evolution_repo.add_record.assert_called_once()
    call_kwargs = engine._evolution_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "params"
    assert call_kwargs.kwargs["success"] is True
    engine._agent_library.update_params.assert_called_once()
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[0] == "coder"
    assert update_args.args[1]["max_iterations"] == 15


def test_tune_params_clamps_max_iterations_to_range():
    """max_iterations 边界保护:LLM 返回 100 → clamp 到 20。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 100}\n```'
    )
    engine._tune_params(agent, trace, "r1")
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[1]["max_iterations"] == 20


def test_tune_params_clamps_max_iterations_to_min_1():
    """max_iterations 边界保护:LLM 返回 0 → clamp 到 1。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 0}\n```'
    )
    engine._tune_params(agent, trace, "r1")
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[1]["max_iterations"] == 1


def test_tune_params_llm_failure_records_error():
    """LLM 失败 → 写 success=False history(保留 old_params 上下文),不更新 Agent。

    I3 强化:不再用 or tautology,改为精确断言。
    I1 强化:验证 except 分支 before_value 保留 old_params 上下文。
    """
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("fail")
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is False
    assert result.error == "fail"
    assert result.reason == "error"
    engine._evolution_repo.add_record.assert_called_once()
    call_args = engine._evolution_repo.add_record.call_args.kwargs
    assert call_args["success"] is False
    assert call_args["error"] == "fail"
    assert call_args["reason"] == "error: fail"
    # I1: before_value 应保留 old_params 上下文(含 max_iterations=5)
    assert call_args["before_value"] != ""
    assert "5" in call_args["before_value"]
    engine._agent_library.update_params.assert_not_called()


def test_tune_params_strips_approval_policy_to_avoid_type_confusion():
    """Issue 1 回归测试:LLM 返回 approval_policy 字段应被 strip,避免 dict 替换 frozen dataclass。

    不带 approval_policy 字段时 → 只应用 max_iterations,类型保持 ApprovalPolicy。
    """
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    # LLM 同时返回 max_iterations 和 approval_policy
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 10, "approval_policy": {"level": "tool"}}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    assert result.success is True
    # 验证 update_params 收到的 dict 不含 approval_policy(被 strip)
    update_args = engine._agent_library.update_params.call_args
    assert "approval_policy" not in update_args.args[1]
    assert update_args.args[1]["max_iterations"] == 10
    # 验证 history 的 after_value 也不含 approval_policy
    call_kwargs = engine._evolution_repo.add_record.call_args.kwargs
    assert "approval_policy" not in call_kwargs["after_value"]


def test_tune_params_non_numeric_max_iterations_skips_gracefully():
    """Issue 3 回归测试:LLM 返回非数字 max_iterations → 跳过(no-change),不写 error history。

    非数字不应走 except 分支被记录为 error(那会污染 error 统计),
    而应走 no-change 路径(reason 含 'skip')。
    """
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    # LLM 返回字符串 "abc" 作为 max_iterations
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": "abc"}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    # 走 no-change 路径,不是 error
    assert result.success is True
    assert "skip" in result.reason.lower()
    # 不写 history(避免污染 error 统计)
    engine._evolution_repo.add_record.assert_not_called()
    engine._agent_library.update_params.assert_not_called()


def test_tune_params_float_max_iterations_truncates_to_int():
    """Issue 3 边界:LLM 返回 5.5 → int(float(5.5)) 截断为 5,与当前相同 → no-change。"""
    from agentteam.domain.approval import ApprovalPolicy
    engine = _make_engine()
    agent = Agent(
        name="coder", role="worker",
        max_iterations=5, approval_policy=ApprovalPolicy(level="worker"),
        version=1,
    )
    trace = [{"event_type": "run_end"}]
    engine._evolution_repo.list_recent_runs.return_value = []
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content='```json\n{"max_iterations": 10.7}\n```'
    )
    result = engine._tune_params(agent, trace, "r1")
    # 10.7 截断为 10,与当前 5 不同 → 有变化
    assert result.success is True
    update_args = engine._agent_library.update_params.call_args
    assert update_args.args[1]["max_iterations"] == 10


def test_generate_skill_skips_failed_run():
    """run 失败 → 跳过(不调用 LLM)。"""
    engine = _make_engine()
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "error", "payload": {"error": "x"}}]  # 无 run_end
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "skip" in result.reason.lower() or "failed" in result.reason.lower()
    engine._mp.get_llm.assert_not_called()


def test_generate_skill_no_skills_dir_skips():
    """skills_dir=None → 跳过。"""
    engine = _make_engine(skills_dir=None)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "no skills_dir" in result.reason.lower() or "skip" in result.reason.lower()


def test_generate_skill_llm_returns_skip():
    """LLM 返回 SKIP → 不生成文件。"""
    engine = _make_engine(skills_dir=MagicMock())
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(content="SKIP")
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    assert "no reusable" in result.reason.lower() or "skip" in result.reason.lower()


def test_generate_skill_creates_file_and_notifies_loader(tmp_path):
    """LLM 返回 skill → 写入 auto_*.md + 通知 SkillLoader.reload + 写 history。"""
    mock_loader = MagicMock()
    engine = _make_engine(skill_loader=mock_loader, skills_dir=tmp_path)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```markdown\n# Skill: auto_pattern1\ndo x then y\n```"
    )
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is True
    # 文件已创建
    skill_path = tmp_path / "auto_pattern1.md"
    assert skill_path.exists()
    assert "auto_pattern1" in skill_path.read_text(encoding="utf-8")
    # SkillLoader.reload 被调用
    mock_loader.reload.assert_called_once()
    # history 写入
    engine._evolution_repo.add_record.assert_called_once()
    call_kwargs = engine._evolution_repo.add_record.call_args
    assert call_kwargs.kwargs["dimension"] == "skill_gen"
    assert call_kwargs.kwargs["success"] is True


def test_generate_skill_existing_file_appends_version(tmp_path):
    """auto_X.md 已存在 → 写 auto_X_v2.md。"""
    # 预创建 auto_pattern1.md
    (tmp_path / "auto_pattern1.md").write_text("existing", encoding="utf-8")
    mock_loader = MagicMock()
    engine = _make_engine(skill_loader=mock_loader, skills_dir=tmp_path)
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.return_value = AIMessage(
        content="```markdown\n# Skill: auto_pattern1\nnew content\n```"
    )
    engine._generate_skill(agent, trace, "r1")
    # 应写入 auto_pattern1_v2.md(不覆盖原文件)
    assert (tmp_path / "auto_pattern1_v2.md").exists()
    assert (tmp_path / "auto_pattern1.md").read_text(encoding="utf-8") == "existing"


def test_generate_skill_llm_failure_records_error():
    """LLM 失败 → 写 success=False history。"""
    engine = _make_engine(skills_dir=MagicMock())
    agent = Agent(name="coder", role="worker", version=1)
    trace = [{"event_type": "run_end"}]
    engine._mp.get_llm.return_value.invoke.side_effect = RuntimeError("fail")
    result = engine._generate_skill(agent, trace, "r1")
    assert result.success is False
    engine._evolution_repo.add_record.assert_called_once()
    call_kwargs = engine._evolution_repo.add_record.call_args
    assert call_kwargs.kwargs["success"] is False
