"""三层组织架构演示团队 — 领导三层级 + N 个技术组 + 组内挂人。

结构：
    cto (L1 · CTO)
    ├── director_platform (L2 · 平台研发部总监)
    │   ├── lead_backend  (L3 · 后端组长)  → backend_a / backend_b
    │   └── lead_qa       (L3 · 测试组长)  → qa_a / qa_b
    └── director_biz     (L2 · 业务研发部总监)
        ├── lead_frontend (L3 · 前端组长)  → fe_a / fe_b
        └── lead_ops      (L3 · 运维组长)  → ops_a

用法：
    # 注册（langgraph 引擎，需模型 Key）
    python -m agentteam.cli register-team examples/org_demo.py

    # 注册为 opencode 引擎（先起 opencode server；或装进 API 后经 Web 控制台查看）
    curl -X POST http://localhost:8000/api/teams -H "Content-Type: application/json" \\
         -d @<(python -c "from examples.org_demo import ORG_TEAM; import json; print(json.dumps(ORG_TEAM, ensure_ascii=False))")

Web 控制台 Teams 页展开 org_demo 卡片的 Agent Tree 即可看到完整三层组织树。
"""
from __future__ import annotations

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef


def _worker(name: str, role_desc: str, *, tools=None, approve=None) -> Agent:
    """组员：按简报交付 + 交付前自检；写/执行类动作默认需人工审批。"""
    return Agent(
        name=name,
        role="worker",
        system_prompt=(
            f"你是{role_desc}。按任务简报交付，交付前对照验收标准自检，"
            "最后输出：做了什么/如何验证/遗留风险。"
        ),
        tools=list(tools or ("read_file", "list_dir", "write_file", "bash")),
        approval_policy=(
            ApprovalPolicy(level="tool", targets=list(approve))
            if approve else None
        ),
        skills=["dev_delivery"],
    )


def _lead(name: str, title: str, children: list[Agent]) -> Agent:
    """组长/总监/CTO：拆解写验收标准、指派、验收、给返工要求。"""
    return Agent(
        name=name,
        role="supervisor",
        system_prompt=(
            f"你是{title}，管理下属小组/成员：拆解任务时为每步写可验证的验收标准，"
            "指派给最合适的下属，有依赖用 depends_on，交付后对照标准验收并给出返工要求。"
        ),
        skills=["task_breakdown"],
        children=children,
    )


TEAM: Team = Team(
    name="org_demo",
    description="演示组织：CTO→两大研发部总监→4 个技术组长→7 名组员（三层领导 + N 技术组）",
    default_model=ModelRef("qwen", "qwen-max"),
    engine="opencode",
    root=_lead("cto", "CTO，统管两大研发部", [
        _lead("director_platform", "平台研发部总监", [
            _lead("lead_backend", "后端技术组组长", [
                _worker("backend_a", "后端程序员（服务接口）"),
                _worker("backend_b", "后端程序员（数据与存储）"),
            ]),
            _lead("lead_qa", "测试技术组组长", [
                _worker("qa_a", "测试工程师（接口与回归）",
                        tools=("read_file", "list_dir", "bash"), approve=("bash",)),
                _worker("qa_b", "测试工程师（自动化脚本）",
                        tools=("read_file", "list_dir", "bash", "write_file"),
                        approve=("bash",)),
            ]),
        ]),
        _lead("director_biz", "业务研发部总监", [
            _lead("lead_frontend", "前端技术组组长", [
                _worker("fe_a", "前端程序员（页面与交互）",
                        tools=("read_file", "list_dir", "write_file"),
                        approve=("write_file",)),
                _worker("fe_b", "前端程序员（组件库）",
                        tools=("read_file", "list_dir", "write_file"),
                        approve=("write_file",)),
            ]),
            _lead("lead_ops", "运维技术组组长", [
                _worker("ops_a", "运维工程师（部署与监控）"),
            ]),
        ]),
    ]),
    mcp_servers=[],
)

# API/CLI 注册用的 JSON dict（register-team 约定变量名 TEAM dict，
# 故同时导出 ORG_TEAM = TEAM 的 dict 形态）
from agentteam.domain.serializer import team_to_dict  # noqa: E402

ORG_TEAM: dict = team_to_dict(TEAM)


if __name__ == "__main__":
    import json

    print(json.dumps(ORG_TEAM, ensure_ascii=False, indent=2))
