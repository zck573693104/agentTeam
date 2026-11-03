"""IM webhook 通知服务。

设计约束:
- 通知绝不影响 run 主流程:发送在 daemon 线程,HTTP 异常/非 2xx 全部吞没,
  只记 warning 日志。
- webhook 地址解析:Team.webhook_url(团队级)优先,回退全局
  AGENTTEAM_NOTIFY_WEBHOOK_URL;两者都空则静默跳过(零配置兼容)。
- 消息格式按 AGENTTEAM_NOTIFY_PROVIDER 适配:飞书/企业微信/钉钉 bot
  各有固定的请求体 schema,generic 输出透明 JSON(自建接收端/Matrix 桥)。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

from agentteam.config import Settings, get_settings
from agentteam.logging_config import get_logger

logger = get_logger("api.notify")

# 触发通知的事件白名单:等待人工(interrupted,两引擎统一在 RunManager 标记)、
# 正常完成、失败。取消不打扰(用户自己操作的,无需回执)。
_NOTIFY_EVENTS = frozenset({"run_interrupted", "run_end", "error"})

# SP12a 消息预算(dsh-superagent 注意力经济学:每条通知都是注意力成本):
# 正文行数与字符数上限,超出截断。截断尾行不带链接假设(通知可能无 link)。
_MAX_LINES = 8
_MAX_CHARS = 300
_TRUNCATE_TAIL = "\n…(内容已截断,详见控制台)"


def _clip(text: str) -> str:
    """正文超长截断:保留前 _MAX_CHARS 字符并追加截断尾行。"""
    if len(text) <= _MAX_CHARS:
        return text
    return text[:_MAX_CHARS] + _TRUNCATE_TAIL


def format_payload(
    provider: str, title: str, lines: list[str], link: str | None = None,
) -> dict[str, Any]:
    """按 IM provider 把 (title, lines, link) 适配成对应 bot 的请求体。

    纯函数(便于单测)。text 统一为纯文本 + 链接尾行;markdown 类 provider
    (wecom/dingtalk)用同一文本,bot 端按纯文本渲染也完全可读。
    SP12a:行数截到 _MAX_LINES,全文截到 _MAX_CHARS(消息预算)。
    """
    lines = list(lines[:_MAX_LINES])
    body_text = "\n".join([title, *lines])
    if link:
        body_text += f"\n👉 {link}"
    body_text = _clip(body_text)
    p = (provider or "generic").strip().lower()
    if p == "feishu":
        # 飞书自定义 bot:https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
        return {"msg_type": "text", "content": {"text": body_text}}
    if p == "wecom":
        # 企业微信群机器人
        return {"msgtype": "markdown", "markdown": {"content": body_text}}
    if p == "dingtalk":
        # 钉钉自定义机器人
        return {"msgtype": "markdown",
                "markdown": {"title": title, "text": body_text}}
    # 通用 JSON 端点(自建接收端/网关/Matrix 桥):字段自解释。
    # 注意:text 不含 link(结构化字段单列,向后兼容),仅做消息预算截断
    return {"title": title, "text": _clip("\n".join(lines)), "link": link}


def _mask_url(url: str) -> str:
    """日志用脱敏:webhook URL 常带签名 query 参数,只保留到 path。"""
    return url.split("?", 1)[0]


class NotificationService:
    """run 事件 → IM webhook。由 RunManager 在关键生命周期点调用。

    SP12b:注入 outbox(NotifyOutboxRepo)后走「先存后发」——本类只落库,
    HTTP 发送交给 OutboxWorker(退避重试/同 run 串行/重启续发);
    未注入 outbox 时保持旧行为(daemon 线程直发,异常全吞),零依赖可单测。
    """

    def __init__(
        self,
        run_repo,
        team_store=None,
        *,
        settings: Settings | None = None,
        outbox=None,
    ) -> None:
        self._run_repo = run_repo
        self._team_store = team_store
        self._settings = settings or get_settings()
        self._outbox = outbox

    # ---------------- 事件入口(RunManager 调用) ----------------

    def on_run_event(
        self, run_id: str, event_type: str, payload: dict | None = None,
    ) -> None:
        """处理一个 run 事件;不满足通知条件时静默返回。

        outbox 路径(SP12b):组装请求体后 enqueue 落库即返回,发送在
        OutboxWorker 后台线程——run 事件路径上只做决策 + 组装 + 落库。
        旧路径:daemon 线程直发(不阻塞 run 状态推进)。
        """
        if event_type not in _NOTIFY_EVENTS:
            return
        url = self._resolve_url(run_id)
        if not url:
            return
        run = self._run_row(run_id)
        team_name = run.get("team_name") if run else None
        task = (run.get("task") or "") if run else ""
        tokens = run.get("total_tokens") or 0 if run else 0
        title, lines = self._render(event_type, run_id, team_name, task, payload, tokens)
        base = self._settings.console_url.rstrip("/")
        link = f"{base}/runs/{run_id}"
        body = format_payload(self._settings.notify_provider, title, lines, link)
        if self._outbox is not None:
            self._outbox.enqueue(
                run_id, event_type, url,
                json.dumps(body, ensure_ascii=False), time.time(),
            )
            return
        threading.Thread(
            target=self._send, args=(url, body),
            name=f"agentteam-notify-{run_id[:8]}", daemon=True,
        ).start()

    # ---------------- 内部实现 ----------------

    def _run_row(self, run_id: str) -> dict | None:
        try:
            run = self._run_repo.get_run(run_id)
        except Exception:
            logger.exception("notify: read run %s failed", run_id)
            return None
        return dict(run) if run is not None else None

    def _resolve_url(self, run_id: str) -> str | None:
        """webhook 地址:Team.webhook_url 优先,回退全局配置。"""
        if self._team_store is not None:
            run = self._run_row(run_id)
            team_name = run.get("team_name") if run else None
            if team_name:
                try:
                    team = self._team_store.get(team_name)
                except Exception:
                    logger.exception("notify: read team %s failed", team_name)
                    team = None
                if team is not None and getattr(team, "webhook_url", None):
                    return team.webhook_url
        return self._settings.notify_webhook_url or None

    @staticmethod
    def _render(
        event_type: str, run_id: str, team_name: str | None,
        task: str, payload: dict | None, tokens: int,
    ) -> tuple[str, list[str]]:
        """事件 → (标题, 行列表)。所有事件共用 团队/任务/ID 三行元信息。"""
        task_line = f"任务:{task[:80]}" if task else None
        lines = ["团队:" + (team_name or "-")]
        if task_line:
            lines.append(task_line)
        if event_type == "run_interrupted":
            title = "⏸ AgentTeam · Run 等待审批"
            lines.append("状态:interrupted,需要人工处理")
        elif event_type == "run_end":
            title = "✅ AgentTeam · Run 完成"
            if tokens:
                lines.append(f"token 用量:{tokens}")
        else:  # error
            title = "❌ AgentTeam · Run 失败"
            err = ((payload or {}).get("error") or "")[:200]
            if err:
                lines.append(f"错误:{err}")
        lines.append(f"Run:{run_id}")
        return title, lines

    def _send(self, url: str, body: dict) -> None:
        try:
            resp = requests.post(
                url, json=body, timeout=self._settings.notify_timeout_seconds,
            )
            if resp.status_code >= 300:
                logger.warning(
                    "notify webhook HTTP %s from %s", resp.status_code,
                    _mask_url(url),
                )
        except Exception as exc:
            # 通知失败绝不影响 run;只留日志供排查
            logger.warning("notify webhook failed (swallowed): %s %s",
                           _mask_url(url), exc)
