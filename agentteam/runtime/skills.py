"""Skill 加载器:从 skills/ 目录读取 markdown skill 文件,提供按名查询接口。"""
from __future__ import annotations

import threading
from pathlib import Path


class SkillLoader:
    """从文件系统加载 markdown skill 文件,内存缓存避免重读。

    skill 名 = 文件名 stem(如 skills/code_review.md → "code_review")。
    若 skill 不存在,load 时抛 KeyError(编译期 fail-fast,避免运行时静默缺失)。

    线程安全:用 threading.Lock 保护 _scan/reload/load。
    SP7b SkillGenerator 在 evolution 线程写入新 skill 后调 reload(),
    同时 API 线程可能在 _compile_worker 中调 load(),无锁会导致
    load() 命中 _scanned=False 中间态读到半扫的 _cache 抛 KeyError(竞态)。
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir
        self._cache: dict[str, str] = {}
        self._scanned = False
        self._lock = threading.Lock()

    def _scan(self) -> None:
        """惰性扫描 skills_dir,构建 name → content 缓存。

        幂等:已扫描则直接返回(skills/ 目录在编译期只读)。
        skills_dir=None 时跳过扫描(缓存保持空)。

        调用方须持有 self._lock。
        """
        if self._scanned or self._skills_dir is None:
            self._scanned = True
            return
        for path in self._skills_dir.glob("*.md"):
            self._cache[path.stem] = path.read_text(encoding="utf-8")
        self._scanned = True

    def load(self, names: list[str]) -> dict[str, str]:
        """按名批量加载 skill,返回 {name: content}。

        缺失的 skill 名抛 KeyError(异常消息列出全部缺失项,便于诊断)。
        空名列表返回空 dict(默认场景,Agent.skills == [])。
        """
        if not names:
            return {}
        with self._lock:
            self._scan()
            missing = [n for n in names if n not in self._cache]
            if missing:
                raise KeyError(f"Skills not found: {missing}")
            return {n: self._cache[n] for n in names}

    def list_available(self) -> list[str]:
        """列出所有可用 skill 名(排序返回,便于稳定输出)。"""
        with self._lock:
            self._scan()
            return sorted(self._cache.keys())

    def reload(self) -> None:
        """清缓存重扫(支持 skills/ 目录热更新)。

        SP7b SkillGenerator 写入 auto_*.md 后调用此方法刷新缓存。
        用锁保护:避免与并发的 load() 竞态(读到半扫的 _cache)。
        """
        with self._lock:
            self._cache.clear()
            self._scanned = False
            self._scan()
