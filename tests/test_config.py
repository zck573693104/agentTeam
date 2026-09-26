"""集中式配置(agentteam.config)测试。

覆盖:
- 默认值(零 env var 时)
- env var 覆盖
- 类型校验(ge=1 等约束)
- get_settings 单例
- override_settings 上下文管理器
"""
import os

import pytest

from agentteam.config import (
    Settings,
    get_settings,
    override_settings,
)


def test_default_settings_has_documented_defaults():
    """无 env var 时,所有字段回退到文档默认值。"""
    # 直接构造 Settings 不走单例,避免被其他测试污染
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    # 显式构造时 pydantic-settings 仍会读 env,但测试环境通常无 AGENTTEAM_ 前缀变量
    assert s.db_path == "data/agentteam.db"
    assert s.log_level == "WARNING"
    assert s.log_format == "text"
    assert s.event_queue_size == 1000
    assert s.max_run_workers == 32
    assert s.max_evolution_workers == 4
    assert s.interrupted_ttl_seconds == 6 * 3600


def test_env_var_override(monkeypatch):
    """AGENTTEAM_<NAME> 环境变量覆盖字段值。"""
    monkeypatch.setenv("AGENTTEAM_DB_PATH", "/tmp/test.db")
    monkeypatch.setenv("AGENTTEAM_MAX_RUN_WORKERS", "8")
    monkeypatch.setenv("AGENTTEAM_INTERRUPTED_TTL_SECONDS", "0")
    s = Settings()
    assert s.db_path == "/tmp/test.db"
    assert s.max_run_workers == 8
    assert s.interrupted_ttl_seconds == 0


def test_invalid_int_rejected(monkeypatch):
    """非整数 env var 触发 pydantic 校验错误。"""
    monkeypatch.setenv("AGENTTEAM_MAX_RUN_WORKERS", "not-an-int")
    with pytest.raises(Exception):
        Settings()


def test_below_min_rejected(monkeypatch):
    """ge=1 约束:max_run_workers=0 应被拒。"""
    monkeypatch.setenv("AGENTTEAM_MAX_RUN_WORKERS", "0")
    with pytest.raises(Exception):
        Settings()


def test_get_settings_returns_singleton():
    """get_settings 多次调用返回同一实例。"""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_override_settings_restores_original():
    """override_settings 退出后恢复原 Settings 实例。"""
    original = get_settings()
    original_max = original.max_run_workers
    with override_settings(max_run_workers=2) as s:
        assert s.max_run_workers == 2
        assert get_settings() is s
    # 退出后恢复
    assert get_settings() is original
    assert get_settings().max_run_workers == original_max


def test_extra_env_var_ignored(monkeypatch):
    """extra='ignore':未声明的 AGENTTEAM_* 变量不报错。"""
    monkeypatch.setenv("AGENTTEAM_UNKNOWN_FUTURE_OPTION", "xxx")
    s = Settings()  # 不抛错
    assert not hasattr(s, "unknown_future_option")
