"""集中式配置管理(pydantic-settings)。

把散落在各模块的 `os.environ.get("AGENTTEAM_...")` 收敛到一处,
提供类型校验、默认值、运行时单例缓存,避免:
- 字符串/数字类型转换重复(`int(os.environ.get(...))` 散布)
- 默认值分散(同一参数在不同模块有不同默认值的隐患)
- 测试 mock 困难(单点注入 Settings 实例 vs 全局 env var monkeypatch)

设计:
- Settings 用 pydantic-settings BaseSettings,自动从环境变量读取
- env_prefix="AGENTTEAM_",字段名 lowercase(如 max_run_workers → AGENTTEAM_MAX_RUN_WORKERS)
- get_settings() 单例:首次调用构造,后续复用;测试可用 override_settings() 上下文重置

用法:
    from agentteam.config import get_settings
    settings = get_settings()
    print(settings.max_run_workers)  # int,已校验

字段对应环境变量(均有默认值,零配置可启动):
- AGENTTEAM_DB_PATH: SQLite 数据库路径(默认 "data/agentteam.db")
- AGENTTEAM_LOG_LEVEL: 日志级别(默认 "WARNING")
- AGENTTEAM_LOG_FORMAT: "text" 或 "json"(默认 "text")
- AGENTTEAM_EVENT_QUEUE_SIZE: EventBus 每订阅者队列上限(默认 1000)
- AGENTTEAM_MAX_RUN_WORKERS: run 线程池大小(默认 32)
- AGENTTEAM_MAX_EVOLUTION_WORKERS: evolution 线程池大小(默认 4)
- AGENTTEAM_INTERRUPTED_TTL_SECONDS: interrupted run 内存态 TTL(默认 21600=6h,0 禁用)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AgentTeam 全局配置。

    所有字段从环境变量 AGENTTEAM_<UPPER_NAME> 读取,
    缺省值保证零配置可启动。
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTTEAM_",
        case_sensitive=False,
        extra="ignore",  # 忽略未声明的 AGENTTEAM_* 变量,向前兼容
    )

    # 存储与日志
    db_path: str = Field(
        default="data/agentteam.db",
        description="SQLite 数据库文件路径",
    )
    log_level: str = Field(
        default="WARNING",
        description="日志级别(DEBUG/INFO/WARNING/ERROR)",
    )
    log_format: str = Field(
        default="text",
        description="日志格式:text 或 json",
    )

    # 事件总线
    event_queue_size: int = Field(
        default=1000,
        ge=1,
        description="EventBus 每订阅者队列上限,满时丢弃最旧事件",
    )

    # 后台线程池
    max_run_workers: int = Field(
        default=32,
        ge=1,
        description="run 执行线程池大小(防高并发下线程爆炸)",
    )
    max_evolution_workers: int = Field(
        default=4,
        ge=1,
        description="evolution 触发线程池大小(独立小池,避免占满 run 池)",
    )

    # interrupted run 内存态 TTL
    interrupted_ttl_seconds: int = Field(
        default=6 * 3600,
        ge=0,
        description="interrupted run 超过该秒数未被 resume 则驱逐内存态;0 禁用",
    )


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """获取全局 Settings 单例。

    首次调用时从环境变量构造,之后复用同一实例。
    测试可用 override_settings() 或直接重置 _settings_instance=None 强制重建。
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


@contextmanager
def override_settings(**overrides) -> Iterator[Settings]:
    """测试用:临时覆盖部分配置,上下文退出后恢复。

    示例:
        with override_settings(max_run_workers=2) as s:
            assert s.max_run_workers == 2
        # 退出后恢复原值
    """
    global _settings_instance
    original = _settings_instance
    base = original or Settings()
    # 用 model_copy + 字段覆盖构造新实例,不读环境变量(避免测试 env 污染)
    new = base.model_copy(update=overrides)
    _settings_instance = new
    try:
        yield new
    finally:
        _settings_instance = original
