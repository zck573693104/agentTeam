"""SP7a Skill 系统测试。"""
from pathlib import Path

import pytest

from agentteam.domain.agent import Agent
from agentteam.runtime.skills import SkillLoader


def test_agent_skills_field_defaults_empty():
    """Agent.skills 默认空 list(向后兼容)。"""
    agent = Agent(name="w1", role="worker")
    assert agent.skills == []


def test_agent_skills_field_accepts_list():
    """Agent.skills 可在构造时传入。"""
    agent = Agent(name="w1", role="worker", skills=["code_review", "testing"])
    assert agent.skills == ["code_review", "testing"]


def test_skill_loader_empty_dir_returns_empty_list(tmp_path):
    """空目录:list_available 返回空 list。"""
    loader = SkillLoader(tmp_path)
    assert loader.list_available() == []


def test_skill_loader_scans_md_files_uses_stem_as_name(tmp_path):
    """扫描 .md 文件,stem 作为 name。"""
    (tmp_path / "code_review.md").write_text("# Code Review\n...", encoding="utf-8")
    (tmp_path / "testing.md").write_text("# Testing\n...", encoding="utf-8")
    # 非 .md 文件应被忽略
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    assert loader.list_available() == ["code_review", "testing"]


def test_skill_loader_load_empty_names_returns_empty_dict(tmp_path):
    """load 空名列表返回空 dict。"""
    loader = SkillLoader(tmp_path)
    assert loader.load([]) == {}


def test_skill_loader_load_missing_raises_keyerror_with_names(tmp_path):
    """load 缺失 skill 抛 KeyError,异常消息含缺失名。"""
    loader = SkillLoader(tmp_path)
    with pytest.raises(KeyError) as exc_info:
        loader.load(["nonexistent"])
    assert "nonexistent" in str(exc_info.value)


def test_skill_loader_load_hit_returns_name_to_content(tmp_path):
    """load 命中返回 {name: content}。"""
    (tmp_path / "code_review.md").write_text("审查代码", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    result = loader.load(["code_review"])
    assert result == {"code_review": "审查代码"}


def test_skill_loader_load_multiple_skills(tmp_path):
    """load 多个 skill 一次性返回。"""
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "b.md").write_text("B", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    result = loader.load(["a", "b"])
    assert result == {"a": "A", "b": "B"}


def test_skill_loader_reload_picks_up_new_files(tmp_path):
    """reload 清缓存重扫,识别新增文件。"""
    loader = SkillLoader(tmp_path)
    assert loader.list_available() == []
    # reload 前新增文件
    (tmp_path / "new_skill.md").write_text("new", encoding="utf-8")
    # 未 reload 时仍为空(缓存未刷新)
    assert loader.list_available() == []
    loader.reload()
    assert loader.list_available() == ["new_skill"]


def test_skill_loader_no_dir_returns_empty(tmp_path):
    """skills_dir=None:不抛异常,list_available 返回空。

    load 行为由 test_skill_loader_load_missing_raises_keyerror_with_names 单独覆盖。
    """
    loader = SkillLoader(None)
    assert loader.list_available() == []


def test_skill_loader_list_available_sorted(tmp_path):
    """list_available 排序返回。"""
    (tmp_path / "zeta.md").write_text("z", encoding="utf-8")
    (tmp_path / "alpha.md").write_text("a", encoding="utf-8")
    (tmp_path / "mid.md").write_text("m", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    assert loader.list_available() == ["alpha", "mid", "zeta"]
