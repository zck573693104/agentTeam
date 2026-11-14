"""R2 补丁:dashboard 端点 + GitHub 入站源 + trigger 去重。"""
import io
import ast


def patch(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        assert old in s, f"pattern not found in {path}: {old[:60]!r}"
        s = s.replace(old, new, 1)
    ast.parse(s)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print("patched", path)


# ===== 1) repo 统计方法 =====
patch("agentteam/v2/storage/tickets.py", [
    ('''    def list_pending_ids(self) -> list[str]:''',
     '''    def count_by_status(self) -> dict[str, int]:
        """B8 dashboard: 各状态工单数。"""
        rows = self._fetchall(
            "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status"
        )
        return {r["status"]: r["n"] for r in rows}

    def list_pending_ids(self) -> list[str]:'''),
])

patch("agentteam/v2/storage/outbox.py", [
    ('''    def list_for_ticket(self, ticket_id: str, limit: int = 100) -> list[dict]:''',
     '''    def counts(self) -> dict[str, int]:
        """B8 dashboard: outbox 各状态计数。"""
        rows = self._fetchall("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def list_for_ticket(self, ticket_id: str, limit: int = 100) -> list[dict]:'''),
])

patch("agentteam/v2/storage/events.py", [
    ('''    def latest_event_id(self, ticket_id: str) -> int:
        row = self._fetchone(
            "SELECT MAX(id) AS m FROM events WHERE ticket_id = ?", (ticket_id,)
        )
        return row["m"] if row and row["m"] is not None else 0''',
     '''    def latest_event_id(self, ticket_id: str) -> int:
        row = self._fetchone(
            "SELECT MAX(id) AS m FROM events WHERE ticket_id = ?", (ticket_id,)
        )
        return row["m"] if row and row["m"] is not None else 0

    def recent_count(self, hours: float = 1.0) -> int:
        """B8 dashboard: 近 N 小时事件量(事件速率)。"""
        row = self._fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE created_at >= "
            "datetime('now', ?)",
            (f"-{hours} hours",),
        )
        return row["n"]

    def total_tokens(self) -> int:
        """A1/B8: 全部会话 token 总量。"""
        row = self._fetchone(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS t FROM sessions"
        )
        return int(row["t"] or 0)'''),
])

# ===== 2) dashboard 端点 =====
patch("agentteam/v2/api/router.py", [
    ('''    @router.get("/skills")''',
     '''    @router.get("/dashboard")
    def dashboard():
        """B8 总览: 工单/预算/worker/注意件/outbox/来源 速率与状态。"""
        by_status = v2.ticket_repo.count_by_status()
        return {
            "tickets": {
                "by_status": by_status,
                "total": sum(by_status.values()),
            },
            "tokens_total": v2.event_repo.total_tokens(),
            "workers": {
                "total": len(v2.worker_repo.list_all()),
                "enabled": len(v2.worker_repo.list_all(include_disabled=False)),
            },
            "attentions_pending": len(v2.attention_repo.list_by_status("pending")),
            "outbox": v2.outbox_repo.counts(),
            "sources": {
                "total": len(v2.source_repo.list_all()),
                "enabled": len([s for s in v2.source_repo.list_all() if s["enabled"]]),
            },
            "threads": len(v2.thread_repo.list_threads(limit=500)),
            "events_recent_1h": v2.event_repo.recent_count(1.0),
        }

    @router.get("/skills")'''),
])

# ===== 3) ThreadRepo.append_trigger 外部 ID 去重 =====
patch("agentteam/v2/storage/threads.py", [
    ('''    def append_trigger(
        self, thread_id: str, actor: str, text: str, raw: dict | None = None,
    ) -> dict:
        """入站事件 → Trigger(不可变)。"""
        trigger_id = uuid4().hex
        now = _now()
        self._execute(
            "INSERT INTO triggers (id, thread_id, actor, text, raw_json, at) VALUES (?,?,?,?,?,?)",
            (trigger_id, thread_id, actor, text,
             json.dumps(raw or {}, ensure_ascii=False), now),
        )
        return {"id": trigger_id, "thread_id": thread_id, "actor": actor,
                "text": text, "at": now}''',
     '''    def append_trigger(
        self, thread_id: str, actor: str, text: str, raw: dict | None = None,
    ) -> dict:
        """入站事件 → Trigger(不可变)。

        C10 轮询源去重: raw.external_id 存在时,同 external_id 不重复落库
        (轮询游标重启回退会重发同事件),返回已有 trigger。
        """
        external_id = (raw or {}).get("external_id")
        if external_id:
            existing = self._fetchone(
                "SELECT id, thread_id, actor, text, at FROM triggers "
                "WHERE thread_id = ? AND json_extract(raw_json, '$.external_id') = ?",
                (thread_id, external_id),
            )
            if existing:
                return dict(existing)
        trigger_id = uuid4().hex
        now = _now()
        self._execute(
            "INSERT INTO triggers (id, thread_id, actor, text, raw_json, at) VALUES (?,?,?,?,?,?)",
            (trigger_id, thread_id, actor, text,
             json.dumps(raw or {}, ensure_ascii=False), now),
        )
        return {"id": trigger_id, "thread_id": thread_id, "actor": actor,
                "text": text, "at": now}'''),
])

# ===== 4) GitHubSource.poll 实现 =====
patch("agentteam/v2/sources/poll.py", [
    ('''"""轮询型 Source 骨架(GitHub/飞书)。

真实 API 接入(令牌管理、增量游标、限流)独立迭代;当前 poll() 返回空,
SourceDaemon 常驻循环已经可以驱动它们——接入时只补 poll() 实现。
"""
from __future__ import annotations

from agentteam.v2.sources.protocol import SourceBase, Trigger


class GitHubSource(SourceBase):
    """GitHub 仓库事件源(issues/PR 评论)。

    config: {repos: [...], token_env: "GITHUB_TOKEN", labels: [...]}
    TODO(P5+): REST 轮询 since 游标 + label 过滤 → Trigger。
    """

    kind = "github"

    def poll(self) -> list[Trigger]:
        return []''',
     '''"""轮询型 Source:GitHub 已实现;飞书为骨架。

安全铁律(spec §8): 外部文本是数据不是规则——Trigger 文本进入 prompt
前先过 gate;Source 身份独立(配置的 token 不与操作者混用)。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from agentteam.logging_config import get_logger
from agentteam.v2.sources.protocol import SourceBase, Trigger

logger = get_logger("v2.sources.poll")


class GitHubSource(SourceBase):
    """GitHub 仓库事件源(C10: issue 新建/评论 → Trigger)。

    config: {repos: ["owner/repo", ...], labels: ["agent"] 可选}
    token: 环境变量 GITHUB_TOKEN(只读权限即可)。
    游标: 每 repo 记最后处理到的 updated_at(实例内存;重启后从最近 20 条
    重放,靠 external_id 去重,不重复立项)。
    """

    kind = "github"
    API = "https://api.github.com"

    def __init__(self, name: str, config: dict, emit) -> None:
        super().__init__(name, config, emit)
        self._cursor: dict[str, str] = {}  # repo → last updated_at ISO
        self._token = os.environ.get(config.get("token_env", "GITHUB_TOKEN"), "")

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get(self, url: str, params: dict | None = None):
        return requests.get(url, headers=self._headers(), params=params or {}, timeout=15)

    def poll(self) -> list[Trigger]:
        out: list[Trigger] = []
        repos = list((self.config or {}).get("repos", []))
        labels = list((self.config or {}).get("labels", []))
        for repo in repos:
            try:
                resp = self._get(
                    f"{self.API}/repos/{repo}/issues",
                    {"state": "all", "sort": "updated", "direction": "desc", "per_page": 20},
                )
                if resp.status_code != 200:
                    logger.warning("github poll %s: HTTP %s", repo, resp.status_code)
                    continue
                for item in resp.json():
                    if "pull_request" in item:
                        continue  # PR 走独立通道(P5+)
                    if labels and not set(labels) <= {l["name"] for l in item.get("labels", [])}:
                        continue
                    updated = item["updated_at"]
                    if updated <= self._cursor.get(repo, ""):
                        continue
                    self._cursor[repo] = updated
                    ext_issue = f"{repo}#{item['number']}:issue:{item['updated_at']}"
                    out.append(Trigger(
                        actor=item.get("user", {}).get("login", "unknown"),
                        thread_key=f"{repo}#{item['number']}",
                        text=f"[{item['title']}] {item.get('body') or ''}"[:4000],
                        at=item["updated_at"],
                        raw={"external_id": ext_issue, "repo": repo,
                             "number": item["number"], "url": item["html_url"]},
                    ))
                    # 评论 → 同线程独立 Trigger(external_id 精确去重)
                    comments = self._get(
                        f"{self.API}/repos/{repo}/issues/{item['number']}/comments",
                        {"sort": "created", "direction": "desc", "per_page": 10},
                    )
                    for c in comments.json() if comments.status_code == 200 else []:
                        cext = f"{repo}#{item['number']}:comment:{c['id']}"
                        out.append(Trigger(
                            actor=c.get("user", {}).get("login", "unknown"),
                            thread_key=f"{repo}#{item['number']}",
                            text=(c.get("body") or "")[:4000],
                            at=c["created_at"],
                            raw={"external_id": cext, "repo": repo,
                                 "number": item["number"], "url": c["html_url"]},
                        ))
            except Exception:
                logger.exception("github poll %s failed", repo)
        return out'''),
])
print("ALL R2 PATCHED")
