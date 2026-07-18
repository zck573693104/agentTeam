"""AgentTeam CLI 入口。

命令:
    agentteam register-dev-team [--api URL]      注册研发小队到 API 服务
    agentteam register-team FILE [--api URL]     注册任意 Team 配置文件
    agentteam list-teams [--api URL]             列出已注册团队
    agentteam register-library FILE [--api URL]  注册专家库
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import requests

from examples.dev_team import DEV_TEAM
from agentteam.presets import list_presets, get_preset, install_preset_to_api


def _load_team_module(path: str) -> dict:
    """从 Python 文件加载 Team dict 配置。

    文件应定义 MODULE_LEVEL_TEAM 或 MULTI_LEVEL_TEAM 或 DEV_TEAM 变量，
    或第一个 Team/MULTI_LEVEL_TEAM 字典变量。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Team config file not found: {path}")
    spec = importlib.util.spec_from_file_location("team_module", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 优先级：MULTI_LEVEL_TEAM > MODULE_LEVEL_TEAM > TEAM > DEV_TEAM
    for name in ("MULTI_LEVEL_TEAM", "MODULE_LEVEL_TEAM", "TEAM", "DEV_TEAM"):
        if hasattr(mod, name):
            from agentteam.domain.serializer import team_to_dict
            from agentteam.domain.team import Team
            val = getattr(mod, name)
            if isinstance(val, dict):
                return val
            if isinstance(val, Team):
                return team_to_dict(val)
    raise AttributeError(f"No team variable found in {path}")


def _load_library_module(path: str) -> list[dict]:
    """从 Python 文件加载 AgentLibrary 中的 agents 列表。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Library file not found: {path}")
    spec = importlib.util.spec_from_file_location("lib_module", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "LIB"):
        raise AttributeError(f"No LIB variable found in {path}")
    lib = getattr(mod, "LIB")
    from dataclasses import asdict
    from agentteam.domain.agent import Agent
    result = []
    for agent in lib.agents.values():
        d = {
            "name": agent.name, "role": agent.role,
            "system_prompt": agent.system_prompt,
            "tools": list(agent.tools),
            "max_iterations": agent.max_iterations,
            "children": [], "ref": None,
            "model": asdict(agent.model) if agent.model else None,
            "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        }
        result.append(d)
    return result


def register_dev_team(api: str = "http://localhost:8000") -> int:
    try:
        resp = requests.post(f"{api}/api/teams", json=DEV_TEAM, timeout=10)
        if resp.status_code < 400:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            print(f"已注册团队: {data.get('name', 'dev_team')}")
            return 0
        try:
            err = resp.json()
            detail = err.get("detail", resp.text)
        except ValueError:
            detail = resp.text
        print(f"错误: {detail}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def register_team(file_path: str, api: str = "http://localhost:8000") -> int:
    try:
        team_dict = _load_team_module(file_path)
    except Exception as e:
        print(f"错误: 加载配置文件失败: {e}")
        return 1
    try:
        resp = requests.post(f"{api}/api/teams", json=team_dict, timeout=10)
        if resp.status_code < 400:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            print(f"已注册团队: {data.get('name', 'unknown')}")
            return 0
        try:
            err = resp.json()
            detail = err.get("detail", resp.text)
        except ValueError:
            detail = resp.text
        print(f"错误: {detail}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def list_teams(api: str = "http://localhost:8000") -> int:
    try:
        resp = requests.get(f"{api}/api/teams", timeout=10)
        if resp.status_code < 400:
            try:
                teams = resp.json()
            except ValueError:
                teams = []
            if not teams:
                print("(空)")
                return 0
            for t in teams:
                name = t.get("name", "?")
                desc = t.get("description", "")
                print(f"  {name}  {desc}")
            return 0
        try:
            err = resp.json()
            detail = err.get("detail", resp.text)
        except ValueError:
            detail = resp.text
        print(f"错误: {detail}")
        return 1
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def register_library(file_path: str, api: str = "http://localhost:8000") -> int:
    try:
        agents = _load_library_module(file_path)
    except Exception as e:
        print(f"错误: 加载库文件失败: {e}")
        return 1
    try:
        for agent in agents:
            resp = requests.post(f"{api}/api/library/agents", json=agent, timeout=10)
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                    detail = err.get("detail", resp.text)
                except ValueError:
                    detail = resp.text
                print(f"错误: 注册 {agent.get('name')} 失败: {detail}")
                return 1
        print(f"已注册 {len(agents)} 个专家 Agent")
        return 0
    except requests.ConnectionError:
        print(f"错误: 无法连接到 {api},请确认 API 服务已启动")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1


def list_presets_cmd() -> int:
    """列出所有可用预置团队。"""
    presets = list_presets()
    if not presets:
        print("(无预置团队)")
        return 0
    print(f"共 {len(presets)} 个预置团队:\n")
    for p in presets:
        print(f"  [{p['category']}] {p['name']}  {p['title']}")
        print(f"      {p['description']}")
        print(f"      tags: {', '.join(p['tags'])}")
        if p['deps_library']:
            print(f"      library: {', '.join(p['deps_library'])}")
        if p['deps_teams']:
            print(f"      sub-teams: {', '.join(p['deps_teams'])}")
        print()
    return 0


def show_preset(name: str) -> int:
    """显示指定预置团队的详细信息。"""
    try:
        mod = get_preset(name)
    except KeyError as e:
        print(f"错误: {e}")
        return 1
    meta = mod.METADATA
    team = mod.TEAM
    print(f"名称: {meta['name']}")
    print(f"标题: {meta['title']}")
    print(f"描述: {meta['description']}")
    print(f"分类: {meta['category']}")
    print(f"标签: {', '.join(meta['tags'])}")
    print(f"\n主团队: {team.name}")
    print(f"  描述: {team.description}")
    print(f"  root: {team.root.name} ({team.root.role})")
    if team.mcp_servers:
        print(f"  MCP (team-level): {[s.name for s in team.mcp_servers]}")
    print(f"\n专家库 agents ({len(mod.LIB_AGENTS)}):")
    for a in mod.LIB_AGENTS:
        print(f"  - {a.name} ({a.role}): tools={a.tools}")
    if meta['deps_teams']:
        print(f"\n依赖 sub-teams: {meta['deps_teams']}")
    return 0


def install_preset(name: str, api: str = "http://localhost:8000") -> int:
    """安装预置团队到 API 服务。"""
    try:
        result = install_preset_to_api(name, api=api)
    except KeyError as e:
        print(f"错误: {e}")
        return 1
    except Exception as e:
        print(f"错误: {e}")
        return 1
    print(f"预置团队 '{name}' 安装成功:")
    if result["library"]:
        print(f"  专家库: {result['library']}")
    print(f"  团队: {result['teams']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentteam", description="AgentTeam CLI")
    sub = parser.add_subparsers(dest="command")

    p_dev = sub.add_parser("register-dev-team", help="注册研发小队到 API")
    p_dev.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_team = sub.add_parser("register-team", help="注册任意 Team 配置文件")
    p_team.add_argument("file", help="Team 配置文件路径（.py）")
    p_team.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_list = sub.add_parser("list-teams", help="列出已注册团队")
    p_list.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_lib = sub.add_parser("register-library", help="注册专家库")
    p_lib.add_argument("file", help="库文件路径（.py，需定义 LIB 变量）")
    p_lib.add_argument("--api", default="http://localhost:8000", help="API 地址")

    p_list_p = sub.add_parser("list-presets", help="列出所有预置团队")
    p_show_p = sub.add_parser("show-preset", help="显示预置团队详情")
    p_show_p.add_argument("name", help="预置团队名称")
    p_install_p = sub.add_parser("install-preset", help="安装预置团队到 API")
    p_install_p.add_argument("name", help="预置团队名称")
    p_install_p.add_argument("--api", default="http://localhost:8000", help="API 地址")

    args = parser.parse_args(argv)

    if args.command == "register-dev-team":
        return register_dev_team(args.api)
    elif args.command == "register-team":
        return register_team(args.file, args.api)
    elif args.command == "list-teams":
        return list_teams(args.api)
    elif args.command == "register-library":
        return register_library(args.file, args.api)
    elif args.command == "list-presets":
        return list_presets_cmd()
    elif args.command == "show-preset":
        return show_preset(args.name)
    elif args.command == "install-preset":
        return install_preset(args.name, args.api)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
