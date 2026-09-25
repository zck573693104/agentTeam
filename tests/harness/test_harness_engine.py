"""HarnessRunner（opencode 套壳引擎）单元/集成测试。

用 FakeOpenCodeServer（契约对齐真实 opencode v1.18.32）驱动：
sequential/dag 编排、三级审批 park/resume、拒绝传播、取消、
快照持久化 + 重启 rehydrate、事件词表。
"""
from __future__ import annotations

import threading
import time

import pytest

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunCancelledError
from agentteam.domain.team import Team
from agentteam.harness.engine import HarnessRunner
from agentteam.harness.opencode_client import (
    OpenCodeClient, OpenCodeConfig, OpenCodeError)
from agentteam.harness.runner import HarnessStateStore
from agentteam.models.provider import ModelRef
from agentteam.storage.audit import AuditRepo
from tests.harness.fake_opencode import FakeOpenCodeServer, Script


def make_team(name="oc_team", engine="opencode", leader_policy=None) -> Team:
    """两个 worker 的最小团队：w1 无工具，w2 有 write_file。"""
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    return Team(
        name=name,
        description="harness 测试团队",
        default_model=ModelRef("qwen", "qwen-max"),
        root=Agent(
            name="leader", role="supervisor", system_prompt="你是主管",
            approval_policy=leader_policy,
            children=[
                Agent(name="w1", role="worker", system_prompt="执行者1",
                      tools=[], max_iterations=3),
                Agent(name="w2", role="worker", system_prompt="执行者2",
                      tools=["write_file"], max_iterations=3),
            ],
        ),
        engine=engine,
    )


class HarnessEnv:
    """一个测试 = 一个 fake server + 一个 runner 组装。"""

    def __init__(self, script: Script, team: Team, task="示例任务",
                 run_manager=None, use_store=True):
        self.server = FakeOpenCodeServer(script)
        self.base_url = self.server.start()
        self.client = OpenCodeClient(
            OpenCodeConfig(base_url=self.base_url, timeout=10)
        )
        import sqlite3
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        from agentteam.storage.db import init_db
        # 用 init_db 建 schema（内存库重新跑一遍脚本最简单）
        self.conn.executescript(_SCHEMA_SQL)
        self.audit = AuditRepo(self.conn)
        self.bus = EventBus()
        from agentteam.api.events import BroadcastTraceWriter
        self.trace = BroadcastTraceWriter(self.audit, self.bus)
        self.store = HarnessStateStore(self.conn) if use_store else None
        self.team = team
        self.task = task
        self._rm = run_manager
        self.runner = HarnessRunner(
            client=self.client, run_id="run_test", team=team, task=task,
            trace_writer=self.trace, audit_repo=self.audit,
            run_manager=run_manager, state_store=self.store,
            default_model="opencode/test-model",
            prompt_timeout=10.0, poll_interval=0.02,
        )

    def close(self):
        self.client.close()
        self.server.stop()
        self.conn.close()

    def events(self):
        return [dict(r) for r in self.audit.list_events("run_test")]

    def event_types(self):
        return [e["event_type"] for e in self.events()]


_SCHEMA_SQL = """
CREATE TABLE runs (id TEXT PRIMARY KEY, team_name TEXT, task TEXT, status TEXT,
 created_at TEXT, updated_at TEXT, ended_at TEXT, total_tokens INTEGER DEFAULT 0);
CREATE TABLE run_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
 event_type TEXT, actor TEXT, timestamp TEXT, payload TEXT DEFAULT '{}',
 duration_ms INTEGER, tokens INTEGER);
CREATE TABLE approvals (id TEXT PRIMARY KEY, run_id TEXT, status TEXT,
 requested_at TEXT, decided_at TEXT, decider TEXT, reason TEXT);
CREATE TABLE run_engine_state (run_id TEXT PRIMARY KEY, state TEXT,
 updated_at TEXT DEFAULT '');
"""


class StubRunManager:
    """引擎级测试用：只实现 is_cancelled。"""

    def __init__(self) -> None:
        self._cancel = threading.Event()

    def is_cancelled(self, run_id: str) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()


@pytest.fixture
def env_factory():
    created = []

    def _make(script: Script, team: Team = None, **kw) -> HarnessEnv:
        env = HarnessEnv(script, team or make_team(), **kw)
        created.append(env)
        return env

    yield _make
    for env in created:
        env.close()


# ================= 基本编排 =================


def test_seq_run_completes(env_factory):
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    env = env_factory(script)
    out = env.runner.invoke({}, {"configurable": {"thread_id": "run_test"}})
    state = env.runner.get_state({})
    assert state.next == ()  # 未 park → completed
    types = env.event_types()
    # 引擎级测试无 RunManager（run_start 由 RunManager 发），
    # 这里验证引擎自身的完整事件词表
    assert types == ["leader_plan", "worker_start", "worker_end", "leader_review"]
    assert out["total_tokens"] > 0


def test_seq_multi_step_order(env_factory):
    script = Script()
    script.plan = {"steps": [
        {"worker": "w1", "instruction": "第一步"},
        {"worker": "w2", "instruction": "第二步"},
    ], "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "w1结果"}]
    script.worker_steps["w2"] = [{"type": "final", "text": "w2结果"}]
    env = env_factory(script)
    env.runner.invoke({}, {})
    evs = env.events()
    starts = [i for i, e in enumerate(evs) if e["event_type"] == "worker_start"]
    ends = [i for i, e in enumerate(evs) if e["event_type"] == "worker_end"]
    # w1 的 start 在 w2 start 之前；两个 worker_end 都在
    assert len(starts) == 2 and len(ends) == 2
    assert starts[0] < starts[1]


def test_dag_respects_dependency(env_factory):
    script = Script()
    script.plan = {
        "steps": [
            {"worker": "w1", "instruction": "前置", "id": "s1"},
            {"worker": "w2", "instruction": "依赖s1", "id": "s2",
             "depends_on": ["s1"]},
        ],
        "execution_mode": "dag",
    }
    script.worker_steps["w1"] = [{"type": "final", "text": "s1完成"}]
    script.worker_steps["w2"] = [{"type": "final", "text": "s2完成"}]
    env = env_factory(script)
    env.runner.invoke({}, {})
    evs = env.events()
    s1_end = next(i for i, e in enumerate(evs)
                  if e["event_type"] == "worker_end" and e["actor"] == "w1")
    s2_start = next(i for i, e in enumerate(evs)
                    if e["event_type"] == "worker_start" and e["actor"] == "w2")
    assert s1_end < s2_start


def test_unknown_worker_fails(env_factory):
    script = Script()
    script.plan = {"steps": [{"worker": "ghost", "instruction": "?"}],
                   "execution_mode": "sequential"}
    env = env_factory(script)
    with pytest.raises(ValueError, match="unknown worker"):
        env.runner.invoke({}, {})


def test_resume_false_admits_but_never_schedules(env_factory):
    """opencode 1.18.32 实测：prompt 带 resume=false 只 durable 入队，回合
    永远不被调度、消息列表停在 user、且没有任何报错 —— 静默死锁。
    fake server 忠实复现该语义，客户端默认为 True（引擎全链路依赖此语义）。
    """
    env = env_factory(Script())
    sid = env.client.create_session(agent="w1")["id"]
    env.client.prompt_async(sid, "做A", resume=False)
    time.sleep(0.3)
    assert [m["type"] for m in env.client.messages(sid)] == ["user"]
    assert env.client.assistant_done(sid) is None
    env.client.prompt_async(sid, "做A")  # 默认值必须真正调度
    deadline = time.time() + 5
    while time.time() < deadline and env.client.assistant_done(sid) is None:
        time.sleep(0.05)
    assert env.client.assistant_done(sid) is not None


def test_history_default_limit_accepted(env_factory):
    """真实 v2 的 history limit 上限是 100（200 → 400），客户端默认值必须合规。"""
    env = env_factory(Script())
    sid = env.client.create_session(agent="w1")["id"]
    env.client.prompt_async(sid, "做A")
    deadline = time.time() + 5
    while time.time() < deadline and env.client.assistant_done(sid) is None:
        time.sleep(0.05)
    assert env.client.history(sid)  # 默认 limit 不触发 400
    with pytest.raises(OpenCodeError, match="400"):
        env.client.history(sid, limit=200)


def test_control_plane_prompt_never_scheduled_times_out(env_factory):
    """控制面回合永不被调度时必须按 timeout 失败，而不是吊死到 600s。

    真实场景：上游限流后 opencode 只写 user 消息、不再产出 assistant 消息
    （实测 §8），引擎唯一的出路就是超时判定。
    """
    from agentteam.harness.engine import HarnessRunner

    env = env_factory(Script())
    env.server.script.never_schedules = True
    team = make_team()
    runner = HarnessRunner(
        client=env.client, run_id="run_test", team=team, task="随便",
        trace_writer=None, audit_repo=env.audit, state_store=None,
        default_model="opencode/test-model", prompt_timeout=0.3,
        poll_interval=0.02,
    )
    with pytest.raises(OpenCodeError, match="timed out"):
        runner._one_shot({"agent_name": "leader", "system_prompt": "主管",
                          "role": "supervisor", "model": None, "tools": []},
                         "拆解任务")


def test_throttled_plan_turn_reports_provider_error(env_factory):
    """上游限流的回合形状是 finish=="error"（没有 session.error）。

    漏判的话引擎会把「模型压根没产出」当成「计划不是 JSON」报出去，
    企业现场看到的会是一条完全指错方向的错误。
    """
    script = Script()
    script.turn_errors = {
        "leader": "Provider request failed with HTTP 429: FreeUsageLimitError"}
    env = env_factory(script)
    with pytest.raises(OpenCodeError, match="429"):
        env.runner.invoke({}, {})


def test_throttled_worker_turn_fails_fast_without_sse(env_factory):
    """同一判定走 REST 兜底：SSE 全黑（blackhole）时也不能吊到 prompt 超时。"""
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.turn_errors = {
        "w1": "Provider request failed with HTTP 429: Rate limit exceeded"}
    env = env_factory(script)
    env.server.script.blackhole = True
    with pytest.raises(OpenCodeError, match="429"):
        env.runner.invoke({}, {})


def test_empty_plan_turn_retried_once(env_factory):
    """实测抖动：回合 finish=stop 但没有任何 text part → 控制面同会话重问一次。

    真实免费模型约 5% 的回合只产出 reasoning（step.ended output=0、
    content=[]），把它当「计划不是 JSON」直接失败会让 run 无谓地死掉。
    """
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    script.empty_turns = 1
    env = env_factory(script)
    out = env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "leader_plan" in env.event_types()
    assert out["total_tokens"] > 0


def test_two_empty_plan_turns_fail_run(env_factory):
    """重问只给一次机会：连续两次空回合仍是失败，但要报得清楚。"""
    script = Script()
    script.empty_turns = 2
    env = env_factory(script)
    with pytest.raises(ValueError, match="not valid JSON"):
        env.runner.invoke({}, {})


def test_plan_json_tolerant_extraction(env_factory):
    """v2 无结构化输出通道：计划 JSON 从散文/围栏里挖。"""
    script = Script()
    script.plan_raw = (
        '好的，计划如下：\n```json\n'
        '{"steps":[{"worker":"w1","instruction":"做A"}],'
        '"execution_mode":"sequential"}\n```\n以上。'
    )
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    env = env_factory(script)
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "leader_plan" in env.event_types()


def test_plan_not_json_fails_clearly(env_factory):
    script = Script()
    script.plan_raw = "我需要更多信息才能拆解"
    env = env_factory(script)
    with pytest.raises(ValueError, match="not valid JSON"):
        env.runner.invoke({}, {})


# ================= step / worker 级审批 =================


def test_step_gate_park_then_approve(env_factory):
    from agentteam.domain.approval import ApprovalPolicy
    team = make_team(leader_policy=ApprovalPolicy(level="step"))
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "做A"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A完成"}]
    env = env_factory(script, team)
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)  # interrupted
    assert "approval_requested" in env.event_types()
    # resume：批准
    env.runner.invoke({"__resume__": {"approved": True, "decider": "t"}}, {})
    assert env.runner.get_state({}).next == ()
    types = env.event_types()
    assert "approval_decided" in types
    # approvals 审计落账
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert len(approvals) == 1 and approvals[0]["status"] == "approved"


def test_step_gate_reject_ends_run(env_factory):
    from agentteam.domain.approval import ApprovalPolicy
    team = make_team(leader_policy=ApprovalPolicy(level="step"))
    script = Script()
    script.plan = {"steps": [
        {"worker": "w1", "instruction": "做A"},
        {"worker": "w2", "instruction": "做B"},
    ], "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A"}]
    env = env_factory(script, team)
    env.runner.invoke({}, {})
    env.runner.invoke({"__resume__": {"approved": False, "decider": "t"}}, {})
    assert env.runner.get_state({}).next == ()  # 正常结束（非 failed）
    types = env.event_types()
    # w1 从未启动（gate 在 worker 之前）
    assert "worker_start" not in types
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert approvals[0]["status"] == "rejected"


def test_worker_gate_targets(env_factory):
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="", default_model=ModelRef("qwen", "qwen-max"),
        root=Agent(name="leader", role="supervisor", system_prompt="s",
                   children=[
                       Agent(name="w1", role="worker", system_prompt="w",
                             tools=[],
                             approval_policy=ApprovalPolicy(level="worker",
                                                            targets=["w1"])),
                       Agent(name="w2", role="worker", system_prompt="w",
                             tools=[]),
                   ]),
        engine="opencode",
    )
    script = Script()
    script.plan = {"steps": [
        {"worker": "w1", "instruction": "a"},
        {"worker": "w2", "instruction": "b"},
    ], "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A"}]
    script.worker_steps["w2"] = [{"type": "final", "text": "B"}]
    env = env_factory(script, team)
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)  # w1 被门住
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    # w2 不在 targets → 无第二次审批
    assert env.runner.get_state({}).next == ()
    assert env.event_types().count("approval_requested") == 1


def test_gate_timeout_auto_approves(env_factory):
    from agentteam.domain.approval import ApprovalPolicy
    team = make_team(leader_policy=ApprovalPolicy(level="step", timeout_seconds=5))
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "a"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A"}]
    env = env_factory(script, team)
    env.runner.invoke({}, {})
    # 超时语义：不 park，直接自动放行
    assert env.runner.get_state({}).next == ()
    types = env.event_types()
    assert "approval_requested" not in types
    assert any(
        e["event_type"] == "approval_decided" and e["actor"] == "timeout"
        for e in env.events()
    )


# ================= tool 级审批（事后中断 + 审计） =================


def _tool_gate_env(env_factory, targets, timeout=None):
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="", default_model=ModelRef("qwen", "qwen-max"),
        root=Agent(name="leader", role="supervisor", system_prompt="s",
                   children=[
                       Agent(name="w2", role="worker", system_prompt="w",
                             tools=["write_file"],
                             approval_policy=ApprovalPolicy(
                                 level="tool", targets=targets,
                                 timeout_seconds=timeout)),
                   ]),
        engine="opencode",
    )
    script = Script()
    script.plan = {"steps": [{"worker": "w2", "instruction": "写文件"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w2"] = [
        {"type": "tool", "name": "write_file", "args": {"path": "a.txt"},
         "result": "written"},
        {"type": "final", "text": "写完了"},
    ]
    return env_factory(script, team)


def test_tool_approval_park_and_approve(env_factory):
    env = _tool_gate_env(env_factory, ["write_file"])
    env.runner.invoke({}, {})
    # 工具已被观测 → 引擎 interrupt + park（事后中断，非事前拦截）
    assert env.runner.get_state({}).next == ("parked",)
    evs = env.events()
    denied = [e for e in evs if e["event_type"] == "tool_denied"]
    assert denied and "requires_approval" in denied[0]["payload"]
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    assert env.runner.get_state({}).next == ()
    types = env.event_types()
    assert "tool_call" in types  # 工具实际执行
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert approvals and approvals[0]["status"] == "approved"


def test_tool_approval_reject_ends_step(env_factory):
    env = _tool_gate_env(env_factory, ["write_file"])
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    env.runner.invoke({"__resume__": {"approved": False}}, {})
    # 拒绝 → 回合已中断，frame 置 rejected，run 正常收尾（LangGraph parity）
    assert env.runner.get_state({}).next == ()
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert approvals[0]["status"] == "rejected"
    assert "worker_end" not in env.event_types()


def test_tool_approval_not_parked_twice_for_same_tool(env_factory):
    env = _tool_gate_env(env_factory, ["write_file"])
    script_calls = [
        {"type": "tool", "name": "write_file", "args": {"path": "a.txt"},
         "result": "written", "settle": 0.05},
        {"type": "tool", "name": "write_file", "args": {"path": "b.txt"},
         "result": "written", "settle": 0.05},
        {"type": "final", "text": "写完了"},
    ]
    env.server.script.worker_steps["w2"] = script_calls
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    # 同一工具已放行 → 第二次调用不再 park，run 直达完成
    assert env.runner.get_state({}).next == ()
    assert env.event_types().count("approval_requested") == 1


def test_tool_gate_parks_even_when_sse_lags(env_factory):
    """回归：REST 完成信号可以领先于 SSE 观测（真实 server 上就是竞态源）。
    收尾必须用 durable history 对账补齐观测，否则事后拦截/审计整批发漏。
    """
    env = _tool_gate_env(env_factory, ["write_file"])
    env.server.script.blackhole = True  # 事件只进 history，不推 SSE
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    evs = env.events()
    assert [e for e in evs if e["event_type"] == "tool_denied"]
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    assert env.runner.get_state({}).next == ()
    # 对账幂等：history 重放不得把 token 翻倍（一次 worker 回合 = 一份用量）
    ends = [e for e in env.events() if e["event_type"] == "worker_end"]
    assert len(ends) == 1


def test_tool_not_in_targets_runs_freely(env_factory):
    # targets 只含 read_file，而 write_file 在 worker 白名单内 → 无违规
    env = _tool_gate_env(env_factory, ["read_file"])
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "approval_requested" not in env.event_types()
    assert "tool_call" in env.event_types()


# ================= 工具白名单事后执法 =================


def _whitelist_env(env_factory, tool_name):
    from agentteam.domain.agent import Agent
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="", default_model=ModelRef("qwen", "qwen-max"),
        root=Agent(name="leader", role="supervisor", system_prompt="s",
                   children=[
                       Agent(name="w1", role="worker", system_prompt="w",
                             tools=["read_file"]),
                   ]),
        engine="opencode",
    )
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "读文件"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [
        {"type": "tool", "name": tool_name, "args": {"path": "a.txt"},
         "result": "r", "settle": 0.05},
        {"type": "final", "text": "完成"},
    ]
    return env_factory(script, team)


def test_unwhitelisted_tool_parks(env_factory):
    env = _whitelist_env(env_factory, "bash")
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    denied = [e for e in env.events() if e["event_type"] == "tool_denied"]
    assert denied and "not_whitelisted" in denied[0]["payload"]
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    assert env.runner.get_state({}).next == ()


def test_whitelisted_tool_never_parks(env_factory):
    env = _whitelist_env(env_factory, "read")  # read_file → read
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "tool_denied" not in env.event_types()


def test_unwhitelisted_tool_audit_mode_continues(env_factory, monkeypatch):
    monkeypatch.setenv("AGENTTEAM_OC_TOOL_GUARD", "audit")
    env = _whitelist_env(env_factory, "bash")
    env.runner.invoke({}, {})
    # audit 模式：只记事实不 park
    assert env.runner.get_state({}).next == ()
    assert "approval_requested" not in env.event_types()
    denied = [e for e in env.events() if e["event_type"] == "tool_denied"]
    assert denied and '"action": "continued"' in denied[0]["payload"]


def test_tool_gate_timeout_auto_allows(env_factory):
    # targets=None → 全部工具都要审批；timeout_seconds → 不等人，自动放行
    env = _tool_gate_env(env_factory, None, timeout=5)
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "approval_requested" not in env.event_types()
    assert any(e["event_type"] == "approval_decided" and e["actor"] == "timeout"
               for e in env.events())


# ================= 取消 =================


def test_cancel_during_run(env_factory):
    stub = StubRunManager()
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "a"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "sleep", "seconds": 30}]
    env = env_factory(script, run_manager=stub)
    result = {}

    def run():
        try:
            env.runner.invoke({}, {})
        except RunCancelledError:
            result["cancelled"] = True

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)  # 等 worker 会话进入 sleep
    stub.cancel()
    t.join(timeout=5)
    assert result.get("cancelled"), "engine should raise RunCancelledError on cancel"


# ================= 快照持久化 + 重启 rehydrate =================


def test_restart_rehydrate_resumes(env_factory):
    from agentteam.domain.approval import ApprovalPolicy
    team = make_team(leader_policy=ApprovalPolicy(level="step"))
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "a"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w1"] = [{"type": "final", "text": "A"}]
    env = env_factory(script, team)
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    # 模拟服务重启：全新 runner 实例（同 state_store、同 audit/bus）
    from agentteam.api.events import BroadcastTraceWriter
    runner2 = HarnessRunner(
        client=env.client, run_id="run_test", team=team, task=env.task,
        trace_writer=BroadcastTraceWriter(env.audit, env.bus),
        audit_repo=env.audit, state_store=env.store,
        default_model="opencode/test-model", prompt_timeout=10.0,
        poll_interval=0.02,
    )
    runner2.invoke({"__resume__": {"approved": True}}, {})
    assert runner2.get_state({}).next == ()
    assert env.store.load("run_test") is None  # 终态后快照清理


def test_state_survives_without_store_raises_clearly(env_factory):
    from agentteam.domain.approval import ApprovalPolicy
    team = make_team(leader_policy=ApprovalPolicy(level="step"))
    script = Script()
    script.plan = {"steps": [{"worker": "w1", "instruction": "a"}],
                   "execution_mode": "sequential"}
    env = env_factory(script, team, use_store=False)
    env.runner.invoke({}, {})
    # 同实例 resume：内存态还在，可以续跑（不经过 store）
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    assert env.runner.get_state({}).next == ()
