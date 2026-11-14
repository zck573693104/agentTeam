"""AgentTeam CLI(v2):presets 安装 / 工人池查看 / 请求提交 / 工单查询。

全部走 HTTP API(默认 http://localhost:8000),与控制台同一套契约。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

API = "http://localhost:8000"
TIMEOUT = 30


def _req(method: str, path: str, body: dict | None = None) -> dict:
    resp = requests.request(method, API + path, json=body, timeout=TIMEOUT)
    if resp.status_code >= 300:
        print(f"✕ HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def install_preset(name: str) -> None:
    """安装 v2 worker-pool 预设(幂等:upsert 语义)。"""
    module_path = Path(__file__).parent / "v2" / "presets" / f"{name}.py"
    if not module_path.exists():
        print(f"✕ 预设不存在: {name}(可用: agentteam list-presets)", file=sys.stderr)
        sys.exit(1)
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"v2_preset_{name}", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    workers = getattr(module, "WORKERS", [])
    if not workers:
        print(f"✕ 预设 {name} 无 WORKERS", file=sys.stderr)
        sys.exit(1)
    for w in workers:
        _req("POST", "/api/workers", w.to_dict())
        print(f"✓ worker 已注册: {w.name}")
    meta = getattr(module, "METADATA", {})
    print(f"✓ 预设安装完成: {meta.get('title', name)}")


def main(argv: list[str] | None = None) -> None:
    args_all = list(argv) if argv is not None else sys.argv[1:]
    if not args_all:
        print(__doc__)
        usage = (
            "  agentteam install-preset <name>   安装 v2 工人池预设" "\n"
            "  agentteam list-presets            列出可用预设" "\n"
            "  agentteam list-workers            列出工人池" "\n"
            "  agentteam list-skills             列出技能" "\n"
            "  agentteam submit <text> [worker]  提交请求(Router 接单)" "\n"
            "  agentteam tickets [status]        查看工单"
        )
        print(usage)
        sys.exit(0)
    cmd, *args = args_all[0], *args_all[1:]
    if cmd == "install-preset" and args:
        install_preset(args[0])
    elif cmd == "list-presets":
        presets_dir = Path(__file__).parent / "v2" / "presets"
        for p in sorted(presets_dir.glob("*.py")):
            if p.stem == "__init__":
                continue
            print(p.stem)
    elif cmd == "list-workers":
        for w in _req("GET", "/api/workers"):
            status = "on" if w.get("enabled") else "off"
            print(f"- {w['name']} [{status}] tools={w.get('tools') or '[]'} "
                  f"approval={w.get('approval_targets') or '[]'}")
    elif cmd == "list-skills":
        for s in _req("GET", "/api/skills").get("skills", []):
            print(f"- {s}")
    elif cmd == "submit" and args:
        worker = args[1] if len(args) > 1 else None
        resp = _req("POST", "/api/requests", {"text": args[0], "worker_name": worker,
                                              "submitted_by": "cli"})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif cmd == "tickets":
        status = args[0] if args else None
        qs = f"?status={status}" if status else ""
        for t in _req("GET", f"/api/tickets{qs}"):
            print(f"- {t['id'][:8]} [{t['status']}] {t['title']} (worker={t['worker_name']})")
    else:
        print("未知命令,运行 agentteam 查看用法", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
