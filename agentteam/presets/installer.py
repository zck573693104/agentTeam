"""预置团队 HTTP 安装器 — 数据/传输分离。

install_preset_to_api 通过 HTTP POST→PUT 回退实现幂等安装。
本模块属于"传输层",presets/__init__.py 只保留数据/目录职责。
"""
from __future__ import annotations

import requests

from agentteam.domain.serializer import team_to_dict
from agentteam.presets import get_preset


def install_preset_to_api(name: str, api: str = "http://localhost:8000") -> dict:
    """安装预置团队到 API 服务。

    安装顺序(确保依赖先就位):
    1. 注册 LIB_AGENTS 到 /api/library/agents(POST 失败为 400 重复 → PUT 更新)
    2. 注册 deps_teams 中的 sub-team 到 /api/teams(POST 失败为 400 重复 → PUT 更新)
       sub-team 需在 preset 模块中定义为模块级变量(变量名 = team.name.upper() 优先)
    3. 注册 TEAM 到 /api/teams(POST 失败为 400 重复 → PUT 更新)

    返回 {"library": [...], "teams": [...]} 记录每步注册结果。
    幂等:重复安装时 POST→PUT 回退,不会因重复而失败。
    """
    from dataclasses import asdict

    mod = get_preset(name)
    meta = mod.METADATA
    result: dict[str, list[str]] = {"library": [], "teams": []}

    def _post_or_put(url_post: str, url_put: str | None, payload: dict, label: str) -> None:
        resp = requests.post(url_post, json=payload, timeout=10)
        if resp.status_code < 400:
            result[label].append(payload.get("name", "?"))
            return
        if resp.status_code == 400 and url_put is not None:
            # 重复 → 回退 PUT(SP4 热更新)
            resp2 = requests.put(url_put, json=payload, timeout=10)
            if resp2.status_code < 400:
                result[label].append(payload.get("name", "?") + "(updated)")
                return
            # BUG-09 修复:PUT 也失败时,错误信息包含 POST 和 PUT 双方
            raise RuntimeError(
                f"注册 {label} '{payload.get('name')}' 失败: "
                f"POST={resp.status_code} {resp.text}; "
                f"PUT={resp2.status_code} {resp2.text}"
            )
        raise RuntimeError(
            f"注册 {label} '{payload.get('name')}' 失败: "
            f"{resp.status_code} {resp.text}"
        )

    # 1. LIB_AGENTS
    for agent in getattr(mod, "LIB_AGENTS", []):
        agent_dict = {
            "name": agent.name, "role": agent.role,
            "system_prompt": agent.system_prompt,
            "tools": list(agent.tools), "max_iterations": agent.max_iterations,
            "model": asdict(agent.model) if agent.model else None,
            "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        }
        _post_or_put(
            f"{api}/api/library/agents",
            f"{api}/api/library/agents/{agent.name}",
            agent_dict, "library",
        )

    # 2. deps_teams (sub-teams referenced by TeamRef)
    for team_name in meta.get("deps_teams", []):
        # 约定:sub-team 模块级变量名 = team.name.upper()(如 "test_subteam" → TEST_SUBTEAM)
        sub_team = getattr(mod, team_name.upper(), None) or getattr(mod, team_name, None)
        if sub_team is None:
            raise RuntimeError(
                f"preset '{name}' 声明 deps_teams=[{team_name!r}] 但模块未定义 "
                f"变量 {team_name.upper()!r} 或 {team_name!r}"
            )
        _post_or_put(
            f"{api}/api/teams",
            f"{api}/api/teams/{team_name}",
            team_to_dict(sub_team), "teams",
        )

    # 3. TEAM (主团队)
    _post_or_put(
        f"{api}/api/teams",
        f"{api}/api/teams/{mod.TEAM.name}",
        team_to_dict(mod.TEAM), "teams",
    )

    return result
