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


def test_runs_router_accepts_skill_loader_param(tmp_path):
    """runs_router 接受 skill_loader 参数,并透传到 create_run 中构造的 TeamCompiler。"""
    from unittest.mock import MagicMock
    from agentteam.api.routes.runs import runs_router
    from agentteam.api.store import TeamStore
    from agentteam.runtime.skills import SkillLoader
    from agentteam.storage.db import init_db
    from agentteam.storage.runs import RunRepo
    from agentteam.storage.audit import AuditRepo
    from agentteam.storage.teams import TeamRepo
    from agentteam.api.events import EventBus
    from agentteam.domain.team import Team
    from agentteam.domain.agent import Agent
    from agentteam.models.provider import ModelRef
    import agentteam.runtime.graph as graph_mod

    # 用临时 db
    import threading
    conn = init_db(str(tmp_path / "test.db"))
    lock = threading.Lock()
    run_repo = RunRepo(conn, lock=lock)
    audit_repo = AuditRepo(conn, lock=lock)
    team_repo = TeamRepo(conn, lock=lock)
    team_store = TeamStore(repo=team_repo)
    bus = EventBus()
    loader = SkillLoader(tmp_path)

    # patch TeamCompiler 捕获 skill_loader
    captured = {}
    class FakeCompiler:
        def __init__(self, mp, tr, library=None, run_manager=None, skill_loader=None):
            captured["skill_loader"] = skill_loader
        def register_team(self, t): pass
        def compile(self, *a, **k): raise RuntimeError("stop before invoke")

    original = graph_mod.TeamCompiler
    graph_mod.TeamCompiler = FakeCompiler
    try:
        router = runs_router(
            MagicMock(), team_store, MagicMock(), MagicMock(),
            run_repo, audit_repo, bus, agent_library=MagicMock(),
            skill_loader=loader,
        )
        # 触发 create_run
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(router)
        # 需要先注册 team
        team_store.register(Team(
            name="t1", description="d", default_model=ModelRef("qwen", "qwen-max"),
            root=Agent(name="root", role="supervisor"),
        ))
        with TestClient(app) as client:
            # FakeCompiler.compile 抛 RuntimeError,被 create_run 的 except 捕获后
            # 转为 HTTPException(400, "Compile failed: ..."),TestClient 返回 400 响应而非抛出。
            # 用显式断言替代 try/except,失败时有清晰诊断且顺带验证错误处理路径。
            resp = client.post("/api/runs", json={"team_name": "t1", "task": "do x"})
    finally:
        graph_mod.TeamCompiler = original

    assert resp.status_code == 400
    assert "Compile failed" in resp.json()["detail"]
    assert captured["skill_loader"] is loader
