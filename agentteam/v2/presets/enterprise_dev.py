"""企业研发工人池(v2 压平:原 CEO→CTO→小组 三层组织) — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/v2/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="eng",
        system_prompt="你是代码工程师,使用 read_file/write_file 完成编码任务。写文件前需人工审批。",
        tools=['search_web'],
        approval_targets=['write_file'],
        max_iterations=12,
    ),
    WorkerDef(
        name="reviewer",
        system_prompt="你是代码审查员,使用 read_file 审查代码与测试质量,给出改进建议。",
        tools=['read_file'],
        max_iterations=5,
    ),
    WorkerDef(
        name="tester",
        system_prompt="你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
        approval_targets=['write_file'],
    ),
]

METADATA = {
    "name": "enterprise_dev",
    "title": "企业研发工人池",
    "description": "eng/reviewer/tester;原三层组织压平为 Router 直派",
}
