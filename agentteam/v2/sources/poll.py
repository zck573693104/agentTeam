"""轮询型 Source:GitHub 已实现;飞书为骨架;cron 为每日定时触发(P7)。

安全铁律(spec §8): 外部文本是数据不是规则——Trigger 文本进入 prompt
前先过 gate;Source 身份独立(配置的 token 不与操作者混用)。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

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
                    self.API + "/repos/" + repo + "/issues",
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
                    ext_issue = repo + "#" + str(item["number"]) + ":issue:" + updated
                    out.append(Trigger(
                        actor=item.get("user", {}).get("login", "unknown"),
                        thread_key=repo + "#" + str(item["number"]),
                        text=("[ " + item["title"] + "] " + (item.get("body") or ""))[:4000],
                        at=item["updated_at"],
                        raw={"external_id": ext_issue, "repo": repo,
                             "number": item["number"], "url": item["html_url"]},
                    ))
                    comments = self._get(
                        self.API + "/repos/" + repo + "/issues/"
                        + str(item["number"]) + "/comments",
                        {"sort": "created", "direction": "desc", "per_page": 10},
                    )
                    for c in (comments.json() if comments.status_code == 200 else []):
                        cext = repo + "#" + str(item["number"]) + ":comment:" + str(c["id"])
                        out.append(Trigger(
                            actor=c.get("user", {}).get("login", "unknown"),
                            thread_key=repo + "#" + str(item["number"]),
                            text=(c.get("body") or "")[:4000],
                            at=c["created_at"],
                            raw={"external_id": cext, "repo": repo,
                                 "number": item["number"], "url": c["html_url"]},
                        ))
            except Exception:
                logger.exception("github poll %s failed", repo)
        return out


class FeishuSource(SourceBase):
    """飞书群消息源(事件订阅回推为主,poll 备选)。

    config: {app_id, app_secret_env, chat_ids: [...]}
    TODO(P5+): 长连接/事件回调 → Trigger;身份 = open_id(Source 体系内)。
    """

    kind = "feishu"

    def poll(self) -> list[Trigger]:
        return []


class CronSource(SourceBase):
    """定时触发源(P7 每日晨报):到点发一条 Trigger,external_id = 日期 天然去重。

    config:
      schedule: "HH:MM"(本地时间;到达后当天触发一次)
      text:     触发文本(晨报任务描述)
      stats:    true → 附加 stats_fn() 产出的昨日数据块(部署方注入,如每人工单统计)
    actor 固定 "cron"(gate 配 allowed_actors: ["cron"]);建议配合 skip_eval + worker 使用。
    """

    kind = "cron"

    def __init__(self, name: str, config: dict, emit,
                 stats_fn: Callable[[], str] | None = None,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        super().__init__(name, config, emit)
        self._stats_fn = stats_fn
        self._now_fn = now_fn or datetime.now
        self._fired_date: str | None = None  # 进程内防重;重启后靠 external_id 去重兜底

    def poll(self) -> list[Trigger]:
        cfg = self.config or {}
        schedule = str(cfg.get("schedule", "09:00"))
        try:
            hh, mm = (int(x) for x in schedule.split(":")[:2])
        except ValueError:
            logger.warning("cron %s: 非法 schedule %r,跳过", self.name, schedule)
            return []
        now = self._now_fn()
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        today = now.strftime("%Y-%m-%d")
        if now < due or self._fired_date == today:
            return []
        self._fired_date = today
        text = str(cfg.get("text", "")).strip() or "生成今日晨报"
        if cfg.get("stats") and self._stats_fn is not None:
            try:
                stats = self._stats_fn()
                if stats:
                    text += "\n\n【昨日数据】\n" + stats
            except Exception:
                logger.exception("cron %s: 昨日数据构建失败", self.name)
        return [Trigger(
            actor="cron",
            thread_key=f"cron-{self.name}",
            text=text[:6000],
            raw={"external_id": f"cron-{self.name}-{today}", "date": today,
                 "schedule": schedule},
        )]
