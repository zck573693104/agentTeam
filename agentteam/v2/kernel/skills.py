"""Skill 加载器(移植自 v1 runtime/skills.py,接口不变)。"""
from __future__ import annotations

import threading
from pathlib import Path


class SkillLoader:
    """从 skills/ 目录加载 markdown skill 文件(名 = 文件 stem),内存缓存。

    缺失的 skill 在 load 时抛 KeyError(fail-fast);线程安全。
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir
        self._cache: dict[str, str] = {}
        self._scanned = False
        self._lock = threading.Lock()

    def _scan(self) -> None:
        if self._scanned or self._skills_dir is None:
            self._scanned = True
            return
        for path in self._skills_dir.glob("*.md"):
            self._cache[path.stem] = path.read_text(encoding="utf-8")
        self._scanned = True

    def load(self, names: list[str]) -> dict[str, str]:
        if not names:
            return {}
        with self._lock:
            self._scan()
            missing = [n for n in names if n not in self._cache]
            if missing:
                raise KeyError(f"Skills not found: {missing}")
            return {n: self._cache[n] for n in names}

    def list_available(self) -> list[str]:
        with self._lock:
            self._scan()
            return sorted(self._cache.keys())

    def reload(self) -> None:
        with self._lock:
            self._cache.clear()
            self._scanned = False
            self._scan()
