"""SP7a Skill 系统 API 测试。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.routes.skills import skills_router
from agentteam.runtime.skills import SkillLoader


def _make_app(skills_dir: Path | None = None) -> FastAPI:
    app = FastAPI()
    loader = SkillLoader(skills_dir)
    app.include_router(skills_router(loader))
    return app


def test_get_skills_empty_dir_returns_empty_list(tmp_path):
    """空目录:GET /api/skills 返回 {"skills": []}。"""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/skills/")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}


def test_get_skills_lists_md_files(tmp_path):
    """有 skill 文件:GET /api/skills 返回排序后的 stem 列表。"""
    (tmp_path / "code_review.md").write_text("CR", encoding="utf-8")
    (tmp_path / "alpha.md").write_text("A", encoding="utf-8")
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/skills/")
    assert resp.status_code == 200
    assert resp.json() == {"skills": ["alpha", "code_review"]}


def test_get_skill_by_name_returns_content(tmp_path):
    """GET /api/skills/{name} 返回 skill 内容。"""
    (tmp_path / "code_review.md").write_text("# Code Review\n审查代码", encoding="utf-8")
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/skills/code_review")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "code_review"
    assert "审查代码" in body["content"]


def test_get_skill_nonexistent_returns_404(tmp_path):
    """GET /api/skills/nonexistent 返回 404 + detail 消息。"""
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/skills/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Skill 'nonexistent' not found"


def test_get_skill_no_skills_dir_returns_empty(tmp_path):
    """skills_dir=None:GET /api/skills 返回空列表。"""
    app = _make_app(None)
    with TestClient(app) as client:
        resp = client.get("/api/skills/")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}
