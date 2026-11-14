"""v2 GUI 演示启动器(无需真实模型 Key)。

注入脚本化 Fake 模型驱动完整 dsh 流程:
- 请求 → Router 确定性派工(指定 worker)或 LLM 路由 → 工单
- Worker 首轮调用 write_file(触发审批门 → Attention)
- 人工批准后真实写入文件并完成;再次请求则直接完成
- Source webhook: Eval 判定 accept → observe 记录;confirm 转正立项

启动: python -m uvicorn scripts.demo_v2:app --port 8000
"""
from __future__ import annotations

import threading

from langchain_core.messages import AIMessage

from agentteam.api.server import create_app
from agentteam.models.provider import ModelProvider


class DemoLLM:
    """脚本化 LLM:按调用次序轮换行为,可重复执行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._invoke_count = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        name = getattr(schema, "__name__", "")

        class _Structured:
            def invoke(self, messages, **kw):
                if name == "RouterDecision":
                    # 自动模式转正/未指定 worker 时的路由决策
                    return schema(
                        action="dispatch", worker="coder",
                        goal=str(messages)[:0] or "处理入站请求(demo)",
                        acceptance="demo", note="demo route",
                    )
                # EvalDecision
                return schema(accept=True, reason="demo: 三事实成立")

        return _Structured()

    def invoke(self, messages, **kwargs):
        with self._lock:
            self._invoke_count += 1
            n = self._invoke_count
        # 首个会话首轮: 触发写文件审批;其后所有调用直接完成
        if n == 1:
            return AIMessage(content="", tool_calls=[{
                "name": "write_file",
                "args": {"path": "hello.txt", "content": "v2 demo — 由人工批准后写入"},
                "id": "demo-call-1",
            }])
        return AIMessage(content="demo 工单已完成:文件已就位,验收通过。")


class DemoProvider(ModelProvider):
    """全部模型名 → DemoLLM。"""

    def get_llm(self, ref):
        return _DEMO_LLM


_DEMO_LLM = DemoLLM()

app = create_app(model_provider=DemoProvider())
