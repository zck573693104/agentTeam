"""AgentLibrary 并发安全 + cache-DB 一致性测试(BUG-02/03 domain 层)。"""
import threading

import pytest

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


class _FailingDeleteRepo:
    def __init__(self) -> None:
        self._data: dict[str, Agent] = {}

    def list_all(self):
        return list(self._data.values())

    def upsert(self, agent) -> None:
        self._data[agent.name] = agent

    def delete(self, name) -> bool:
        raise RuntimeError("DB delete failed")


class _FailingUpsertRepo:
    def __init__(self) -> None:
        self._data: dict[str, Agent] = {}

    def list_all(self):
        return list(self._data.values())

    def upsert(self, agent) -> None:
        raise RuntimeError("DB upsert failed")

    def delete(self, name) -> bool:
        self._data.pop(name, None)
        return True


# ---------------------------------------------------------------------------
# BUG-02: AgentLibrary.register_if_absent 原子性
# ---------------------------------------------------------------------------


def test_agent_library_register_if_absent_returns_true_for_new():
    """register_if_absent 新 agent 返回 True,重复返回 False。"""
    lib = AgentLibrary()
    assert lib.register_if_absent(Agent(name="coder", role="worker")) is True
    assert lib.register_if_absent(Agent(name="coder", role="worker")) is False
    assert lib.get("coder") is not None


def test_agent_library_register_if_absent_atomic():
    """并发 register_if_absent 同名 agent,只有一个返回 True(BUG-02 domain 层)。

    无锁实现下 check-then-act 竞态会让多个线程通过检查,导致多个 True(静默覆盖)。
    加锁后恰好一个 True,其余 False。
    """
    lib = AgentLibrary()
    n = 30
    barrier = threading.Barrier(n)
    results: list[bool] = []
    lock = threading.Lock()

    def reg():
        barrier.wait()
        ok = lib.register_if_absent(Agent(name="dup", role="worker"))
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=reg) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == n - 1
    assert lib.get("dup") is not None


def test_agent_library_register_if_absent_does_not_raise_on_duplicate():
    """register_if_absent 重复注册不抛 ValueError(与 register 区分),返回 False。"""
    lib = AgentLibrary()
    lib.register_if_absent(Agent(name="x", role="worker"))
    # 第二次返回 False,不抛异常
    assert lib.register_if_absent(Agent(name="x", role="worker")) is False


# ---------------------------------------------------------------------------
# BUG-03: AgentLibrary cache-DB 一致性(DB 失败保留内存)
# ---------------------------------------------------------------------------


def test_agent_library_delete_db_failure_keeps_cache():
    """DB delete 失败时,内存保留该 agent(BUG-03 delete)。

    修复前:先 del agents 再 repo.delete → 内存已空但 DB 还在(重启复活)。
    修复后:先 repo.delete(抛异常)→ 不动 agents → 内存保留,保持一致。
    """
    lib = AgentLibrary(repo=_FailingDeleteRepo())
    lib.register(Agent(name="coder", role="worker"))
    assert lib.get("coder") is not None

    with pytest.raises(RuntimeError, match="DB delete failed"):
        lib.delete("coder")

    # DB delete 失败 → 内存必须保留 agent
    assert lib.get("coder") is not None


def test_agent_library_register_db_failure_keeps_cache_clean():
    """DB upsert 失败时,内存不写入(BUG-03 register)。

    修复前:先写 agents 再 repo.upsert → 内存有但 DB 无(重启丢失)。
    修复后:先 repo.upsert(抛异常)→ 不动 agents → 内存无,保持一致。
    """
    lib = AgentLibrary(repo=_FailingUpsertRepo())
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        lib.register(Agent(name="coder", role="worker"))
    # DB 失败 → 内存不应有该 agent
    assert lib.get("coder") is None


def test_agent_library_update_db_failure_keeps_old_cache():
    """DB upsert 失败时,内存保留旧 agent(BUG-03 update)。

    修复前:先覆盖 agents 再 repo.upsert → 内存是新值但 DB 是旧值。
    修复后:先 repo.upsert(抛异常)→ 不动 agents → 内存仍是旧值。
    """
    lib = AgentLibrary(repo=_FailingUpsertRepo())
    # 直接放旧值进内存(绕过失败的 repo)
    old = Agent(name="coder", role="worker", system_prompt="old")
    lib.agents["coder"] = old

    new_agent = Agent(name="coder", role="worker", system_prompt="new")
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        lib.update(new_agent)
    # DB 失败 → 内存仍是旧值
    assert lib.get("coder").system_prompt == "old"
