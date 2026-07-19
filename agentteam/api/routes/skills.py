"""Skill 系统 API 路由:列出 / 查询单个 skill。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.runtime.skills import SkillLoader


def skills_router(skill_loader: SkillLoader) -> APIRouter:
    """构造 /api/skills 路由。

    endpoints:
    - GET /api/skills/        — 列出所有可用 skill 名(排序)
    - GET /api/skills/{name}  — 返回指定 skill 的内容
    """
    router = APIRouter(prefix="/api/skills", tags=["skills"])

    @router.get("/")
    def list_skills():
        return {"skills": skill_loader.list_available()}

    @router.get("/{skill_name}")
    def get_skill(skill_name: str):
        try:
            contents = skill_loader.load([skill_name])
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Skill '{skill_name}' not found",
            )
        return {"name": skill_name, "content": contents[skill_name]}

    return router
