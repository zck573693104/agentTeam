"""集中式 logging 配置。

整个项目使用 `get_logger(name)` 获取 logger,默认行为:
- WARNING 及以上输出到 stderr
- 通过 init_logging(level=...) 调整级别
- 格式:`%(asctime)s %(levelname)s %(name)s: %(message)s`

支持环境变量(收敛到 agentteam.config.Settings 统一管理):
- `AGENTTEAM_LOG_LEVEL`: DEBUG/INFO/WARNING/ERROR (默认 WARNING)
- `AGENTTEAM_LOG_FORMAT`: "text" (默认) 或 "json"

设计原则:
- 库代码只 Import get_logger,不调用 init_logging
- 应用入口(api/server.py / cli.py)调用 init_logging 应用配置
- 所有 logger 命名以 `agentteam.` 前缀,便于过滤
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from agentteam.config import get_settings
from agentteam.security.crypto import mask_secrets_in_text

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _SecretFilter(logging.Filter):
    """Anti-Log 过滤器:扫描日志 message 中的敏感模式并脱敏。

    对标阿里云 AgentTeams 的"Anti-Log"特性:防止 API key/token/credential
    意外写入日志(如 logger.info("using token=%s", token))。

    覆盖 key=value 形式与已知厂商 key 前缀(ghp_ / github_pat_ / sk- / sk-ant-)。
    详见 agentteam.security.crypto.mask_secrets_in_text。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # 改写 record.getMessage() 的结果难以回填到 record,故直接改 msg/args。
        # 简化策略:对 record.msg(模板字符串)与 record.args 都做脱敏;
        # 若 args 非空,getMessage 会用 args % msg 拼接,这里在拼接后再过滤。
        # 最稳妥的方式:在 emit 前替换 record.message(由 Formatter.format 设置)。
        # 但 Filter 在 format 之前运行,无 message 字段。
        # 此处采用:重写 record.getMessage 临时包装,使 format 阶段拿到脱敏后的字符串。
        original_get = record.getMessage
        def _masked_get() -> str:
            return mask_secrets_in_text(original_get())
        record.getMessage = _masked_get  # type: ignore[method-assign]
        return True


class _JsonFormatter(logging.Formatter):
    """结构化 JSON 日志格式,便于日志聚合系统采集。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc"] = self.formatException(record.exc_info)
        # 额外字段
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "module", "msecs", "message", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName",
            ):
                try:
                    sval = json.dumps(value)  # 检查可序列化
                    # 字符串值再过一次脱敏(覆盖 logger.info("token=%s", token) 场景)
                    if isinstance(value, str):
                        from agentteam.security.crypto import mask_secrets_in_text
                        log_entry[key] = mask_secrets_in_text(value)
                    else:
                        log_entry[key] = json.loads(sval)
                except (TypeError, ValueError):
                    log_entry[key] = repr(value)
        return json.dumps(log_entry, ensure_ascii=False)


def init_logging(
    level: str | int | None = None,
    fmt: str | None = None,
    stream=None,
) -> None:
    """初始化全局 logging 配置。

    多次调用幂等:重复调用只更新级别。

    参数:
        level: 日志级别(字符串如 "INFO" 或 logging.DEBUG 等)。
               None 则读 Settings.log_level(对应 AGENTTEAM_LOG_LEVEL,默认 WARNING)
        fmt: "text" 或 "json"。None 则读 Settings.log_format(默认 "text")
        stream: 输出流,默认 sys.stderr
    """
    global _CONFIGURED
    settings = get_settings()
    if level is None:
        level = settings.log_level
    if fmt is None:
        fmt = settings.log_format
    if isinstance(level, str):
        level = level.upper()
        level = getattr(logging, level, logging.WARNING)

    root = logging.getLogger("agentteam")
    # 清除既有 handler(支持重新配置)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream or sys.stderr)
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    # Anti-Log 过滤器:任何格式下都启用,防止 token/secret 写入日志
    handler.addFilter(_SecretFilter())
    root.addHandler(handler)
    root.setLevel(level)
    # 防止传播到 root logger 造成重复输出
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取 agentteam.{name} logger。

    若未调用 init_logging,自动以默认级别(WARNING)初始化,
    确保库代码直接使用也能输出关键日志。
    """
    if not _CONFIGURED:
        # 自动初始化(读环境变量),但避免重复初始化
        init_logging()
    if not name.startswith("agentteam"):
        name = f"agentteam.{name}"
    return logging.getLogger(name)
