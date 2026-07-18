"""BUG-08: SSE 直播模式 disconnect 检测延迟 + 线程池占用。

原实现 `q.get(timeout=2.0)` 阻塞 threadpool 线程 2 秒，客户端断开后
最坏延迟 2 秒才 break，期间 threadpool 线程被占用。>40 个并发 SSE 客户端
会耗尽 threadpool。

修复后：timeout 缩短到 0.5s，提高 disconnect 检测频率。

测试策略：用静态分析（inspect.getsource）验证 q.get(timeout=...) 的值
≤ 1.0s，避免起真实 SSE 客户端的计时测试不稳定性。
"""
import inspect
import re

from agentteam.api.routes import runs as runs_module


def _extract_q_get_timeouts(source: str) -> list[float]:
    """从源码中提取所有 q.get(timeout=N) 调用的 timeout 值。"""
    pattern = re.compile(r"q\.get\(\s*timeout\s*=\s*([\d.]+)\s*\)")
    return [float(m) for m in pattern.findall(source)]


def test_sse_live_q_get_timeout_is_short():
    """SSE 直播 q.get(timeout=N) 的 N 必须 ≤ 1.0s。

    原值 2.0s 过长：客户端断开后 disconnect 检测延迟，期间占用 threadpool
    线程，>40 个并发 SSE 客户端会耗尽 threadpool。修复后应 ≤ 1.0s。
    """
    source = inspect.getsource(runs_module)
    timeouts = _extract_q_get_timeouts(source)

    assert timeouts, (
        "未找到 q.get(timeout=...) 调用——SSE 直播循环应使用阻塞队列 get 并设 "
        "较短 timeout 以频繁检测 disconnect"
    )
    for t in timeouts:
        assert t <= 1.0, (
            f"SSE 直播 q.get timeout={t}s 过长（应 ≤ 1.0s 以避免 threadpool 占用 "
            "和 disconnect 检测延迟）"
        )


def test_sse_live_q_get_timeout_exists():
    """源码中应至少存在一个 q.get(timeout=...) 调用——否则直播循环可能
    退化为无限阻塞 q.get()，完全不检测 disconnect。"""
    source = inspect.getsource(runs_module)
    timeouts = _extract_q_get_timeouts(source)
    assert len(timeouts) >= 1, "SSE 直播循环应至少有一个 q.get(timeout=...)"
