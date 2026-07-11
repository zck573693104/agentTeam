import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """提供一个临时 SQLite 连接，测试结束自动关闭。"""
    from agentteam.storage.db import init_db

    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()
