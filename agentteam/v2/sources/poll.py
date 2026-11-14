"""轮询型 Source 骨架(GitHub/飞书)。

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
        return []


class FeishuSource(SourceBase):
    """飞书群消息源(事件订阅回推为主,poll 备选)。

    config: {app_id, app_secret_env, chat_ids: [...]}
    TODO(P5+): 长连接/事件回调 → Trigger;身份 = open_id(Source 体系内)。
    """

    kind = "feishu"

    def poll(self) -> list[Trigger]:
        return []
