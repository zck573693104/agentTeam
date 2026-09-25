"""真实 opencode server 冒烟测试（skipif 保护）。

跳过条件（满足任一即 skip）：
- AGENTTEAM_OC_SMOKE=0 显式关闭
- 探测不到真实 server（默认依次尝试 AGENTTEAM_OPENCODE_URL 与
  http://127.0.0.1:4117，1 秒超时）

这些测试走真实 LLM（免费模型 opencode/ling-3.0-flash-fin-free），验证套壳
引擎对 opencode v1.18.32 **v2 `/api/*` 面**的端到端可用性；断言基于确定性
契约（回合完成信号、turn 锚点、SSE 载荷位置、history seq），不依赖模型输出内容。
"""
from __future__ import annotations

import os
import re
import threading
import time

import pytest

from agentteam.harness.opencode_client import OpenCodeClient, OpenCodeConfig

_SMOKE_URLS = [
    os.environ.get("AGENTTEAM_OPENCODE_URL"),
    "http://127.0.0.1:4117",
    "http://127.0.0.1:4096",
]
_SMOKE_MODEL = os.environ.get("AGENTTEAM_OC_MODEL",
                              "opencode/ling-3.0-flash-fin-free")

# 实测：连发真实请求会撞上 opencode zen 的免费额度闸门，形态是
# assistant finish=="error" + "HTTP 429 / FreeUsageLimitError"。
# 那是配额问题不是套壳缺陷，冒烟层按 skip 处理（离线 90 例才是功能保证）。
_THROTTLE_RE = re.compile(
    r"429|FreeUsageLimit|Rate.?limit|资源使用上限|usage.?limit", re.I)


def _blame_upstream(text: str) -> None:
    if _THROTTLE_RE.search(text or ""):
        pytest.skip(f"上游免费额度限流（非引擎缺陷）：{(text or '')[:180]}")
    pytest.fail(f"真实 server 回合失败：{(text or '')[:400]}")


def _model_ref() -> dict:
    provider_id, model_id = _SMOKE_MODEL.split("/", 1)
    return {"providerID": provider_id, "modelID": model_id}


def _find_server() -> str | None:
    if os.environ.get("AGENTTEAM_OC_SMOKE") == "0":
        return None
    for url in _SMOKE_URLS:
        if not url:
            continue
        try:
            client = OpenCodeClient(OpenCodeConfig(base_url=url, timeout=2))
            client.health()
            client.close()
            return url.rstrip("/")
        except Exception:
            continue
    return None


_SERVER_URL = _find_server()

pytestmark = pytest.mark.skipif(
    _SERVER_URL is None,
    reason="no real opencode server reachable "
           "(set AGENTTEAM_OC_SMOKE=1 with `opencode serve --port 4117`)",
)


@pytest.fixture(scope="module")
def client():
    c = OpenCodeClient(OpenCodeConfig(base_url=_SERVER_URL, timeout=120))
    yield c
    c.close()


def _wait_turn(c: OpenCodeClient, sid: str, turn_id: str,
               timeout: float = 90.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        done = c.assistant_done(sid, turn_id)
        if done is not None:
            return done
        err = c.turn_error(sid, turn_id)
        if err:
            _blame_upstream(err)
        time.sleep(1)
    return None


def test_health(client):
    assert client.health() is not None


def test_session_creation_accepts_unregistered_agent_label(client):
    """v2 无 title 字段，引擎用 agent 携带 worker 名 —— 未注册名字必须被接受。"""
    sess = client.create_session(model=_model_ref(), agent="agentteam-smoke-worker")
    sid = sess["id"]
    assert sid
    got = client.get_session(sid)
    assert got["id"] == sid
    # 空闲会话上的 interrupt 只要求不报错（v1 abort 的 v2 等价物）
    client.interrupt_session(sid)


def test_prompt_roundtrip_with_turn_anchor(client):
    """异步 prompt + 锚点轮询：本回合 assistant finish=stop 且文本非空。"""
    sess = client.create_session(model=_model_ref(), agent="agentteam-smoke")
    sid = sess["id"]
    resp = client.prompt_async(sid, "回答一个词：1+1=?")
    turn_id = OpenCodeClient.turn_message_id(resp)
    assert turn_id, "v2 prompt 响应应返回本轮用户消息 id 作为完成锚点"
    done = _wait_turn(client, sid, turn_id)
    assert done is not None, "回合未在超时内 finish=stop"
    assert done["type"] == "assistant"
    assert (done.get("finish") or "") == "stop"
    assert client.final_text(sid, turn_id).strip()
    # v2 会话对象聚合恒为 0，用量只能逐消息累加（实测 §8）
    assert client.message_tokens(sid) > 0


def test_messages_newest_first_and_history_durable(client):
    sess = client.create_session(model=_model_ref(), agent="agentteam-smoke-hist")
    sid = sess["id"]
    resp = client.prompt_async(sid, "只回答两个字：好的")
    turn_id = OpenCodeClient.turn_message_id(resp)
    assert _wait_turn(client, sid, turn_id) is not None
    msgs = client.messages(sid)
    ids = [m.get("id") for m in msgs]
    assert turn_id in ids, "锚点用户消息应可在消息列表中找到"
    # 倒序：锚点之前的消息（本回合产出）排在锚点之前
    assert ids.index(turn_id) >= 1
    history = client.history(sid)
    assert history, "history 应返回 durable 事件"
    assert all("durable" in e or "type" in e for e in history[:5])


def test_sse_events_carry_data_payload(client):
    """v2 事件载荷在 data（v1 是 properties）—— EventMapper 依赖此形状。"""
    seen: list[dict] = []
    stop = threading.Event()

    def handler(event):
        if not stop.is_set():
            seen.append(event)

    client.subscribe(handler)
    try:
        sess = client.create_session(model=_model_ref(), agent="agentteam-smoke-sse")
        sid = sess["id"]
        resp = client.prompt_async(sid, "只回答一个词：你好")
        turn_id = OpenCodeClient.turn_message_id(resp)
        _wait_turn(client, sid, turn_id, timeout=60)
        time.sleep(0.5)
    finally:
        stop.set()
        client.unsubscribe(handler)

    own = [e for e in seen
           if (e.get("data") or {}).get("sessionID") == sid]
    assert own, "SSE 应推送到本会话的 session.next.* 事件"
    assert any(str(e.get("type", "")).startswith("session.next.") for e in own)


def test_v2_contract_shape_and_gaps(client):
    """固定 v2 契约边界（迁移三处退化/一处坑的实测依据）：
    1) model 引用是 {id, providerID}，v1 的 modelID 会被 schema 拒绝；
    2) 无会话级 permission 规则集端点（v1 面仍在，但 v2 会话不受其约束）；
    3) prompt 带 v1 的 format 字段必须被忽略而不是 500（退化不应变成崩溃）。
    """
    import requests

    base = _SERVER_URL
    provider_id, model_id = _SMOKE_MODEL.split("/", 1)
    bad = requests.post(f"{base}/api/session", json={
        "model": {"providerID": provider_id, "modelID": model_id}}, timeout=30)
    assert bad.status_code == 400, "v2 不接受 v1 的 modelID 字段名（实测 schema 拒绝）"

    r = requests.post(f"{base}/api/session", json={
        "agent": "agentteam-smoke-cap",
        "model": {"id": model_id, "providerID": provider_id},
    }, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sid = r.json()["data"]["id"]
    assert requests.get(f"{base}/api/permission/request",
                        timeout=30).status_code < 500
    r2 = requests.post(f"{base}/api/session/{sid}/prompt", json={
        "prompt": {"text": "只回答一个词：谢谢", "format": {"type": "json_schema"}},
        "delivery": "queue", "resume": True,
    }, timeout=30)
    assert r2.status_code < 400


def _explain(api, run: dict | None, run_id: str) -> str:
    """失败时把 trace 里的 error 事件带进断言消息。

    真实 server 冒烟是整套里唯一的非确定性层（免费模型会限流、返回空回合），
    只报 status='failed' 无法区分「引擎缺陷」和「上游抖动」，所以主动取回
    RunManager 落的 error 事件。
    """
    if run is None:
        return "run never appeared", ""
    try:
        events = api.get(f"/api/runs/{run_id}/trace").json()
    except Exception as e:  # noqa: BLE001
        return f"status={run['status']} (trace unreadable: {e})", ""
    errs = [str(e.get("payload")) for e in events
            if e["event_type"] == "error"]
    text = (f"status={run['status']} "
            f"trace={[e['event_type'] for e in events]} "
            f"errors={errs}")
    return text, " ".join(errs)


def test_harness_team_end_to_end(client, tmp_path):
    """完整套壳流程跑真实 server：注册团队→提交→completed→事件词表。"""
    from fastapi.testclient import TestClient

    from agentteam.api.server import create_app

    os.environ["AGENTTEAM_OPENCODE_URL"] = _SERVER_URL
    os.environ["AGENTTEAM_OC_MODEL"] = _SMOKE_MODEL
    app = create_app(
        db_path=str(tmp_path / "smoke.db"), web_dist=None,
        harness_enabled=True,
    )
    api = TestClient(app)
    team = {
        "name": "oc_smoke_e2e",
        "description": "真实冒烟团队",
        "engine": "opencode",
        "root": {
            "name": "leader", "role": "supervisor",
            "system_prompt": "你是研发主管，尽量拆成最少步骤。",
            "children": [{
                "name": "writer", "role": "worker",
                "system_prompt": "你是写手，直接用文本作答，不调用工具。",
                "tools": [],
            }],
        },
        # 与 _SMOKE_MODEL 对齐：ensure_backend 会拿 team.default_model 去
        # PATCH provider 配置，写一个本环境没有 key 的 qwen 只会误导排查。
        "default_model": {"provider": _SMOKE_MODEL.split("/")[0],
                          "name": _SMOKE_MODEL.split("/", 1)[1]},
        "skills": [],
        "mcp_servers": [],
    }
    api.post("/api/teams", json=team)
    run_id = api.post(
        "/api/runs",
        json={"team_name": "oc_smoke_e2e",
              "task": "用一句话介绍什么是多智能体系统，writer 直接作答。"},
    ).json()["run_id"]
    deadline = time.time() + 180
    run = None
    while time.time() < deadline:
        run = api.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("completed", "failed", "interrupted", "cancelled"):
            break
        time.sleep(1)
    assert run is not None, _explain(api, run, run_id)[0]
    if run["status"] != "completed":
        detail, err_text = _explain(api, run, run_id)
        if err_text:
            _blame_upstream(err_text)
        pytest.fail(detail)
    trace = [e["event_type"]
             for e in api.get(f"/api/runs/{run_id}/trace").json()]
    for expected in ("run_start", "leader_plan", "worker_start",
                     "worker_end", "leader_review", "run_end"):
        assert expected in trace, (expected, trace)
    assert run.get("total_tokens", 0) > 0
