"""opencode 套壳引擎示例团队定义（SP8）。

与 dev_team.py 同构的研发小队，但 `"engine": "opencode"` —— 执行层跑在
开源 opencode server 上（agent loop/工具/模型接入由底座承担），AgentTeam
保留控制平面（编排/三级审批/审计/自进化/控制台）。

前置:
    npm i -g opencode-ai && opencode serve --port 4117

用法:
    from examples.opencode_dev_team import OPENCODE_DEV_TEAM
    # 或直接 POST http://localhost:8000/api/teams

语义对照（相对 LangGraph 引擎）:
- tools 白名单 → prompt 级约束 + 引擎事后中断（v1.18.32 限制，见设计文档 §7）
- approval_policy(level="tool") → opencode permission 观测 + park/resume
- approval_policy(level="step"/"worker") → 控制平面门（原语义不变）
"""
from __future__ import annotations

OPENCODE_DEV_TEAM: dict = {
    "name": "oc_dev_team",
    "description": "opencode 套壳研发小队 — 主管 + 编码 + 测试",
    "engine": "opencode",
    "root": {
        "name": "tech_lead",
        "role": "supervisor",
        "system_prompt": (
            "你是技术主管,负责把任务拆解为步骤计划,指派给 coder/tester 执行,"
            "并在每步完成后简要点评。"
        ),
        "approval_policy": {"level": "step"},
        "children": [
            {
                "name": "coder",
                "role": "worker",
                "system_prompt": (
                    "你是代码工程师,在当前工作区完成编码任务;"
                    "改动文件前先 read_file 了解现状。"
                ),
                "tools": ["read_file", "write_file", "bash"],
                # tool 级审批:写文件/执行命令需人工批准（opencode permission 桥）
                "approval_policy": {"level": "tool", "targets": ["write_file", "bash"]},
                "skills": ["code_review"],
                "max_iterations": 10,
            },
            {
                "name": "tester",
                "role": "worker",
                "system_prompt": (
                    "你是测试工程师,阅读代码并运行测试,汇报结论。"
                ),
                "tools": ["read_file", "bash", "list_dir"],
                "max_iterations": 8,
            },
        ],
    },
    "default_model": {"provider": "qwen", "name": "qwen-max",
                      "temperature": 0.7, "streaming": True},
    "skills": [],
    "mcp_servers": [],
}
