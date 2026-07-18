"""多级层级 + Team 嵌套 + 专家库示例团队定义。

验证 SP1 三大特性：
- 3 级 supervisor 链：CEO → CTO → eng
- Team 嵌套：CTO 下挂 qa_team（引用 test_subteam）
- 专家库引用：eng 用 $ref 引用 code_engineer 模板

用法:
    from examples.multi_level_team import MULTI_LEVEL_TEAM, TEST_TEAM, LIB
    # 或通过 CLI: agentteam register-team examples/multi_level_team.py
"""
from __future__ import annotations

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef

# —— 专家库 ——
LIB = AgentLibrary()
LIB.register(Agent(
    name="code_engineer", role="worker",
    system_prompt="你是代码工程师，用 read_file/write_file 完成编码任务。",
    tools=["read_file", "write_file"], max_iterations=10,
))
LIB.register(Agent(
    name="tester", role="worker",
    system_prompt="你是测试员，使用 read_file/write_file 编写测试用例。",
    tools=["read_file", "write_file"], max_iterations=5,
))

# —— 子 Team：测试小队 ——
TEST_TEAM = Team(
    name="test_subteam",
    description="测试小队",
    root=Agent(
        name="test_lead", role="supervisor",
        system_prompt="你是测试主管，派活给 tester。",
        children=[Agent(
            name="tester", role="worker",
            system_prompt="你是测试员，写测试用例。",
            tools=["read_file", "write_file"], max_iterations=5,
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)

# —— 主 Team：3 级层级 + Team 嵌套 + 专家库引用 ——
MULTI_LEVEL_TEAM = Team(
    name="multi_level",
    description="3 级层级 + Team 嵌套 + 专家库",
    root=Agent(
        name="ceo", role="supervisor",
        system_prompt="你是 CEO，派活给技术副总裁 CTO。",
        children=[Agent(
            name="cto", role="supervisor",
            system_prompt="你是 CTO，派活给工程师和测试小队。",
            children=[
                # 专家库引用：复用 code_engineer 模板，覆盖 name
                Agent(name="eng", role="worker", ref="library:code_engineer"),
                # Team 嵌套
                TeamRef(name="test_subteam", alias="qa_team"),
            ],
        )],
    ),
    default_model=ModelRef("qwen", "qwen-max"),
)
