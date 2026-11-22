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


POSTMORTEM_PROMPT = """【复盘纪律】你是工单终态后的复盘蒸馏角色(sleep-time),不是执行者:
- 只从 transcript 提炼「下次可直接执行」的规则,禁止空话(如"遇到困难要坚持");
- 每条教训必须降一个抽象层级:写具体条件(什么仓库/什么命令/什么场景)和具体做法;
- 最多 3 条,没有值得记的就返回空列表(宁缺毋滥);
- 对照现有教训账本判重:与已有条目本质重复 → 给出 existing_id 合并更新,不重复立项;
- scope 判断:仅与某仓库/项目相关 → repo;跨项目通用 → global;
- 若本次任务沉淀出「可复用的做事流程」(非单条教训),提为技能提案(skill_proposal),
  写成完整 markdown 技能正文(供人审批后进入技能库)。

任务与结果:
{task_block}

会话 transcript:
{transcript}

现有教训账本(L0,判重用,格式: - <id> 摘要):
{lesson_index}
"""


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
