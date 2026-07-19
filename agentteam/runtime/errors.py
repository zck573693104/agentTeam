"""runtime 层共享异常类型。

抽到独立模块避免 runtime 层反向依赖 api 层
(原 nodes.py `from agentteam.api.run_manager import RunCancelledError`
造成 runtime → api 不当依赖;正确方向是 api → runtime)。
"""
from __future__ import annotations


class RunCancelledError(BaseException):
    """run 被用户取消,worker 节点检测到 cancel event 后抛出。

    继承 BaseException(而非 Exception)以绕过 worker 内部
    `try: ... except Exception:` 的常规 catch,确保取消信号能
    一路传播到 RunManager._handle_error 被识别并标记为 cancelled。

    放在 runtime/errors.py 而非 api/run_manager.py 的理由:
    - 抛出方在 runtime/nodes.py(agent_step 检测 cancel event)
    - 捕获方在 api/run_manager.py(_handle_error)
    - 抛出方不应反向 import 捕获方所在模块
    """

    pass
