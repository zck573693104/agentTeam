"""审批 webhook 通知(P-A5 对标阿里云 AgentTeams "IM 原生")。

设计:
- Team.webhook_url 配置后,审批请求触发时 POST 通知
- 通知体 JSON:{run_id, team_name, gate, target, message, timestamp, delivery_id?}
- 失败不阻塞审批主流程(仅记录日志),webhook 是"尽力而为"通知
- 用后台线程发起 HTTP 请求,避免阻塞 LangGraph interrupt

Graph Engineering P4 外部 receipt 幂等:
文章原文:"付款、部署、发消息遇到超时,应先读取外部 receipt,再决定重试或补偿"。
- 投递时生成唯一 delivery_id 放入 payload,接收方可据此去重
- 失败时用同一 delivery_id 重试(指数退避),接收方识别重试不重复处理
- 可选 receipt_url:重试前先 GET 检查 receipt,已 delivered 则跳过重试

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
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from agentteam.logging_config import get_logger
from agentteam.storage.utils import utcnow_iso as _now

logger = get_logger("api.webhook")

# webhook POST 超时(秒)。短超时避免阻塞后台线程池。
_WEBHOOK_TIMEOUT = 5.0

# 通知体最大字段长度,防止 task/step 等大字段撑爆 IM 消息
_MAX_FIELD_LEN = 1024

# P4 外部 receipt:默认重试次数(首次 + N 次重试)与退避基准(秒)。
# 指数退避:base * 2^attempt,封顶 60s。失败后由 receipt 检查决定是否继续重试。
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_CAP = 60.0


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
    delivery_id: str | None = None,
) -> dict:
    """构造 webhook 通知 payload。

    字段对齐阿里云 AgentTeams 审批事件 schema,便于第三方平台解析。

    Graph Engineering P4:delivery_id 作为幂等键放入 payload,
    接收方据此去重(收到重复 delivery_id 不重复处理)。
    """
    payload = {
        "event": "approval_requested",
        "run_id": run_id,
        "team_name": team_name,
        "gate": gate,
        "target": _truncate(target),
        "message": _truncate(message),
        "timestamp": _now(),
    }
    if delivery_id is not None:
        payload["delivery_id"] = delivery_id
    return payload


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


def _check_receipt(receipt_url: str, delivery_id: str) -> bool:
    """Graph Engineering P4:重试前先读取外部 receipt。

    文章原文:"应先读取外部 receipt,再决定重试或补偿"。

    GET {receipt_url}/{delivery_id}:
    - 200: receipt 存在,接收方已处理,返回 True(跳过重试)
    - 404: receipt 不存在,返回 False(需要重试)
    - 其他异常:保守返回 False(继续重试,宁可重复也不漏投)

    receipt_url 为 None/空时直接返回 False(无 receipt 端点,走默认重试)。
    """
    if not receipt_url:
        return False
    url = f"{receipt_url.rstrip('/')}/{delivery_id}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_WEBHOOK_TIMEOUT) as resp:
            return resp.status < 400
    except urllib.error.HTTPError as e:
        # 404 = receipt 不存在,需要重试;其他 4xx/5xx 保守认为需要重试
        logger.debug(
            "receipt check %s returned %s, will retry", _mask_url(url), e.code,
        )
        return False
    except Exception as e:
        # 网络异常等:保守返回 False(继续重试)
        logger.debug(
            "receipt check %s failed: %s, will retry", _mask_url(url), e,
        )
        return False


def fire_approval_webhook(
    webhook_url: str | None,
    run_id: str,
    team_name: str,
    gate: str,
    target: Any,
    message: str,
    delivery_repo=None,
    receipt_url: str | None = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> None:
    """异步 fire 审批 webhook(后台线程,不阻塞主流程)。

    webhook_url 为 None/空时静默跳过。

    Graph Engineering P4 外部 receipt 幂等:
    - delivery_repo 提供 → 启用幂等追踪:生成 delivery_id,记 DB,
      失败时用同一 delivery_id 重试(指数退避),重试前可选检查 receipt。
    - delivery_repo 为 None → 向后兼容:fire-and-forget,无追踪无重试。

    失败仅记录日志,不抛异常(审批主流程不受影响)。
    """
    if not webhook_url:
        return

    # P4:启用幂等追踪时生成 delivery_id,否则 None(向后兼容)
    enable_tracking = delivery_repo is not None
    delivery_id = uuid.uuid4().hex if enable_tracking else None

    payload = build_approval_payload(
        run_id, team_name, gate, target, message, delivery_id=delivery_id,
    )

    if enable_tracking:
        # 记录 pending 投递(便于后续补偿任务扫描重试 / 运维查询)
        try:
            delivery_repo.create_delivery(
                delivery_id=delivery_id,  # type: ignore[arg-type]
                run_id=run_id,
                event_type="approval_requested",
                target_url=webhook_url,
                payload=payload,
            )
        except Exception:
            # DB 写入失败不应阻塞投递本身:降级为无追踪模式
            logger.exception(
                "create_delivery failed, falling back to untracked delivery: run=%s",
                run_id,
            )
            enable_tracking = False

    def _send() -> None:
        try:
            if enable_tracking:
                _send_with_retry(
                    webhook_url, payload, delivery_id,  # type: ignore[arg-type]
                    delivery_repo, receipt_url, max_retries, run_id, team_name, gate,
                )
            else:
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


def _send_with_retry(
    webhook_url: str,
    payload: dict,
    delivery_id: str,
    delivery_repo,
    receipt_url: str | None,
    max_retries: int,
    run_id: str,
    team_name: str,
    gate: str,
) -> None:
    """P4:带 receipt 检查 + 指数退避重试的投递。

    流程(对每个 attempt):
    1. (attempt > 0 时)先 GET receipt_url/{delivery_id} 检查是否已 delivered
       - receipt 存在 → 标 delivered,退出(接收方已处理,不重复投递)
       - receipt 不存在 → 继续重试
    2. POST webhook_url(同一 delivery_id,接收方可去重)
    3. 成功 → mark_attempt(success=True) + 退出
    4. 失败 → mark_attempt(success=False) + 指数退避 + 进入下一轮
    5. 重试耗尽 → mark_failed(保留 last_error)
    """
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        # 首次不查 receipt;重试前先查 receipt 避免重复投递
        if attempt > 0 and receipt_url:
            if _check_receipt(receipt_url, delivery_id):
                logger.info(
                    "webhook receipt found, skip retry: run=%s delivery_id=%s",
                    run_id, delivery_id,
                )
                # receipt 存在说明接收方已处理,标 delivered 退出
                try:
                    delivery_repo.mark_attempt(delivery_id, success=True)
                except Exception:
                    logger.exception(
                        "mark_attempt(delivered via receipt) failed: delivery_id=%s",
                        delivery_id,
                    )
                return

        try:
            _post_json(webhook_url, payload)
            try:
                delivery_repo.mark_attempt(delivery_id, success=True)
            except Exception:
                logger.exception(
                    "mark_attempt(success) failed: delivery_id=%s", delivery_id,
                )
            logger.info(
                "approval webhook sent: run=%s team=%s gate=%s url=%s "
                "delivery_id=%s attempt=%d",
                run_id, team_name, gate, _mask_url(webhook_url),
                delivery_id, attempt + 1,
            )
            return
        except Exception as e:
            last_error = str(e)
            try:
                delivery_repo.mark_attempt(delivery_id, success=False, error=last_error)
            except Exception:
                logger.exception(
                    "mark_attempt(fail) failed: delivery_id=%s", delivery_id,
                )
            logger.warning(
                "approval webhook attempt %d/%d failed: run=%s url=%s "
                "delivery_id=%s error=%s",
                attempt + 1, max_retries + 1, run_id,
                _mask_url(webhook_url), delivery_id, last_error,
            )
            # 仍有重试机会 → 指数退避
            if attempt < max_retries:
                backoff = min(
                    _DEFAULT_BACKOFF_BASE * (2 ** attempt),
                    _DEFAULT_BACKOFF_CAP,
                )
                time.sleep(backoff)

    # 重试耗尽 → 标 failed
    try:
        delivery_repo.mark_failed(delivery_id, error=last_error)
    except Exception:
        logger.exception(
            "mark_failed failed: delivery_id=%s", delivery_id,
        )


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
