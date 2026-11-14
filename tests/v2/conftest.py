"""v2 测试夹具:FakeLLM/FakeModelProvider(沿用 v1 已验证的 duck-typing 模式)。

v2 内核的模型抽象刻意保持 v1 FakeLLM 兼容的窄接口:
invoke / bind_tools / with_structured_output。
"""
from pathlib import Path

import pytest

from tests.conftest import FakeLLM, FakeModelProvider  # noqa: F401 (re-export)


def wait_ticket_terminal(v2, ticket_id: str, timeout: float = 10.0):
    """轮询直到 ticket 终态,返回 Ticket;超时抛 TimeoutError。"""
    import time

    from agentteam.v2.domain.events import TicketStatus

    deadline = time.time() + timeout
    while time.time() < deadline:
        t = v2.ticket_repo.get(ticket_id)
        if t and t.status.is_terminal():
            return t
        time.sleep(0.05)
    t = v2.ticket_repo.get(ticket_id)
    raise TimeoutError(
        f"ticket {ticket_id} not terminal in {timeout}s, status={t.status if t else None}"
    )


@pytest.fixture
def make_v2(tmp_path: Path):
    """构建 v2 组件(临时 db + workspace;FakeModelProvider 注入)。"""
    from agentteam.v2.api.deps import create_v2_components

    created = []

    def _create(llm_by_model: dict[str, FakeLLM] | None = None) -> "V2Components":
        provider = FakeModelProvider(llm_by_model or {"qwen-max": FakeLLM()})
        v2 = create_v2_components(
            db_path=str(tmp_path / f"v2_{len(created)}.db"),
            workspace_root=tmp_path / "workspaces",
            model_provider=provider,
            max_session_workers=4,
        )
        v2.scheduler.start()  # 服务内由 lifespan 调用;测试需显式启动
        created.append(v2)
        return v2

    yield _create

    for v2 in created:
        v2.scheduler.shutdown(wait=False)
        v2.conn.close()
