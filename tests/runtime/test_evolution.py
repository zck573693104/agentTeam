"""SP7b EvolutionEngine 测试。"""
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.runtime.evolution import EvolutionEngine, EvolutionResult


def _make_engine(tmp_path=None, skill_loader=None, skills_dir=None):
    """构造测试用 EvolutionEngine,所有 repo 用 MagicMock。"""
    return EvolutionEngine(
        model_provider=MagicMock(),
        agent_library=MagicMock(),
        evolution_repo=MagicMock(),
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
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
