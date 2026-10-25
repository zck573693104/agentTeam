"""P-A5 IM 通知出口测试。

覆盖:
- format_payload 四种 provider 请求体形状(feishu/wecom/dingtalk/generic)
- NotificationService:webhook 地址解析(Team 优先→全局回退→无则跳过)、
  事件白名单、发送失败吞没
- RunManager._notify 挂钩:notifier 调用/异常兜底/None 跳过
- RunManager 完成路径集成:run completed → notifier 收到 run_end
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.config import Settings
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.notify import NotificationService, format_payload
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep, ReviewVerdict
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


# ================= format_payload =================


def test_format_payload_four_providers():
    f = format_payload("feishu", "T", ["a", "b"], "http://link")
    assert f == {"msg_type": "text", "content": {"text": "T\na\nb\n👉 http://link"}}

    w = format_payload("wecom", "T", ["a"], None)
    assert w["msgtype"] == "markdown"
    assert w["markdown"]["content"] == "T\na"

    d = format_payload("dingtalk", "T", ["a"], None)
    assert d["msgtype"] == "markdown"
    assert d["markdown"]["title"] == "T"
    assert d["markdown"]["text"] == "T\na"

    g = format_payload("generic", "T", ["a"], "http://link")
    assert g == {"title": "T", "text": "a", "link": "http://link"}

    # 未知/空 provider 回退 generic
    assert format_payload("", "T", ["a"], None)["title"] == "T"
    assert format_payload("weird", "T", ["a"], None)["title"] == "T"


# ================= NotificationService =================


class _SyncThread:
    """把 daemon 发送线程替换为同步执行，消除测试竞态。"""

    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class _FakeRunRepo:
    def __init__(self, rows: dict):
        self._rows = rows

    def get_run(self, run_id):
        return self._rows.get(run_id)


class _FakeTeamStore:
    def __init__(self, teams: dict):
        self._teams = teams

    def get(self, name):
        return self._teams.get(name)


def _settings(**kw) -> Settings:
    base = dict(
        notify_webhook_url="",
        notify_provider="generic",
        console_url="http://console",
        notify_timeout_seconds=1.0,
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def sent(monkeypatch):
    """同步化发送线程 + 记录 POST (url, json)。返回记录列表。"""
    records: list[tuple[str, dict]] = []

    def _fake_post(url, json=None, timeout=None):
        records.append((url, json))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("agentteam.notify.service.threading.Thread", _SyncThread)
    monkeypatch.setattr("agentteam.notify.service.requests.post", _fake_post)
    return records


_RUN_ROW = {"team_name": "t", "task": "示例任务", "total_tokens": 42}


def test_team_webhook_url_priority(sent):
    repo = _FakeRunRepo({"r1": dict(_RUN_ROW)})
    store = _FakeTeamStore({"t": SimpleNamespace(webhook_url="http://team-hook")})
    svc = NotificationService(
        repo, store, settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_end")
    assert [u for u, _ in sent] == ["http://team-hook"]


def test_fallback_to_global_webhook(sent):
    repo = _FakeRunRepo({"r1": dict(_RUN_ROW)})
    # team 存在但未配 webhook_url → 回退全局
    store = _FakeTeamStore({"t": SimpleNamespace(webhook_url=None)})
    svc = NotificationService(
        repo, store, settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_end")
    assert [u for u, _ in sent] == ["http://global"]


def test_no_url_configured_skips_silently(sent):
    repo = _FakeRunRepo({"r1": dict(_RUN_ROW)})
    svc = NotificationService(repo, _FakeTeamStore({}), settings=_settings())
    svc.on_run_event("r1", "run_end")
    assert sent == []


def test_event_whitelist(sent):
    repo = _FakeRunRepo({"r1": dict(_RUN_ROW)})
    svc = NotificationService(
        repo, None, settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_cancelled")  # 取消不打扰
    svc.on_run_event("r1", "leader_plan")  # 非生命周期事件
    assert sent == []
    svc.on_run_event("r1", "run_end")
    assert len(sent) == 1


def test_unknown_run_still_notifies(sent):
    """run 行缺失(极端竞态)不炸,团队名显示占位符。"""
    svc = NotificationService(
        _FakeRunRepo({}), None, settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("ghost", "error", {"error": "boom"})
    assert len(sent) == 1
    assert sent[0][1]["title"].startswith("❌ AgentTeam · Run 失败")
    assert "错误:boom" in sent[0][1]["text"]


def test_message_rendering_per_event(sent):
    repo = _FakeRunRepo({"r1": dict(_RUN_ROW)})
    svc = NotificationService(
        repo, None, settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_interrupted")
    title, text, link = sent[0][1]["title"], sent[0][1]["text"], sent[0][1]["link"]
    assert "等待审批" in title
    assert "团队:t" in text and "任务:示例任务" in text
    assert link == "http://console/runs/r1"

    sent.clear()
    svc.on_run_event("r1", "run_end")
    assert "完成" in sent[0][1]["title"]
    assert "42" in sent[0][1]["text"]  # token 用量

    sent.clear()
    svc.on_run_event("r1", "error", {"error": "x" * 500})
    assert "失败" in sent[0][1]["title"]
    # 错误信息截断到 200
    assert len(sent[0][1]["text"]) < len("x" * 500) + 200


def test_http_500_swallowed(monkeypatch):
    monkeypatch.setattr(
        "agentteam.notify.service.threading.Thread", _SyncThread)

    def _post(url, json=None, timeout=None):
        return SimpleNamespace(status_code=500)

    monkeypatch.setattr("agentteam.notify.service.requests.post", _post)
    svc = NotificationService(
        _FakeRunRepo({"r1": dict(_RUN_ROW)}), None,
        settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_end")  # 不抛即通过


def test_post_exception_swallowed(monkeypatch):
    monkeypatch.setattr(
        "agentteam.notify.service.threading.Thread", _SyncThread)

    def _post(url, json=None, timeout=None):
        raise ConnectionError("network down")

    monkeypatch.setattr("agentteam.notify.service.requests.post", _post)
    svc = NotificationService(
        _FakeRunRepo({"r1": dict(_RUN_ROW)}), None,
        settings=_settings(notify_webhook_url="http://global"),
    )
    svc.on_run_event("r1", "run_end")  # 不抛即通过


# ================= RunManager 挂钩 =================


def test_run_manager_notify_hook():
    notifier = MagicMock()
    rm = RunManager(None, None, EventBus(), notifier=notifier)
    rm._notify("r1", "run_end")
    notifier.on_run_event.assert_called_once_with("r1", "run_end", None)


def test_run_manager_notify_swallows_notifier_errors():
    notifier = MagicMock()
    notifier.on_run_event.side_effect = RuntimeError("bad notifier")
    rm = RunManager(None, None, EventBus(), notifier=notifier)
    rm._notify("r1", "error", {"error": "x"})  # 不抛即通过


def test_run_manager_notify_none_is_noop():
    rm = RunManager(None, None, EventBus())
    rm._notify("r1", "run_end")  # 不抛即通过


# ================= 完成路径集成 =================


def _make_team() -> Team:
    return Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )


def test_completed_run_triggers_notify(tmp_path):
    """run 正常完成 → notifier 收到 run_end(真实 RunManager 生命周期路径)。"""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from agentteam.storage.audit import AuditRepo
    from agentteam.storage.db import init_db
    from agentteam.storage.runs import RunRepo

    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    notifier = MagicMock()
    rm = RunManager(run_repo, audit_repo, EventBus(), notifier=notifier)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="w1", instruction="do x")]),
        ReviewVerdict(passed=True, reason="ok"),
    ])
    fake_llm.set_invoke_responses([AIMessage(content="done")])
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    saver = SqliteSaver(conn)
    saver.setup()
    graph = compiler.compile(_make_team(), checkpointer=saver)

    run_id = run_repo.create_run("t", "test task")
    rm.start_run(run_id, graph, {"configurable": {"thread_id": run_id}}, "test task")
    rm.wait(run_id, timeout=10)

    assert run_repo.get_run(run_id)["status"] == "completed"
    calls = [c for c in notifier.on_run_event.call_args_list
             if c.args[1] == "run_end"]
    assert calls, f"run_end 通知未触发: {notifier.on_run_event.call_args_list}"
    assert calls[0].args[0] == run_id
    rm.shutdown(wait=False)
    conn.close()
