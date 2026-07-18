"""BUG-07 回归测试:FastAPI lifespan 关闭 SQLite 连接。

create_app 创建的 conn 应在 app shutdown(lifespan exit)时被 close,
避免 Windows 上 SQLite 文件锁残留。

测试策略:用 sqlite3.connect factory 参数注入 _TrackingConnection 子类,
追踪 close() 是否在 lifespan shutdown 时被调用。子类化是必要的,因为
sqlite3.Connection(C 类型)不允许实例级属性赋值。
"""
import sqlite3

from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_app_lifespan_closes_connection(tmp_path, monkeypatch):
    """app shutdown(lifespan exit)时 conn.close() 被调用。

    BUG-07:原 create_app 无 lifespan,conn 在 app GC 前不会被显式 close,
    Windows 上 SQLite 文件锁残留。修复后:FastAPI lifespan 在 yield 后 close conn。
    """
    closed = {"called": False}
    real_connect = sqlite3.connect

    class _TrackingConnection(sqlite3.Connection):
        """sqlite3.Connection 子类:追踪 close() 调用。

        子类化是必要的 —— sqlite3.Connection 是 C 类型,
        不允许实例级属性赋值(conn.foo = ... 会抛 AttributeError)。
        """

        def close(self):
            closed["called"] = True
            super().close()

    def _tracking_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        return real_connect(*args, **kwargs)

    # monkeypatch sqlite3.connect:让 init_db 创建出 _TrackingConnection 实例
    monkeypatch.setattr(sqlite3, "connect", _tracking_connect)

    app = create_app(db_path=str(tmp_path / "test.db"), web_dist=None)

    # with 语法触发 lifespan(startup + shutdown)
    with TestClient(app):
        pass

    assert closed["called"] is True, (
        "conn.close() 应在 app shutdown(lifespan exit)时被调用"
    )
