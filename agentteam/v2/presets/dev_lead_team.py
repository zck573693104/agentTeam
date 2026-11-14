"""研发工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="coder",
        system_prompt="你是代码工程师,使用 read_file/write_file 完成编码任务。写文件前需人工审批。",
        tools=['search_web'],
        approval_targets=['write_file'],
        max_iterations=12,
    ),
    WorkerDef(
        name="tester",
        system_prompt="你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
        approval_targets=['write_file'],
    ),
]

METADATA = {
    "name": "dev_lead_team",
    "title": "研发工人池",
    "description": "coder/tester;写文件审批",
}
