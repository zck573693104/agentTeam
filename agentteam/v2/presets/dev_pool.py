"""dev_pool — 研发工人池预设(v2 首个内置池)。

Router(轻量模型) + coder/tester 两 worker:
- coder: 文件读写工具,写文件需人工批准(P1 审批门激活后生效)
- tester: 只读 + web 搜索

用法:
    POST /api/workers 逐个注册 WORKERS
    或 agentteam-v2-install-preset dev_pool(CLI,P5 提供)
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="coder",
        system_prompt=(
            "你是编码工程师,负责实现代码任务。"
            "使用 read_file/write_file/list_dir 在工单工作区内完成编码;"
            "write_file 属高危操作,执行前工单会进入人工审批。"
            "完成后用一句话汇报改动点。"
        ),
        tools=["search_web"],
        approval_targets=["write_file"],
        max_iterations=12,
    ),
    WorkerDef(
        name="tester",
        system_prompt=(
            "你是测试工程师,负责验收编码产出。"
            "使用 read_file/list_dir 检查文件与测试用例,search_web 查询资料;"
            "发现问题用一句话说明,通过则明确说「验收通过」。"
        ),
        tools=["search_web"],
        max_iterations=8,
    ),
]

METADATA = {
    "name": "dev_pool",
    "title": "研发工人池",
    "description": "Router + coder/tester;写文件审批,验收闭环",
    "category": "dev",
}
