"""SP12c: TriggerEngine 两级过滤 + 命中创建 run(单元测试,fake 依赖)。"""
import threading

import pytest
from langchain_core.messages import AIMessage

from agentteam.api.routes.runs import RunSubmitError
from agentteam.domain.trigger import TriggerRule, resolve_path
from agentteam.models.provider import ModelRef
from agentteam.runtime.memory import build_memory_context  # noqa: F401  (确保 runtime 可导入)
from agentteam.storage.db import init_db
from agentteam.storage.triggers import TriggerRepo
from agentteam.triggers.engine import TriggerEngine, check_secret
from tests.conftest import FakeLLM, FakeModelProvider

SLOW_YES = '```json\n{"act": true, "reason": "明确请求"}\n```'
SLOW_NO = '```json\n{"act": false, "reason": "闲聊"}\n```'


def _rule(name="hook1", **kw) -> TriggerRule:
    base = dict(
        name=name, team_name="dev", task_from="payload.text",
        conditions=[{"field": "payload.kind", "op": "eq", "value": "request"}],
    )
    base.update(kw)
    return TriggerRule(**base)


def _event(kind="request", text="修复登录 bug", **extra) -> dict:
    ev = {"source": "webhook", "headers": {}, "payload": {"kind": kind, "text": text}, "text": text}
    ev.update(extra)
    return ev


@pytest.fixture
def env(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    repo = TriggerRepo(conn, lock=threading.Lock())
    created = []

    def starter(team_name, task, triggered_by_user):
        if team_name == "ghost":
            raise RunSubmitError(404, f"Team '{team_name}' not found")
        created.append((team_name, task, triggered_by_user))
        return f"run-{len(created)}"

    yield {"repo": repo, "created": created, "starter": starter}
    conn.close()


def _engine(env, llm=None) -> TriggerEngine:
    return TriggerEngine(
        trigger_repo=env["repo"],
        model_provider=FakeModelProvider({"qwen-max": llm or FakeLLM()}),
        run_starter=env["starter"],
        default_model=ModelRef("qwen", "qwen-max"),
    )


# ---------- Fast Path ----------

def test_fast_path_match_creates_run(env):
    env["repo"].add_rule(_rule())
    d = _engine(env).handle_event("hook1", _event())
    assert d == {"matched": True, "run_id": "run-1", "via": "fast_path",
                 "reason": None, "status_code": None}
    assert env["created"] == [("dev", "修复登录 bug", "trigger:hook1")]


def test_fast_path_filtered(env):
    env["repo"].add_rule(_rule())
    d = _engine(env).handle_event("hook1", _event(kind="chat"))
    assert d["matched"] is False
    assert d["reason"] == "filtered_by_fast_path"
    assert env["created"] == []


def test_rule_not_found_and_disabled(env):
    e = _engine(env)
    assert e.handle_event("nope", _event())["reason"] == "rule_not_found"
    env["repo"].add_rule(_rule(enabled=False))
    assert e.handle_event("hook1", _event())["reason"] == "rule_disabled"


def test_task_text_empty_does_not_fire(env):
    env["repo"].add_rule(_rule(task_from="payload.nope"))
    d = _engine(env).handle_event("hook1", _event())
    assert d["matched"] is False and d["reason"] == "task_text_empty"


def test_submit_failure_surfaces_status_code(env):
    env["repo"].add_rule(_rule(team_name="ghost"))
    d = _engine(env).handle_event("hook1", _event())
    assert d["matched"] is True and d["run_id"] is None
    assert d["status_code"] == 404


# ---------- 条件算子 ----------

def test_condition_ops():
    rule = _rule(conditions=[
        {"field": "headers.x-event", "op": "in", "value": ["push", "pr"]},
        {"field": "payload.title", "op": "regex", "value": "fail(ure|ed)"},
        {"field": "payload.tag", "op": "exists", "value": True},
    ])
    ok = {"headers": {"x-event": "push"}, "payload": {"title": "build failed", "tag": "ci"}}
    assert rule.matches_fast_path(ok) is True
    bad = {"headers": {"x-event": "issue"}, "payload": {"title": "ok", "tag": "ci"}}
    assert rule.matches_fast_path(bad) is False
    missing = {"headers": {"x-event": "push"}, "payload": {"title": "build failed"}}
    assert rule.matches_fast_path(missing) is False  # exists 不满足


def test_resolve_path():
    assert resolve_path({"a": {"b": 1}}, "a.b") == (True, 1)
    assert resolve_path({"a": 1}, "a.b")[0] is False


def test_template_task_build():
    rule = _rule(conditions=[], task_template="处理 {payload.title}，来源 {headers.x-src}",
                 task_from=None)
    task = rule.build_task({"payload": {"title": "崩溃" * 3000}, "headers": {"x-src": "gh"}})
    assert task.startswith("处理 崩溃")
    assert "来源 gh" in task
    assert len(task) <= 4000  # 总长截断


# ---------- Slow Path ----------

def test_slow_path_act_creates_run(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content=SLOW_YES)])
    env["repo"].add_rule(_rule(conditions=[], slow_path={"enabled": True, "prompt": "只处理 bug"}))
    d = _engine(env, llm).handle_event("hook1", _event())
    assert d["matched"] is True and d["via"] == "slow_path"
    assert d["reason"] == "明确请求"


def test_slow_path_declined(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content=SLOW_NO)])
    env["repo"].add_rule(_rule(conditions=[], slow_path={"enabled": True, "prompt": ""}))
    d = _engine(env, llm).handle_event("hook1", _event())
    assert d["matched"] is False
    assert d["reason"].startswith("slow_path_declined")


def test_slow_path_invalid_response_skips(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="我不是 JSON")])
    env["repo"].add_rule(_rule(conditions=[], slow_path={"enabled": True, "prompt": ""}))
    d = _engine(env, llm).handle_event("hook1", _event())
    assert d["matched"] is False and d["reason"] == "slow_path_unavailable"


def test_slow_path_llm_failure_silent(env):
    llm = FakeLLM()
    llm.invoke_responses = [RuntimeError("boom")]
    env["repo"].add_rule(_rule(conditions=[], slow_path={"enabled": True, "prompt": ""}))
    d = _engine(env, llm).handle_event("hook1", _event())  # 不抛
    assert d["matched"] is False


def test_slow_path_globally_disabled(env):
    llm = FakeLLM()
    env["repo"].add_rule(_rule(conditions=[], slow_path={"enabled": True, "prompt": ""}))
    engine = _engine(env, llm)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTTEAM_TRIGGERS_SLOW_PATH_ENABLED", "false")
        from agentteam.config import override_settings
        with override_settings(triggers_slow_path_enabled=False):
            d = engine.handle_event("hook1", _event())
    assert d["matched"] is False
    assert llm._inv_idx == 0  # LLM 未被调用


# ---------- secret ----------

def test_check_secret():
    rule = _rule(secret="abc")
    assert check_secret(rule, {"x-trigger-secret": "abc"}) is True
    assert check_secret(rule, {"x-trigger-secret": "wrong"}) is False
    assert check_secret(_rule(secret=None), {}) is True  # 未配置不校验
