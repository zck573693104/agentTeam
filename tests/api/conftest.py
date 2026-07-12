"""API 集成测试 fixtures。"""
import pytest
from fastapi.testclient import TestClient

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.api.server import create_app
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry


def make_team_json(name="dev", with_approval=False):
    """返回一个有效的 Team JSON dict。"""
    leader = {
        "name": "leader",
        "role": "主管",
        "system_prompt": "你是主管",
        "model": None,
        "approval_policy": {"level": "step"} if with_approval else None,
    }
    return {
        "name": name,
        "description": "研发小队",
        "leader": leader,
        "workers": [
            {
                "name": "w1",
                "role": "执行者",
                "description": "干活",
                "system_prompt": "你是执行者",
                "model": None,
                "tools": [],
                "approval_policy": None,
                "max_iterations": 10,
            }
        ],
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [],
        "mcp_servers": [],
    }


@pytest.fixture
def make_app(tmp_path):
    """返回一个工厂函数，用自定义 FakeLLM 创建 app。"""
    from tests.conftest import FakeModelProvider

    def _create(fake_provider, db_path=None):
        path = str(db_path or (tmp_path / "api_test.db"))
        app = create_app(
            db_path=path,
            model_provider=fake_provider,
            tool_registry=ToolRegistry(),
        )
        return app

    return _create


@pytest.fixture
def make_client(make_app):
    from tests.conftest import FakeModelProvider, FakeLLM

    def _create(fake_provider=None, db_path=None):
        if fake_provider is None:
            fake_provider = FakeModelProvider({"qwen-max": FakeLLM()})
        app = make_app(fake_provider, db_path)
        return TestClient(app)

    return _create
