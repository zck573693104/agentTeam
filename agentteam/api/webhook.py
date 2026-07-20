"""审批 webhook 通知(P-A5 对标阿里云 AgentTeams "IM 原生")。

设计:
- Team.webhook_url 配置后,审批请求触发时 POST 通知
- 通知体 JSON:{run_id, team_name, gate, target, message, timestamp}
- 失败不阻塞审批主流程(仅记录日志),webhook 是"尽力而为"通知
- 用后台线程发起 HTTP 请求,避免阻塞 LangGraph interrupt

支持接入:
- 钉钉群机器人(自定义 keyword)
- 飞书自定义机器人
- 企业微信群机器人
- 通用 webhook(POST JSON)

钉钉示例(在 webhook_url 中带 access_token):
    https://oapi.dingtalk.com/robot/send?access_token=xxx
飞书示例:
    https://open.feishu.cn/open-apis/bot/v2/hook/xxx
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

from agentteam.logging_config import get_logger
from agentteam.storage.utils import utcnow_iso as _now

logger = get_logger("api.webhook")

# webhook POST 超时(秒)。短超时避免阻塞后台线程池。
_WEBHOOK_TIMEOUT = 5.0

# 通知体最大字段长度,防止 task/step 等大字段撑爆 IM 消息
_MAX_FIELD_LEN = 1024


def _truncate(value: Any) -> str:
    """截断字段值到 _MAX_FIELD_LEN,转字符串。"""
    s = str(value) if value is not None else ""
    if len(s) > _MAX_FIELD_LEN:
        return s[:_MAX_FIELD_LEN] + "...(truncated)"
    return s


def build_approval_payload(
    run_id: str,
    team_name: str,
    gate: str,
    target: Any,
    message: str,
) -> dict:
    """构造 webhook 通知 payload。

    字段对齐阿里云 AgentTeams 审批事件 schema,便于第三方平台解析。
    """
    return {
        "event": "approval_requested",
        "run_id": run_id,
        "team_name": team_name,
        "gate": gate,
        "target": _truncate(target),
        "message": _truncate(message),
        "timestamp": _now(),
    }


def _post_json(url: str, payload: dict) -> None:
    """同步 POST JSON 到 url,失败抛异常。

    用 urllib 而非 requests,避免引入新依赖。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT) as resp:
        if resp.status >= 400:
            raise urllib.error.HTTPError(
                url, resp.status, f"webhook returned {resp.status}", resp.headers, None
            )


def fire_approval_webhook(
    webhook_url: str | None,
    run_id: str,
    team_name: str,
    gate: str,
    target: Any,
    message: str,
) -> None:
    """异步 fire 审批 webhook(后台线程,不阻塞主流程)。

    webhook_url 为 None/空时静默跳过。
    失败仅记录日志,不抛异常(审批主流程不受影响)。
    """
    if not webhook_url:
        return
    payload = build_approval_payload(run_id, team_name, gate, target, message)

    def _send() -> None:
        try:
            _post_json(webhook_url, payload)
            logger.info(
                "approval webhook sent: run=%s team=%s gate=%s url=%s",
                run_id, team_name, gate, _mask_url(webhook_url),
            )
        except Exception as e:
            # 失败不阻塞审批主流程,记录日志即可
            logger.warning(
                "approval webhook failed: run=%s team=%s url=%s error=%s",
                run_id, team_name, _mask_url(webhook_url), e,
            )

    # daemon 线程:进程退出时不需要等待
    t = threading.Thread(target=_send, name="agentteam-webhook", daemon=True)
    t.start()


def _mask_url(url: str) -> str:
    """脱敏 webhook URL 中的 token 部分,避免日志泄漏。"""
    if not url:
        return url
    # 钉钉/飞书 webhook URL 常含 access_token query param
    if "access_token=" in url:
        # 把 access_token=xxx 中的 xxx 替换为 ***
        import re
        return re.sub(
            r"(access_token=)[^&]+",
            r"\1***",
            url,
        )
    if "/hook/" in url:
        # 飞书 /open-apis/bot/v2/hook/xxx 形式
        parts = url.rsplit("/", 1)
        if len(parts) == 2:
            return parts[0] + "/***"
    return url
