"""SP12b IM 通知 outbox 测试。

覆盖:
- NotifyOutboxRepo:enqueue/pending/mark_sent/mark_retry/mark_dropped/回执查询
- OutboxWorker.run_once:成功发送、退避重试、上限 dropped、同 run 串行、
  坏 payload 隔离、重启恢复(新 worker 实例续发)
- NotificationService outbox 路径:只落库不直发
- GET /api/runs/{id}/notifications 回执 API
- v14 迁移:旧库(user_version=13)升级后表存在
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.config import Settings
from agentteam.notify import NotificationService
from agentteam.notify.outbox import OutboxWorker
from agentteam.storage.db import run_migrations
from agentteam.storage.notify import NotifyOutboxRepo


# ================= 夹具 =================


@pytest.fixture
def repo():
    """内存库 outbox repo(_init_table 自建表,不依赖 init_db)。"""
    conn = sqlite3.connect(":memory:")
    yield NotifyOutboxRepo(conn)
    conn.close()


def _enqueue_two(repo, run_id="r1"):
    """同一 run 两条通知(id 序 = 入队序)。"""
    repo.enqueue(run_id, "run_interrupted", "http://hook",
                 json.dumps({"n": 1}), now=100.0)
    repo.enqueue(run_id, "run_end", "http://hook",
                 json.dumps({"n": 2}), now=100.0)


class _FlakySender:
    """可编程发送器:按 url+n 决定成败,记录调用。"""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: dict) -> None:
        self.calls.append((url, body))
        if len(self.calls) <= self.fail_times:
            raise ConnectionError(f"boom #{len(self.calls)}")


# ================= NotifyOutboxRepo =================


def test_repo_enqueue_and_pending_order(repo):
    _enqueue_two(repo)
    _enqueue_two(repo, run_id="r2")
    rows = repo.pending_rows()
    # (run_id, id) 序:r1 两条在前,r2 两条在后
    assert [(r["run_id"], json.loads(r["payload_json"])["n"]) for r in rows] == [
        ("r1", 1), ("r1", 2), ("r2", 1), ("r2", 2),
    ]


def test_repo_mark_lifecycle(repo):
    _enqueue_two(repo)
    rows = repo.pending_rows()
    repo.mark_sent(rows[0]["id"], sent_at=123.0)
    repo.mark_retry(rows[1]["id"], attempts=2, next_retry_at=200.0,
                    last_error="boom")
    sent_rows = [r for r in repo.list_for_run("r1") if r["status"] == "sent"]
    assert len(sent_rows) == 1 and sent_rows[0]["sent_at"] == 123.0
    retry_rows = [r for r in repo.list_for_run("r1") if r["status"] == "pending"]
    assert retry_rows[0]["attempts"] == 2
    assert retry_rows[0]["next_retry_at"] == 200.0
    assert retry_rows[0]["last_error"] == "boom"


def test_repo_mark_dropped_keeps_error(repo):
    _enqueue_two(repo)
    row = repo.pending_rows()[0]
    repo.mark_dropped(row["id"], "gave up")
    dropped = [r for r in repo.list_for_run("r1") if r["status"] == "dropped"]
    assert dropped[0]["last_error"] == "gave up"
    # 队头 dropped 后,同 run 第二条仍在等(pending 恰好剩一条)
    assert len(repo.pending_rows()) == 1
    assert repo.pending_rows()[0]["event"] == "run_end"


# ================= OutboxWorker.run_once =================


def test_run_once_sends_and_marks_sent(repo):
    _enqueue_two(repo)
    sender = _FlakySender()
    worker = OutboxWorker(repo, sender=sender, retry_max=5, backoff_base=5.0)
    assert worker.run_once(now=1000.0) is True
    # 同 run 串行:一轮只发队头一条
    assert len(sender.calls) == 1
    assert sender.calls[0][1] == {"n": 1}
    statuses = {r["id"]: r["status"] for r in repo.list_for_run("r1")}
    assert list(statuses.values())[0] == "sent"
    assert list(statuses.values())[1] == "pending"


def test_run_once_second_row_after_first_sent(repo):
    _enqueue_two(repo)
    sender = _FlakySender()
    worker = OutboxWorker(repo, sender=sender)
    worker.run_once(now=1000.0)   # 第一条 sent
    worker.run_once(now=1001.0)   # 第二条出队
    assert [b["n"] for _, b in sender.calls] == [1, 2]


def test_run_once_retry_backoff_doubles(repo):
    _enqueue_two(repo)
    sender = _FlakySender(fail_times=3)
    worker = OutboxWorker(repo, sender=sender, retry_max=5, backoff_base=5.0)
    worker.run_once(now=1000.0)   # 失败 #1 → next 1005
    rows = repo.pending_rows()
    assert rows[0]["attempts"] == 1 and rows[0]["next_retry_at"] == 1005.0
    worker.run_once(now=1006.0)   # 失败 #2 → next 1006+10
    assert repo.pending_rows()[0]["attempts"] == 2
    assert repo.pending_rows()[0]["next_retry_at"] == 1016.0
    worker.run_once(now=1020.0)   # 失败 #3 → next 1020+20
    assert repo.pending_rows()[0]["next_retry_at"] == 1040.0


def test_run_once_backoff_blocks_run_head(repo):
    """队头退避未到期 → 整个 run 本轮跳过(后续行隐式被阻塞)。"""
    _enqueue_two(repo)
    sender = _FlakySender(fail_times=1)
    worker = OutboxWorker(repo, sender=sender)
    worker.run_once(now=1000.0)   # 队头失败,退避到 1005
    sender.calls.clear()
    assert worker.run_once(now=1002.0) is False   # 队头未到期
    assert sender.calls == []
    assert worker.run_once(now=1006.0) is True    # 到期,发的是队头
    assert sender.calls[0][1] == {"n": 1}


def test_run_once_max_attempts_drops(repo):
    _enqueue_two(repo)
    sender = _FlakySender(fail_times=99)
    worker = OutboxWorker(repo, sender=sender, retry_max=5, backoff_base=5.0)
    now = 1000.0
    for _ in range(5):
        worker.run_once(now=now)
        rows = repo.pending_rows()
        now = rows[0]["next_retry_at"] if rows else now
    dropped = [r for r in repo.list_for_run("r1") if r["status"] == "dropped"]
    assert len(dropped) == 1 and "boom" in dropped[0]["last_error"]
    # 队头 dropped 后,同 run 第二条解除阻塞
    assert repo.pending_rows()[0]["event"] == "run_end"


def test_run_once_bad_payload_dropped_isolated(repo):
    """payload 损坏 → dropped,不堵死该 run,也不影响其他 run。"""
    repo.enqueue("r1", "run_end", "http://hook", "{not-json", now=100.0)
    repo.enqueue("r1", "run_end", "http://hook", json.dumps({"n": 2}), now=100.0)
    repo.enqueue("r2", "run_end", "http://hook", json.dumps({"n": 3}), now=100.0)
    sender = _FlakySender()
    worker = OutboxWorker(repo, sender=sender)
    worker.run_once(now=1000.0)
    by_status = {}
    for r in repo.list_for_run("r1"):
        by_status.setdefault(r["status"], []).append(r["event"])
    # 坏 payload 行被隔离;同 run 第二条解除阻塞进入待发
    assert by_status.get("dropped") == ["run_end"]
    assert by_status.get("pending") == ["run_end"]
    # 其他 run 不受影响:同轮已发出
    r2 = repo.list_for_run("r2")
    assert len(r2) == 1 and r2[0]["status"] == "sent"


def test_run_once_empty_returns_false(repo):
    worker = OutboxWorker(repo, sender=_FlakySender())
    assert worker.run_once() is False


def test_restart_recovery_sends_pending(repo):
    """重启恢复:新 worker 实例在同一 repo 上续发 pending(先存后发)。"""
    _enqueue_two(repo)
    first = OutboxWorker(repo, sender=_FlakySender(fail_times=1))
    first.run_once(now=1000.0)   # 失败,落库
    # 进程"重启":全新 worker 实例接手同一张表
    second = OutboxWorker(repo, sender=_FlakySender())
    assert second.run_once(now=2000.0) is True
    sent = [r for r in repo.list_for_run("r1") if r["status"] == "sent"]
    assert len(sent) == 1 and json.loads(sent[0]["payload_json"]) == {"n": 1}


# ================= NotificationService outbox 路径 =================


class _FakeRunRepo:
    def __init__(self, rows: dict):
        self._rows = rows

    def get_run(self, run_id):
        return self._rows.get(run_id)


def _settings(**kw) -> Settings:
    base = dict(
        notify_webhook_url="http://global",
        notify_provider="generic",
        console_url="http://console",
        notify_timeout_seconds=1.0,
    )
    base.update(kw)
    return Settings(**base)


def test_service_with_outbox_enqueues_only(repo, monkeypatch):
    """outbox 路径:只落库,绝不直发(连线程都不起)。"""
    started = []
    monkeypatch.setattr(
        "agentteam.notify.service.threading.Thread",
        lambda *a, **kw: started.append(1),
    )
    svc = NotificationService(
        _FakeRunRepo({"r1": {"team_name": "t", "task": "x", "total_tokens": 1}}),
        None, settings=_settings(), outbox=repo,
    )
    svc.on_run_event("r1", "run_end")
    assert started == []            # 未直发
    rows = repo.list_for_run("r1")
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert json.loads(rows[0]["payload_json"])["title"].startswith("✅")
    assert rows[0]["url"] == "http://global"


def test_service_without_outbox_legacy_path(monkeypatch):
    """未注入 outbox → 旧行为(daemon 线程直发)。"""
    records = []

    class _SyncThread:
        def __init__(self, target=None, args=(), **kw):
            self._t, self._a = target, args

        def start(self):
            self._t(*self._a)

    monkeypatch.setattr("agentteam.notify.service.threading.Thread", _SyncThread)
    monkeypatch.setattr(
        "agentteam.notify.service.requests.post",
        lambda url, json=None, timeout=None: records.append(url)
        or type("R", (), {"status_code": 200})(),
    )
    svc = NotificationService(
        _FakeRunRepo({"r1": {"team_name": "t", "task": "x", "total_tokens": 1}}),
        None, settings=_settings(),
    )
    svc.on_run_event("r1", "run_end")
    assert records == ["http://global"]


# ================= 回执 API =================


def test_notifications_endpoint():
    """GET /api/runs/{id}/notifications 返回回执列表;未知 run 404。"""
    from agentteam.api.routes.runs import runs_router

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    repo = NotifyOutboxRepo(conn)
    repo.enqueue("r1", "run_interrupted", "http://hook", "{}", now=100.0)
    repo.enqueue("r1", "run_end", "http://hook", "{}", now=100.0)
    repo.mark_dropped(1, "gave up")
    repo.mark_sent(2, sent_at=110.0)

    run_repo = _FakeRunRepo({"r1": {"id": "r1", "status": "completed"}})
    app = FastAPI()
    app.include_router(runs_router(
        run_manager=None, team_store=None, model_provider=None,
        tool_registry=None, run_repo=run_repo, audit_repo=None,
        event_bus=None, notify_outbox_repo=repo,
    ))
    client = TestClient(app)

    resp = client.get("/api/runs/r1/notifications")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["status"] for i in items] == ["dropped", "sent"]
    assert items[0]["last_error"] == "gave up"
    assert items[1]["sent_at"] == 110.0

    assert client.get("/api/runs/ghost/notifications").status_code == 404
    conn.close()


def test_notifications_endpoint_no_outbox_returns_empty():
    """未装配 outbox(向后兼容)时返回空列表而非 500。"""
    from agentteam.api.routes.runs import runs_router

    run_repo = _FakeRunRepo({"r1": {"id": "r1", "status": "completed"}})
    app = FastAPI()
    app.include_router(runs_router(
        run_manager=None, team_store=None, model_provider=None,
        tool_registry=None, run_repo=run_repo, audit_repo=None,
        event_bus=None,
    ))
    client = TestClient(app)
    assert client.get("/api/runs/r1/notifications").json() == []


# ================= v14 迁移 =================


def test_migration_v14_creates_outbox_table(tmp_path):
    """旧库(user_version=13)跑 run_migrations 后 notify_outbox 表存在。"""
    conn = sqlite3.connect(tmp_path / "old.db")
    # 模拟 v13 止步的旧库:建一张 v1 基础表把 user_version 抬到 13
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    # run_migrations 返回本次执行的 migration 数:13→14 恰好 1 条
    assert run_migrations(conn) == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='notify_outbox'"
    ).fetchall()
    assert rows
    conn.close()
