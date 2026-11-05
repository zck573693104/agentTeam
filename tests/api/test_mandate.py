"""SP12 Mandate 授权模型测试。

覆盖:
- Mandate 数据结构:往返、旧数据兼容(None/损坏/非 dict)、basis 校验、
  超长字段截断
- policy_version_for_team:配置内容哈希,配置变 → 版本变,同配置稳定
- default_mandate:触发身份推断 explicit/inferred
- 存储链:create_run 落 mandate_json、旧路径 None、run_to_dict 解析
- 撤权:_revocation_payload 带不带 mandate_version 两形态
- state 快照:_run_in_background 把 mandate 注入 initial(只读)
- 策略版本化:团队热更新 → 进行中 run 轨迹出现 policy_expired 注记
- 审批盖章:add_approval 记录 mandate_version
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.api.routes.runs import run_to_dict
from agentteam.domain.mandate import (
    Mandate,
    default_mandate,
    policy_version_for_team,
)
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from tests.conftest import FakeLLM, FakeModelProvider


@pytest.fixture
def db(tmp_path):
    conn = init_db(tmp_path / "mandate.db")
    yield conn
    conn.close()


@pytest.fixture
def run_repo(db):
    return RunRepo(db)


@pytest.fixture
def audit_repo(db):
    return AuditRepo(db)


def _team(model_name: str = "qwen-max"):
    from tests.api.conftest import make_team_json

    data = make_team_json()
    data["default_model"] = {"provider": "qwen", "name": model_name}
    from agentteam.domain.serializer import team_from_dict

    return team_from_dict(data)


# ================= Mandate 数据结构 =================


def test_mandate_round_trip():
    m = Mandate(basis="explicit", submitted_by="u1",
                policy_version="abc123", scope="做 X")
    assert Mandate.from_dict(m.to_dict()) == m


def test_mandate_from_dict_none_and_empty():
    """旧 run 无授权凭据 → None(向后兼容)。"""
    assert Mandate.from_dict(None) is None
    assert Mandate.from_dict("") is None


def test_mandate_from_dict_corrupt_raises():
    with pytest.raises(ValueError, match="corrupt mandate"):
        Mandate.from_dict("{not-json")
    with pytest.raises(ValueError, match="JSON object"):
        Mandate.from_dict(42)


def test_mandate_invalid_basis_raises():
    with pytest.raises(ValueError, match="basis"):
        Mandate(basis="guessed", submitted_by="u", policy_version="p")


def test_mandate_empty_policy_version_raises():
    with pytest.raises(ValueError, match="policy_version"):
        Mandate(basis="explicit", submitted_by="u", policy_version="")


def test_mandate_long_fields_truncated():
    m = Mandate(basis="explicit", submitted_by="u" * 5000,
                policy_version="p", scope="s" * 5000)
    assert len(m.submitted_by) == 4000
    assert len(m.scope) == 4000


def test_mandate_default_submitted_by():
    m = Mandate(basis="explicit", submitted_by="", policy_version="p")
    assert m.submitted_by == "api"


# ================= policy_version =================


def test_policy_version_stable_and_changes():
    t1 = _team("qwen-max")
    pv1 = policy_version_for_team(t1)
    assert pv1 == policy_version_for_team(_team("qwen-max"))  # 同配置稳定
    assert policy_version_for_team(_team("other-model")) != pv1  # 配置变 → 版本变


# ================= default_mandate =================


def test_default_mandate_explicit_for_human():
    m = default_mandate(_team(), "做 X", "alice")
    assert m.basis == "explicit"
    assert m.submitted_by == "alice"
    assert m.scope == "做 X"
    assert m.policy_version == policy_version_for_team(_team())


def test_default_mandate_none_user_falls_back_api():
    m = default_mandate(_team(), "做 X", None)
    assert m.basis == "explicit"
    assert m.submitted_by == "api"


def test_default_mandate_inferred_for_trigger():
    m = default_mandate(_team(), "做 X", "trigger:nightly")
    assert m.basis == "inferred"
    assert m.submitted_by == "trigger:nightly"


# ================= 存储链 =================


def test_create_run_persists_mandate(run_repo):
    m = Mandate(basis="explicit", submitted_by="u1",
                policy_version="pv-1", scope="做 X")
    run_id = run_repo.create_run("t", "做 X", triggered_by_user="u1", mandate=m)
    row = run_repo.get_run(run_id)
    d = run_to_dict(row)
    assert d["mandate"] == m.to_dict()
    # 再经 Mandate.from_dict 往返不丢
    assert Mandate.from_dict(row["mandate_json"]) == m


def test_create_run_without_mandate_is_none(run_repo):
    """旧路径(不传 mandate)→ mandate_json NULL,API 层读 None。"""
    run_id = run_repo.create_run("t", "task")
    assert run_repo.get_run(run_id)["mandate_json"] is None
    assert run_to_dict(run_repo.get_run(run_id))["mandate"] is None


def test_run_to_dict_corrupt_mandate_json_is_none():
    d = run_to_dict({"id": "r1", "mandate_json": "{broken"})
    assert d["mandate"] is None


# ================= 撤权 + state 快照 =================


def _run_manager(run_repo, audit_repo):
    return RunManager(run_repo, audit_repo, EventBus())


def test_revocation_payload_with_mandate(run_repo, audit_repo):
    rm = _run_manager(run_repo, audit_repo)
    run_id = run_repo.create_run(
        "t", "task",
        mandate=Mandate(basis="explicit", submitted_by="u",
                        policy_version="pv-9"),
    )
    payload = rm._revocation_payload(run_id, "user requested cancel")
    assert payload["revoked_by_human"] is True
    assert payload["mandate_version"] == "pv-9"


def test_revocation_payload_without_mandate(run_repo, audit_repo):
    """旧 run 无凭据 → 不带 mandate_version 键(向后兼容)。"""
    rm = _run_manager(run_repo, audit_repo)
    run_id = run_repo.create_run("t", "task")
    payload = rm._revocation_payload(run_id, "user requested cancel")
    assert payload == {"reason": "user requested cancel", "revoked_by_human": True}


def test_mandate_version_helper(run_repo, audit_repo):
    rm = _run_manager(run_repo, audit_repo)
    run_id = run_repo.create_run(
        "t", "task",
        mandate=Mandate(basis="explicit", submitted_by="u", policy_version="pv-x"),
    )
    assert rm.mandate_version(run_id) == "pv-x"
    assert rm.mandate_version("ghost") is None


def test_mandate_snapshot_injected_into_initial_state(run_repo, audit_repo):
    """_run_in_background 把 mandate 快照注入 initial state(只读)。"""
    rm = _run_manager(run_repo, audit_repo)
    run_id = run_repo.create_run(
        "t", "task",
        mandate=Mandate(basis="explicit", submitted_by="api",
                        policy_version="pv-snap"),
    )
    run_repo.update_status(run_id, "running")

    fake_graph = MagicMock()
    fake_graph.invoke.return_value = {}
    fake_graph.get_state.return_value = SimpleNamespace(next=(), values={})
    rm._run_in_background(run_id, fake_graph, {}, "task")

    initial = fake_graph.invoke.call_args[0][0]
    assert initial["mandate"]["policy_version"] == "pv-snap"


def test_initial_state_without_mandate_has_no_key(run_repo, audit_repo):
    """旧 run 无凭据 → initial 不设 mandate 键(TypedDict 可选键语义)。"""
    rm = _run_manager(run_repo, audit_repo)
    run_id = run_repo.create_run("t", "task")
    run_repo.update_status(run_id, "running")

    fake_graph = MagicMock()
    fake_graph.invoke.return_value = {}
    fake_graph.get_state.return_value = SimpleNamespace(next=(), values={})
    rm._run_in_background(run_id, fake_graph, {}, "task")

    assert "mandate" not in fake_graph.invoke.call_args[0][0]


# ================= 策略版本化(热更新注记) =================


def test_policy_expired_note_on_team_hot_update(make_client, tmp_path):
    """团队配置热更新 → 进行中 run 轨迹出现 policy_expired 注记(不中断)。"""
    client = make_client(FakeModelProvider({"qwen-max": FakeLLM()}))
    client.post("/api/teams", json={
        "name": "mt", "description": "",
        "leader": {"system_prompt": "s"},
        "workers": [{"name": "w1", "role": "r", "description": "",
                     "system_prompt": "s"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    })
    assert client.post("/api/teams", json={
        "name": "mt", "description": "",
        "leader": {"system_prompt": "s"},
        "workers": [{"name": "w1", "role": "r", "description": "",
                     "system_prompt": "s"}],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    }).status_code in (400, 200)  # 已存在

    # 直接造一个进行中 run,带当前配置版本的授权凭据
    conn = init_db(tmp_path / "api_test.db")
    run_repo = RunRepo(conn)
    audit = AuditRepo(conn)
    run_id = run_repo.create_run(
        "mt", "task",
        mandate=default_mandate(_team("qwen-max"), "task", "u1"),
    )
    run_repo.update_status(run_id, "running")

    # 热更新:改 worker 模型 → policy_version 变化
    resp = client.put("/api/teams/mt", json={
        "name": "mt", "description": "",
        "leader": {"system_prompt": "s"},
        "workers": [{"name": "w1", "role": "r", "description": "",
                     "system_prompt": "s"}],
        "default_model": {"provider": "qwen", "name": "other-model"},
    })
    assert resp.status_code == 200

    events = [dict(e) for e in audit.list_events(run_id)]
    expired = [e for e in events if e["event_type"] == "policy_expired"]
    assert len(expired) == 1
    payload = json.loads(expired[0]["payload"])
    assert payload["run_policy_version"] != payload["current_policy_version"]
    assert "已过期" in payload["note"]
    conn.close()


# ================= 审批盖章 =================


def test_add_approval_stamps_mandate_version(run_repo, audit_repo):
    run_id = run_repo.create_run("t", "task")
    approval_id = audit_repo.add_approval(run_id, tool_name="bash",
                                          args_preview="ls",
                                          mandate_version="pv-approve")
    row = audit_repo.get_approval(approval_id)
    assert row["mandate_version"] == "pv-approve"
