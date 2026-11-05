"""SP12c: 事件驱动 Trigger(Fast/Slow Path 两级过滤)。

设计对标 DeepSeek《Super-agent 设计探索》第 3 节:
- Trigger = 群聊消息 / webhook / CI 等环境事件
- Fast Path:确定性规则(条件全命中才放行),程序求值零 LLM 成本
- Slow Path:Fast Path 未命中时,LLM 按规则的自然语言策略判定是否行动;
  无模型 Key / 全局关闭 / 判定异常时静默跳过(与 MemoryEngine 同纪律)
- 命中后经 submit_run 走与人工提交完全相同的配额/审计路径,
  triggered_by_user 记 "trigger:<规则名>"
"""
from agentteam.triggers.engine import TriggerEngine

__all__ = ["TriggerEngine"]
