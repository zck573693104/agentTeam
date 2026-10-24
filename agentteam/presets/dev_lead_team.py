"""程序员领导管理小队预置 — 领导拆解/分派/验收，程序员实现/自测。

适用场景：一个技术主管管理少数程序员下属的日常研发（扁平两层，
区别于 enterprise_dev 的 CEO→CTO 多级嵌套）。

「领导管理下属」的关键语义：
- **带验收标准的分派**：领导拆解时为每步写可验证的 acceptance_criteria，
  派活即告知下属交付标准（harness 引擎透传进 worker prompt）；
- **验收留痕**：worker 交付后对 dict 形式标准做机器验证
  （contains/regex/test，复用 runtime/criteria），发 criteria_check 事件；
- **治理点**：程序员写文件/执行命令需人工审批（tool 级事前拦截——
  langgraph 引擎；opencode 引擎为事后中断+审计）；
- **自进化**：coder 注册进专家库（LIB_AGENTS），run 终态后参与 SP7 进化。

用法:
    python -m agentteam.cli install-preset dev_lead --api http://localhost:8000
    python -m agentteam.cli install-preset dev_lead --engine opencode   # 免费模型零 Key
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


METADATA: dict = {
    "name": "dev_lead",
    "title": "程序员领导管理小队",
    "description": "技术主管+程序员+测试,带验收标准的分派/机器验收/写执行审批,扁平两层",
    "category": "research",
    "tags": ["研发", "验收标准", "审批", "扁平团队", "专家库"],
    "deps_teams": [],
    "deps_library": ["coder"],
}


# 程序员进专家库：跨 run 可被 $ref 复用，并参与 SP7 自进化
LIB_AGENTS: list[Agent] = [
    Agent(
        name="coder",
        role="程序员",
        system_prompt=(
            "你是程序员,在当前工作区完成编码任务:\n"
            "1. 先 read_file/list_dir 了解现状,再动手;\n"
            "2. 严格按任务简报中的验收标准交付,交付前逐条自检;\n"
            "3. 改动最小化,不做验收标准之外的重构;\n"
            "4. 最后输出简短交付说明:做了什么/如何验证/遗留风险。"
        ),
        tools=["read_file", "list_dir", "write_file", "bash"],
        approval_policy=ApprovalPolicy(level="tool", targets=["write_file", "bash"]),
        skills=["dev_delivery"],
        max_iterations=12,
        model=ModelRef("qwen", "qwen-max"),
    ),
]


TEAM: Team = Team(
    name="dev_lead",
    description="程序员领导管理小队 — 技术主管拆解/分派/验收,程序员实现,测试自测",
    default_model=ModelRef("qwen", "qwen-max"),
    skills=["task_breakdown"],
    root=Agent(
        name="tech_lead", role="supervisor",
        system_prompt=(
            "你是技术主管,直接管理程序员下属(coder/tester):\n"
            "1. 拆解任务时为每一步写**可验证的验收标准**(acceptance_criteria,"
            "如'输出包含 X'/'测试命令通过'),指派给最合适的下属;\n"
            "2. 有依赖的步骤用 depends_on 表达,可并行的并行;\n"
            "3. 下属交付后对照验收标准验收,不合格的要点在点评里写清返工要求;\n"
            "4. 全部验收通过后才结束。"
        ),
        skills=["task_breakdown", "code_review"],
        approval_policy=None,
        children=[
            # 程序员:引用专家库 coder(可进化、可跨团队复用)
            Agent(
                ref="library:coder",
                name="coder",
                role="worker",
            ),
            # 测试:读代码 + 跑测试,写操作不在职责内故不设写审批
            Agent(
                name="tester", role="worker",
                system_prompt=(
                    "你是测试工程师,阅读 coder 的产出并运行验证:"
                    "按任务简报中的验收标准逐条验证,报告每条 通过/不通过 及证据。"
                ),
                tools=["read_file", "list_dir", "bash"],
                approval_policy=ApprovalPolicy(level="tool", targets=["bash"]),
                skills=["dev_delivery"],
                max_iterations=8,
            ),
        ],
    ),
    mcp_servers=[],
)
