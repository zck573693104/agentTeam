"""AgentTeam CLI 入口。

命令:
    agentteam register-dev-team [--api URL]  注册研发小队到 API 服务
"""
from __future__ import annotations

import argparse
import sys

import requests

from examples.dev_team import DEV_TEAM


def register_dev_team(api: str = "http://localhost:8000") -> int:
    """注册研发小队到指定 API 服务,返回退出码。"""
    try:
        resp = requests.post(f"{api}/api/teams", json=DEV_TEAM, timeout=10)
        if resp.status_code < 400:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            print(f"已注册团队: {data.get('name', 'dev_team')}")
            return 0
        else:
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


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(prog="agentteam", description="AgentTeam CLI")
    sub = parser.add_subparsers(dest="command")

    p_register = sub.add_parser("register-dev-team", help="注册研发小队到 API")
    p_register.add_argument("--api", default="http://localhost:8000", help="API 地址")

    args = parser.parse_args(argv)

    if args.command == "register-dev-team":
        return register_dev_team(args.api)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
