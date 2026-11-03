"""SP12a 通信纪律 — leader/worker 职责硬隔离的共享 prompt 文本。

借鉴 dsh-superagent（DeepSeek Harness 团队开源原型）的 Router/Worker 分层:
- Router(leader) 只路由不做技术决策,路由批注 ≤30 词;
- Worker 静默干活,例行问题自查,只有必须人拍板的阻塞才提问,且一次最多一个。

langgraph 与 harness 双引擎共用同一份文本(spec §3.1/§3.3),保证纪律一致;
纪律是 prompt 层约束,不改工具集与审批门(与 dsh "prompt text cannot change
enforcement" 同原则)。

QUESTION 协议:worker 回复中以「QUESTION: 」或「问题：」开头的单独一行
表示需要人/领导拍板的问题,每轮最多一个。extract_question 供双引擎
从纯文本回复中提取(与 artifact/evidence 的文本协议同风格)。
"""
from __future__ import annotations

# leader 纪律(plan 与 review 共用一张卡;harness 经 supervisor_user_prompt
# 单一漏斗注入,langgraph 在 leader_plan/leader_review 各插一条 SystemMessage)
LEADER_DISCIPLINE = (
    "【分派纪律】你是协调者，不是执行者：\n"
    "1. 给下属的指令只写「目标 + 背景 + 验收标准」，禁止写实现方案、"
    "技术选型、文件路径清单或代码设计——怎么实现由下属自己决定；\n"
    "2. 不扩写需求：不得添加原始任务中没有的要求；\n"
    "3. 分派/点评说明控制在 30 词以内，只写路由背景与约束，"
    "不代下属解决技术问题。"
)

# worker 纪律(附在任务指令前,与 system_prompt 同级)
WORKER_DISCIPLINE = (
    "【汇报纪律】静默完成交付，例行问题自己解决；只有必须人拍板的阻塞"
    "（设计分歧/权限不足/需求歧义无法自查）才提问，且最多提一个，"
    "格式为回复末尾单独一行「QUESTION: <问题>」。没有问题就不要写这一行。"
)

# QUESTION 标记行前缀(半角冒号带空格 / 全角冒号都接受)
_QUESTION_PREFIXES = ("QUESTION:", "问题：")


def extract_question(text: str) -> str | None:
    """从 worker 纯文本回复中提取结构化问题(每轮最多一个)。

    取第一个匹配标记行,去掉前缀后 strip;多条标记只取第一条
    (dsh 语义:一次最多一个结构化问题,多问视为没问)。无标记返回 None。
    """
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in _QUESTION_PREFIXES:
            if stripped.startswith(prefix):
                question = stripped[len(prefix):].strip()
                return question or None
    return None
