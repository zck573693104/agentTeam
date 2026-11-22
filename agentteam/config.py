"""集中式配置管理(pydantic-settings,v2 内核)。

字段对应环境变量(均有默认值,零配置可启动):
- AGENTTEAM_DB_PATH: SQLite 数据库路径(默认 "data/agentteam_v2.db")
- AGENTTEAM_LOG_LEVEL / AGENTTEAM_LOG_FORMAT
- AGENTTEAM_EVENT_QUEUE_SIZE: EventBus 每订阅者队列上限
- AGENTTEAM_V2_MAX_SESSION_WORKERS: 同时执行的 Ticket 数上限
- AGENTTEAM_V2_DEFAULT_PROVIDER / AGENTTEAM_V2_DEFAULT_MODEL: 默认模型
- AGENTTEAM_V2_WORKSPACE_ROOT: 工单工作区根目录(空 = db 同目录 workspaces/)
- AGENTTEAM_V2_SOURCE_POLL_INTERVAL: SourceDaemon 轮询间隔秒
- AGENTTEAM_NOTIFY_*: IM 出站 webhook(outbox 先存后发)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AgentTeam v2 全局配置(env_prefix=AGENTTEAM_)。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENTTEAM_",
        case_sensitive=False,
        extra="ignore",
    )

    # 存储与日志
    db_path: str = Field(
        default="data/agentteam_v2.db",
        description="SQLite 数据库文件路径(v2 内核)",
    )
    log_level: str = Field(default="WARNING", description="日志级别")
    log_format: str = Field(default="text", description="日志格式:text 或 json")

    # 事件总线
    event_queue_size: int = Field(
        default=1000, ge=1,
        description="EventBus 每订阅者队列上限,满时丢弃最旧事件",
    )

    # v2 内核
    v2_max_session_workers: int = Field(
        default=8, ge=1,
        description="worker session 线程池大小(同时执行的 Ticket 数上限)",
    )
    v2_default_provider: str = Field(
        default="qwen",
        description="默认模型 provider(Router 与未指定模型的 worker 使用)",
    )
    v2_default_model: str = Field(
        default="qwen-max",
        description="默认模型名",
    )
    v2_workspace_root: str = Field(
        default="",
        description="工单工作区根目录;空 = db 同目录下的 workspaces/",
    )
    v2_source_poll_interval: float = Field(
        default=10.0, ge=1,
        description="SourceDaemon 轮询间隔(秒)",
    )

    # P4 学习闭环(复盘蒸馏 + 文件式记忆 + 技能晋升)
    v2_memory_dir: str = Field(
        default="",
        description="经验记忆库根目录;空 = db 同目录下的 memory/(置为 off 关闭)",
    )
    v2_aux_provider: str = Field(
        default="",
        description="轻量辅助模型 provider(Eval 研判/复盘蒸馏);空 = 回落默认模型",
    )
    v2_aux_model: str = Field(
        default="",
        description="轻量辅助模型名;空 = 回落默认模型",
    )
    v2_postmortem_enabled: bool = Field(
        default=True,
        description="工单终态后是否自动复盘蒸馏(需 memory 目录已启用)",
    )
    v2_memory_evict_days: int = Field(
        default=30, ge=0,
        description="P5: 从未命中教训归档天数(_archive/);0 = 关闭",
    )
    v2_workspace_archive_days: int = Field(
        default=7, ge=0,
        description="P5: 终态工单工作区归档天数(_archive/);0 = 关闭",
    )
    v2_eval_distill_threshold: int = Field(
        default=5, ge=0,
        description="P5: 观察模式确认样本攒够多少条蒸馏一次校准规则;0 = 关闭",
    )

    # IM 通知出站(Outbox: 先存后发,退避重试)
    notify_webhook_url: str = Field(
        default="",
        description="IM webhook 地址(飞书/企业微信/钉钉 bot);空 = 关闭外发",
    )
    notify_provider: str = Field(
        default="generic",
        description="webhook 消息格式:feishu / wecom / dingtalk / generic",
    )
    console_url: str = Field(
        default="http://127.0.0.1:8000",
        description="控制台外部可达地址,通知消息里的跳转链接以它为基准",
    )
    notify_timeout_seconds: float = Field(default=5.0, ge=0, description="webhook POST 超时秒数")
    notify_retry_max: int = Field(
        default=5, ge=1, description="单条通知连续失败多少次后置 dropped(保留 last_error 审计)",
    )
    notify_backoff_base: float = Field(
        default=5.0, ge=0, description="重试退避起点秒数,指数翻倍,上限 300s",
    )


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例(测试可用 override_settings 覆盖)。"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


@contextmanager
def override_settings(**overrides) -> Iterator[Settings]:
    """测试用:临时覆盖部分配置,上下文退出后恢复。"""
    global _settings_instance
    original = _settings_instance
    base = original or Settings()
    new = base.model_copy(update=overrides)
    _settings_instance = new
    try:
        yield new
    finally:
        _settings_instance = original
