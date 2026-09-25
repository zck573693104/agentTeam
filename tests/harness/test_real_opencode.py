"""真实 opencode server 冒烟测试（skipif 保护）。

跳过条件（满足任一即 skip）：
- AGENTTEAM_OC_SMOKE=0 显式关闭
- 探测不到真实 server（默认依次尝试 AGENTTEAM_OPENCODE_URL 与
  http://127.0.0.1:4117，1 秒超时）

这些测试走真实 LLM（免费模型 opencode/ling-3.0-flash-fin-free），
验证套壳引擎对真实 opencode v1.18.32 的端到端可用性；断言基于
确定性行为（终态、事件词表），不依赖具体模型输出内容。
"""
from __future__ import annotations

import os
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


def test_health(client):
    assert client.health() is not None


def test_session_lifecycle(client):
    sess = client.create_session(title="agentteam-smoke-lifecycle")
    sid = sess["id"]
    got = client.get_session(sid)
    assert got["id"] == sid
    assert client.abort_session(sid) is True
    client.delete_session(sid)


def test_prompt_roundtrip(client):
    """同步 prompt 往返：真实模型回答非空（内容不敏感）。"""
    sess = client.create_session(title="agentteam-smoke-prompt")
    resp = client.prompt(
        sess["id"], "回答一个词：1+1=?",
        system="你是算术器，只回答数字。",
        model={"providerID": _SMOKE_MODEL.split("/", 1)[0],
               "modelID": _SMOKE_MODEL.split("/", 1)[1]},
    )
    texts = [p.get("text", "") for p in resp.get("parts", [])
             if p.get("type") == "text"]
    assert any(t.strip() for t in texts)
    client.delete_session(sess["id"])


def test_harness_team_end_to_end(client, tmp_path):
    """完整套壳流程跑真实 server：注册团队→提交→completed→事件词表。"""
    from fastapi.testclient import TestClient

    from agentteam.api.server import create_app
    from agentteam.tools.registry import ToolRegistry

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
        "default_model": {"provider": "qwen", "name": "qwen-max"},
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
    assert run is not None and run["status"] == "completed", run
    trace = [e["event_type"]
             for e in api.get(f"/api/runs/{run_id}/trace").json()]
    for expected in ("run_start", "leader_plan", "worker_start",
                     "worker_end", "leader_review", "run_end"):
        assert expected in trace, (expected, trace)
    assert run.get("total_tokens", 0) > 0
