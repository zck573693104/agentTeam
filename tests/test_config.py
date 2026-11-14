"""配置层测试(v2 字段与约束)。"""
import pytest

from agentteam.config import Settings, get_settings, override_settings


def test_defaults():
    s = Settings()
    assert s.db_path == "data/agentteam_v2.db"
    assert s.v2_max_session_workers == 8
    assert s.v2_default_provider == "qwen"
    assert s.v2_default_model == "qwen-max"
    assert s.v2_workspace_root == ""
    assert s.notify_retry_max == 5


def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENTTEAM_DB_PATH", "/tmp/test.db")
    monkeypatch.setenv("AGENTTEAM_V2_MAX_SESSION_WORKERS", "4")
    s = Settings()
    assert s.db_path == "/tmp/test.db"
    assert s.v2_max_session_workers == 4


def test_override_settings_context():
    with override_settings(v2_max_session_workers=2) as s:
        assert s.v2_max_session_workers == 2
    assert get_settings().v2_max_session_workers == 8


def test_ge_constraint():
    """ge=1 约束:v2_max_session_workers=0 应被拒。"""
    with pytest.raises(ValueError):
        Settings(v2_max_session_workers=0)


def test_dead_fields_removed():
    """v1 死字段已随内核重建移除(opencode scars 清理)。"""
    s = Settings()
    assert not hasattr(s, "max_run_workers")
    assert not hasattr(s, "auth_enabled")
    assert not hasattr(s, "memory_enabled")
    assert not hasattr(s, "max_evolution_workers")
