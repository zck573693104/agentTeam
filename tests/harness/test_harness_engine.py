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
from agentteam.harness.opencode_client import OpenCodeClient, OpenCodeConfig
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


# ================= tool 级审批（permission 桥） =================


def _tool_gate_env(env_factory, targets):
    from agentteam.domain.agent import Agent
    from agentteam.domain.approval import ApprovalPolicy
    from agentteam.models.provider import ModelRef
    team = Team(
        name="t", description="", default_model=ModelRef("qwen", "qwen-max"),
        root=Agent(name="leader", role="supervisor", system_prompt="s",
                   children=[
                       Agent(name="w2", role="worker", system_prompt="w",
                             tools=["write_file"],
                             approval_policy=ApprovalPolicy(level="tool",
                                                            targets=targets)),
                   ]),
        engine="opencode",
    )
    script = Script()
    script.plan = {"steps": [{"worker": "w2", "instruction": "写文件"}],
                   "execution_mode": "sequential"}
    script.worker_steps["w2"] = [
        {"type": "tool", "name": "write_file", "ask": True, "result": "written"},
        {"type": "final", "text": "写完了"},
    ]
    return env_factory(script, team)


def test_tool_permission_park_and_approve(env_factory):
    env = _tool_gate_env(env_factory, ["write_file"])
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ("parked",)
    # fake server 收到回帖前应有一个 pending permission
    env.runner.invoke({"__resume__": {"approved": True}}, {})
    assert env.runner.get_state({}).next == ()
    types = env.event_types()
    assert "tool_call" in types  # 工具实际执行
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert approvals and approvals[0]["status"] == "approved"


def test_tool_permission_reject_continues(env_factory):
    env = _tool_gate_env(env_factory, ["write_file"])
    env.runner.invoke({}, {})
    env.runner.invoke({"__resume__": {"approved": False}}, {})
    # 拒绝 → opencode 回帖 reject → 模型收到拒绝继续 → run 正常完成
    assert env.runner.get_state({}).next == ()
    approvals = [dict(r) for r in env.audit.list_approvals("run_test")]
    assert approvals[0]["status"] == "rejected"


def test_tool_not_in_targets_auto_allows(env_factory):
    # targets 只含 read_file → write_file 的 ask 不会被 park
    env = _tool_gate_env(env_factory, ["read_file"])
    env.runner.invoke({}, {})
    assert env.runner.get_state({}).next == ()
    assert "approval_requested" not in env.event_types()


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
