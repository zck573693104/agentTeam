"""P7 定时晨报测试:cron 源触发/去重/昨日数据注入 + skip_eval + 源指定承接人。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage

from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.sources.poll import CronSource
from agentteam.v2.sources.protocol import Trigger
from tests.conftest import FakeLLM, FakeModelProvider


def _cron(config: dict, now_hm=("09", "05"), stats_fn=None):
    def now_fn() -> datetime:
        return datetime(2026, 11, 22, int(now_hm[0]), int(now_hm[1]))

    return CronSource("briefing", config, lambda t: {}, stats_fn=stats_fn, now_fn=now_fn)


def test_cron_fires_once_per_day_with_stats():
    stats_calls: list[int] = []

    def stats_fn() -> str:
        stats_calls.append(1)
        return "- pms_ops: 昨日完成 2 / 失败 1;当前在途 1"

    src = _cron({"schedule": "09:00", "text": "生成今日晨报", "stats": True},
                stats_fn=stats_fn)

    # 未到点 → 不触发
    src2 = _cron({"schedule": "23:00", "text": "生成今日晨报", "stats": True},
                 stats_fn=stats_fn)
    assert src2.poll() == []

    # 到点 → 触发一次,带昨日数据
    triggers = src.poll()
    assert len(triggers) == 1 and stats_calls == [1]
    assert "生成今日晨报" in triggers[0].text
    assert "【昨日数据】" in triggers[0].text and "pms_ops" in triggers[0].text
    assert triggers[0].raw["external_id"] == "cron-briefing-2026-11-22"
    assert triggers[0].actor == "cron"

    # 同日再 poll → 不重复(进程内防重)
    assert src.poll() == []


def test_cron_invalid_schedule_no_fire():
    src = _cron({"schedule": "bad"})
    assert src.poll() == []


def test_skip_eval_and_duplicate_short_circuit(tmp_path: Path):
    """受信机器源跳过 Eval;同 external_id 回放短路不重复立项。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentteam.v2.api.deps import create_v2_components
    from agentteam.v2.api.router import build_v2_router
    from agentteam.v2.domain.events import TicketStatus
    from tests.v2.conftest import wait_ticket_terminal

    main = FakeLLM()
    main.set_invoke_responses([AIMessage(content="晨报已生成")])
    v2 = create_v2_components(db_path=str(tmp_path / "v2.db"),
                              workspace_root=tmp_path / "ws",
                              model_provider=FakeModelProvider({"qwen-max": main}))
    v2.worker_repo.upsert(WorkerDef(name="pms_lead"))
    v2.scheduler.start()
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api")
    client = TestClient(app)

    # 受信机器源:skip_eval + 指定承接人
    client.post("/api/sources", json={"name": "cron_daily", "kind": "cron", "config": {
        "schedule": "09:00", "text": "生成今日晨报", "skip_eval": True,
        "worker": "pms_lead", "allowed_actors": ["cron"], "mode": "auto",
    }})
    body = client.post("/api/sources/cron_daily/webhook", json={
        "actor": "cron", "thread_key": "cron-cron_daily",
        "text": "生成今日晨报",
        "raw": {"external_id": "cron-cron_daily-2026-11-22"},
    }).json()
    assert body["decision"] == "accepted"      # skip_eval: 不研判直接接受
    assert len(main.invoked_structured) == 0   # Eval 没被调用
    tid = body["ticket_id"]
    assert wait_ticket_terminal(v2, tid).status == TicketStatus.DONE
    assert v2.ticket_repo.get(tid).worker_name == "pms_lead"  # 源指定承接人

    # 同 external_id 回放 → duplicate 短路,不重复立项
    body2 = client.post("/api/sources/cron_daily/webhook", json={
        "actor": "cron", "thread_key": "cron-cron_daily",
        "text": "生成今日晨报",
        "raw": {"external_id": "cron-cron_daily-2026-11-22"},
    }).json()
    assert body2["decision"] == "duplicate"
    done_tickets = [t for t in v2.ticket_repo.list_by_status(TicketStatus.DONE)
                    if t.worker_name == "pms_lead"]
    assert len(done_tickets) == 1
    v2.scheduler.shutdown(wait=True)
    v2.conn.close()
