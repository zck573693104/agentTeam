"""org_demo — 组织架构演示预设:双部门 8 人(每组一人 + 各部门领导)。

PMS部门: 操作组 / 职能组 / 报表组 / 部门领导
应用部:  财务组 / 平台应用组 / 成本分摊组 / 部门领导

用法:
    agentteam install-preset org_demo
    或 POST /api/workers 逐个注册 WORKERS
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

_REPORT_TAIL = (
    "完成后用一句话汇报结果。"
    "你不能直接与人对话,汇报只进工单轨迹;需要人拍板时在回复末尾单独一行 QUESTION: <问题>。"
)


def _member(dept: str, group: str, duty: str) -> str:
    return f"你是{dept}·{group}成员。职责: {duty}{_REPORT_TAIL}"


def _lead(dept: str, duty: str) -> str:
    return (f"你是{dept}部门领导。职责: {duty}你不做具体执行,"
            "只做目标拆解/协调/把关;验收他人产出时逐项给结论。"
            "需要向人升级的事项用 QUESTION: 标记。")


WORKERS: list[WorkerDef] = [
    # —— PMS部门 ——
    WorkerDef(
        name="pms_ops",
        system_prompt=_member("PMS部门", "操作组",
                              "日常运营操作执行、工单处理、数据录入与修正、状态跟催;"
                              "数据/文件写入前先核对口径。"),
        approval_targets=["write_file"],   # 运营数据写入需人工批准
        max_iterations=12,
    ),
    WorkerDef(
        name="pms_func",
        system_prompt=_member("PMS部门", "职能组",
                              "制度流程与规范维护、权限/参数配置、跨组流程答疑。"),
        max_iterations=10,
    ),
    WorkerDef(
        name="pms_report",
        system_prompt=_member("PMS部门", "报表组",
                              "报表生成与核对、指标统计、口径异常标记;"
                              "数字必须给出数据来源与统计周期。"),
        tools=["search_web"],
        approval_targets=["write_file"],
        max_iterations=10,
    ),
    WorkerDef(
        name="pms_lead",
        system_prompt=_lead("PMS", "部门目标拆解、跨组协调、交付与验收把关。"),
        max_iterations=6,
    ),
    # —— 应用部 ——
    WorkerDef(
        name="app_finance",
        system_prompt=_member("应用部", "财务组",
                              "账务处理、对账与差异追踪、发票与凭证整理;"
                              "金额字段必须双人口径核对(给出计算式)。"),
        approval_targets=["write_file"],
        max_iterations=10,
    ),
    WorkerDef(
        name="app_platform",
        system_prompt=_member("应用部", "平台应用组",
                              "平台账号与应用配置、系统日常维护、故障初判与升级。"),
        tools=["search_web"],
        max_iterations=10,
    ),
    WorkerDef(
        name="app_cost",
        system_prompt=_member("应用部", "成本分摊组",
                              "成本核算、分摊规则维护、月结数据校验;"
                              "分摊结果必须附规则说明与计算过程。"),
        approval_targets=["write_file"],
        max_iterations=10,
    ),
    WorkerDef(
        name="app_lead",
        system_prompt=_lead("应用部", "部门规划与排期、跨组协调、需求验收把关。"),
        max_iterations=6,
    ),
]

METADATA = {
    "title": "组织架构演示池(PMS部门/应用部,每组一人+部门领导)",
    "departments": {
        "PMS部门": ["操作组 pms_ops", "职能组 pms_func", "报表组 pms_report", "领导 pms_lead"],
        "应用部": ["财务组 app_finance", "平台应用组 app_platform",
                 "成本分摊组 app_cost", "领导 app_lead"],
    },
}
