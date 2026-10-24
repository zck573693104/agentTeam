"""dev_lead（程序员领导管理小队）预置测试。"""
from __future__ import annotations

from pathlib import Path

from agentteam.presets import get_preset
from agentteam.runtime.skills import SkillLoader


def _mod():
    return get_preset("dev_lead")


def _skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "skills"


def test_team_structure_flat_two_layers():
    """扁平两层：tech_lead(supervisor) + coder/tester(worker)，无嵌套 TeamRef。"""
    mod = _mod()
    root = mod.TEAM.root
    assert root.role == "supervisor" and root.name == "tech_lead"
    assert all(isinstance(c, object) and getattr(c, "role", None) == "worker"
               for c in root.children)
    assert {c.name for c in root.children} == {"coder", "tester"}
    # 无 deps_teams / deps_library 之外的团队依赖
    assert mod.METADATA["deps_teams"] == []


def test_leader_has_breakdown_skill_and_criteria_instruction():
    """领导的 system_prompt 要求写可验证验收标准；装备 task_breakdown/code_review。"""
    mod = _mod()
    root = mod.TEAM.root
    assert "acceptance_criteria" in root.system_prompt
    assert "验收标准" in root.system_prompt
    assert set(root.skills) == {"task_breakdown", "code_review"}


def test_coder_in_library_with_governance():
    """coder 进专家库（可自进化/$ref），写文件与执行命令需人工审批。"""
    mod = _mod()
    lib_agents = {a.name: a for a in mod.LIB_AGENTS}
    assert "coder" in lib_agents
    coder = lib_agents["coder"]
    policy = coder.approval_policy
    assert policy.level == "tool"
    assert set(policy.targets) == {"write_file", "bash"}
    # 团队内的 coder 通过 $ref 引用库（实例名 = 库名，自进化按 actor 名对齐）
    team_coder = next(c for c in mod.TEAM.root.children if c.name == "coder")
    assert team_coder.ref == "library:coder"


def test_tester_reads_and_runs_only():
    """tester 只有读+执行类工具，无写文件职责。"""
    mod = _mod()
    tester = next(c for c in mod.TEAM.root.children if c.name == "tester")
    assert "write_file" not in tester.tools
    assert {"read_file", "list_dir", "bash"} <= set(tester.tools)


def test_skills_exist_on_disk():
    """预设引用的 skills 在 skills/ 目录真实存在（SkillLoader fail-fast 前置校验）。"""
    mod = _mod()
    loader = SkillLoader(_skills_dir())
    need = set(mod.TEAM.skills)
    for a in mod.LIB_AGENTS:
        need |= set(a.skills)
    for c in mod.TEAM.root.children:
        need |= set(getattr(c, "skills", []) or [])
    missing = [n for n in need if n not in loader.list_available()]
    assert missing == []


def test_install_payload_shape(monkeypatch):
    """安装 payload：库 agent + 主团队，无 sub-team；engine 覆盖生效。"""
    from unittest.mock import MagicMock, patch

    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = MagicMock(status_code=200, text="{}",
                                               json=lambda: {"name": "x"})
        mock_req.ConnectionError = Exception
        from agentteam.presets import install_preset_to_api
        result = install_preset_to_api("dev_lead", api="http://api",
                                       engine="opencode")
    assert result["library"] == ["coder"]
    assert result["teams"] == ["dev_lead"]
    team_payload = mock_req.post.call_args_list[-1][1]["json"]
    assert team_payload["engine"] == "opencode"
    lib_payload = mock_req.post.call_args_list[0][1]["json"]
    assert lib_payload["name"] == "coder"
