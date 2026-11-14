"""通信纪律 prompt 卡(移植自 v1 runtime/discipline.py)。

dsh 红线(spec §2):
1. Worker 永不直接对人;对外出口唯一(Router 汇总)
2. Router 不做技术决策:plan = 目标 + 验收标准,批注 ≤30 词
3. Worker 消息默认一句话,一次最多一个结构化问题(QUESTION)
"""
from __future__ import annotations

_QUESTION_PREFIXES = ("QUESTION:", "问题：")

ROUTER_DISCIPLINE = """【分派纪律】你是前台协调者(Router),不是执行者:
- 只输出「目标 + 背景 + 验收标准」,禁止输出实现方案、技术选型、文件路径清单;
- 复述原始请求时不许扩写需求范围;
- 路由批注不超过 30 词;
- 你没有任何执行工具,不要尝试自己完成任务。"""

WORKER_DISCIPLINE = """【汇报纪律】
- 默默交付,回复默认一句话,不超过 100 词;
- 每轮最多提一个结构化问题,且必须是「必须人拍板」的问题(设计分歧/权限卡死/需求歧义无法自查);
- 琐碎问题自己解决,不要升级给人;
- 需要提问时,在回复末尾单独一行: QUESTION: <问题>
- 你不能直接与人对话,你的汇报只进入工单轨迹。"""


def extract_question(text: str) -> str | None:
    """提取 worker 回复中的 QUESTION 标记(一次最多一个;无标记返回 None)。"""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in _QUESTION_PREFIXES:
            if stripped.startswith(prefix):
                question = stripped[len(prefix):].strip()
                return question or None
    return None
