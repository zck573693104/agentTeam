"""MemoryStore — 文件式经验记忆库(P4 学习闭环的存储层)。

范式(OpenViking L0/L2):记忆 = 人可读可改的 markdown 文件,不是黑盒向量库。
- L0(摘要): lessons/_index.md 一行账本(带 id),供 Router 注入与复盘判重廉价读取
- L2(全文): lessons/<id>.md 单条教训(frontmatter + 【条件】【做法】正文)

目录布局(memory_dir 下):
  lessons/_index.md            全局 L0
  lessons/<id>.md              全局 L2
  repo/<slug>/_index.md        仓库级 L0
  repo/<slug>/lessons/<id>.md  仓库级 L2

检索为零依赖打分(CJK 双字串 + ASCII 词重叠),不做嵌入;够用、可单测、可解释。
进程内一把 RLock;写入原子化(temp + os.replace)。
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from agentteam.logging_config import get_logger
from agentteam.storage.utils import utcnow_iso as _now

logger = get_logger("v2.kernel.memory")

_MAX_INDEX_LINE = 100      # L0 每行摘要截断
_RECALL_LIMIT_DEFAULT = 5
_RECALL_MAX_CHARS = 2000
_ASCII_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]{1,}")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def repo_slug(repo_path: str | None) -> str | None:
    """repo_path → 稳定 slug(目录名 + 全路径短哈希,防重名);无 repo → None。"""
    if not repo_path:
        return None
    norm = str(Path(repo_path).resolve())
    name = Path(norm).name or "repo"
    return f"{name}-{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:8]}"


def _terms(text: str) -> set[str]:
    """极简词袋:ASCII 词(≥2 字符) + CJK 相邻双字串。零依赖,确定性。"""
    if not text:
        return set()
    out: set[str] = set()
    for m in _ASCII_WORD.finditer(text):
        out.add(m.group(0).lower())
    cjk = _CJK.findall(text)
    for a, b in zip(cjk, cjk[1:]):
        out.add(a + b)
    return out


@dataclass
class LessonDraft:
    """复盘产出的一条教训(写入前的载体)。"""

    condition: str            # 触发条件(什么仓库/命令/场景)
    lesson: str               # 可执行做法
    scope: str = "repo"       # global | repo
    existing_id: str | None = None  # 判重命中 → 合并更新该条


@dataclass
class RecallResult:
    block: str = ""           # 注入 worker 的【历史教训】块(空串 = 无关经验)
    ids: list[str] = field(default_factory=list)


class MemoryStore:
    """经验记忆库(lesson 文件 + L0 索引)。root 为 None 时整体停用(零副作用)。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.RLock()

    # —— 路径 ——
    def _dir_for(self, repo: str | None) -> Path | None:
        if self._root is None:
            return None
        slug = repo_slug(repo)
        base = self._root / "repo" / slug if slug else self._root
        return base / "lessons"

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", "", text or "").lower()

    # —— 读写 lesson 文件 ——
    @staticmethod
    def _parse(path: Path) -> dict | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta: dict = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) == 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        meta[k.strip()] = v.strip()
                body = parts[2].strip()
        meta["_body"] = body
        return meta

    @staticmethod
    def _serialize(meta: dict, body: str) -> str:
        lines = ["---"]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
        lines.append(body)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _write_lesson(self, lessons_dir: Path, lesson_id: str, meta: dict, body: str) -> None:
        meta["id"] = lesson_id
        self._atomic_write(lessons_dir / f"{lesson_id}.md", self._serialize(meta, body))
        self._rebuild_index(lessons_dir)

    def _rebuild_index(self, lessons_dir: Path) -> None:
        """L0: 每个 lesson 一行「- <id> 条件 → 做法」(截断)。"""
        lines: list[str] = []
        for path in sorted(lessons_dir.glob("les_*.md")):
            meta = self._parse(path)
            if not meta:
                continue
            one = f"{meta.get('condition', '')} → {meta.get('lesson', '')}"
            lines.append(f"- {meta.get('id', path.stem)} {one[:_MAX_INDEX_LINE]}")
        index = lessons_dir / "_index.md"
        header = "# 教训账本(L0)\n\n一行一条,供路由注入与复盘判重;全文见同目录 <id>.md。\n"
        self._atomic_write(index, header + ("\n".join(lines) + "\n" if lines else ""))

    # —— 写入(复盘蒸馏产物) ——
    def add_lessons(self, repo: str | None, drafts: list[LessonDraft]) -> list[dict]:
        """落库一批教训;文本规范化哈希兜底判重(同文不重复立项)。返回写入的条目。"""
        lessons_dir = self._dir_for(repo if repo else None)
        if lessons_dir is None or not drafts:
            return []
        written: list[dict] = []
        with self._lock:
            existing_hashes: dict[str, str] = {}
            for path in lessons_dir.glob("les_*.md"):
                meta = self._parse(path) or {}
                h = hashlib.sha1(self._normalize(meta.get("_body", "")).encode("utf-8")).hexdigest()
                existing_hashes[h] = str(meta.get("id", path.stem))
            for d in drafts:
                body = f"【条件】{d.condition.strip()}\n\n【做法】{d.lesson.strip()}"
                h = hashlib.sha1(self._normalize(body).encode("utf-8")).hexdigest()
                target_id = d.existing_id or existing_hashes.get(h)
                if target_id and (lessons_dir / f"{target_id}.md").exists():
                    # 合并更新:保留原 id 与创建时间,以最新表述为准
                    old = self._parse(lessons_dir / f"{target_id}.md") or {}
                    self._write_lesson(lessons_dir, target_id, {
                        k: v for k, v in old.items() if k != "_body"
                    } | {
                        "condition": d.condition.strip(),
                        "lesson": d.lesson.strip(),
                        "updated_at": _now(),
                    }, body)
                    written.append({"id": target_id, "merged": True})
                    continue
                lesson_id = f"les_{uuid4().hex[:10]}"
                meta = {
                    "condition": d.condition.strip(),
                    "lesson": d.lesson.strip(),
                    "scope": "repo" if repo else "global",
                    "repo": repo_slug(repo) or "",
                    "kind": "lesson",
                    "created_at": _now(),
                    "use_count": "0",
                    "last_used_at": "",
                }
                self._write_lesson(lessons_dir, lesson_id, meta, body)
                existing_hashes[h] = lesson_id
                written.append({"id": lesson_id, "merged": False})
        return written

    # —— 读取(派工注入) ——
    def _candidates(self, repo: str | None) -> list[dict]:
        out: list[dict] = []
        if self._root is None:
            return out
        # 有仓库 → 仓库级 + 全局;无仓库 → 只查全局(不重复扫描)
        scopes = ((self._dir_for(repo), True), (self._dir_for(None), False)) if repo \
            else ((self._dir_for(None), False),)
        for scope_dir, is_repo in scopes:
            if scope_dir is None:
                continue
            for path in scope_dir.glob("les_*.md"):
                meta = self._parse(path)
                if not meta:
                    continue
                meta["_is_repo"] = is_repo
                out.append(meta)
        return out

    @staticmethod
    def _score(task_terms: set[str], meta: dict, repo_match: bool) -> float:
        text = f"{meta.get('condition', '')} {meta.get('_body', '')}"
        overlap = len(task_terms & _terms(text))
        if overlap == 0:
            return 0.0
        score = overlap * 2.0
        if repo_match:
            score += 1.0
        try:
            score += min(float(meta.get("use_count", 0) or 0) * 0.1, 1.0)
        except (TypeError, ValueError):
            pass
        return score

    def recall(
        self, task_text: str, repo_path: str | None = None,
        limit: int = _RECALL_LIMIT_DEFAULT, max_chars: int = _RECALL_MAX_CHARS,
    ) -> RecallResult:
        """按相关性取 top-k 教训,格式化为注入块(零命中返回空块)。"""
        lessons_dir = self._dir_for(repo_path)
        if lessons_dir is None or not task_text:
            return RecallResult()
        slug = repo_slug(repo_path)
        task_terms = _terms(task_text)
        scored: list[tuple[float, dict]] = []
        with self._lock:
            for meta in self._candidates(repo_path):
                s = self._score(task_terms, meta, repo_match=bool(slug) and meta.get("_is_repo"))
                if s > 0:
                    scored.append((s, meta))
            scored.sort(key=lambda t: -t[0])
            picked = scored[:limit]
            if not picked:
                return RecallResult()
            lines = ["【历史教训】(来自既往工单复盘,直接可采信的做事规则)"]
            ids: list[str] = []
            used = 0
            for _, meta in picked:
                line = f"- [条件] {meta.get('condition', '')} → [做法] {meta.get('lesson', '')}"
                if used + len(line) > max_chars and ids:
                    break
                lines.append(line)
                ids.append(str(meta.get("id", "")))
                used += len(line)
            return RecallResult(block="\n".join(lines), ids=ids)

    def record_use(self, ids: list[str]) -> None:
        """命中即计数(供后续淘汰策略;best-effort 不抛错)。"""
        if self._root is None or not ids:
            return
        now = _now()
        with self._lock:
            for scope_dir in {p.parent for p in self._root.glob("repo/*/lessons/les_*.md")} | {
                p for p in [self._root / "lessons"] if p.is_dir()
            }:
                for lesson_id in ids:
                    path = scope_dir / f"{lesson_id}.md"
                    if not path.exists():
                        continue
                    meta = self._parse(path)
                    if not meta:
                        continue
                    try:
                        count = int(meta.get("use_count", 0) or 0) + 1
                    except (TypeError, ValueError):
                        count = 1
                    meta["use_count"] = str(count)
                    meta["last_used_at"] = now
                    body = meta.pop("_body")
                    self._atomic_write(path, self._serialize(meta, body))

    # —— L0 摘要(Router 注入 / 复盘判重) ——
    def l0_lines(self, repo_path: str | None = None, limit: int = 5) -> list[str]:
        """仓库级 L0 优先,不足补全局;每行已含 id(复盘合并靠它)。"""
        if self._root is None:
            return []
        lines: list[str] = []
        for lessons_dir in (self._dir_for(repo_path), self._dir_for(None)):
            if lessons_dir is None:
                continue
            index = lessons_dir / "_index.md"
            if not index.exists():
                continue
            try:
                raw = index.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in raw.splitlines():
                if line.startswith("- ") and line not in lines:
                    lines.append(line)
                    if len(lines) >= limit:
                        return lines
        return lines

    def list_lessons(self, limit: int = 200) -> list[dict]:
        """API 用:全库教训清单(不含正文;已归档的排除)。"""
        if self._root is None:
            return []
        out: list[dict] = []
        with self._lock:
            for path in sorted(self._root.glob("repo/*/lessons/les_*.md")) + sorted(
                (self._root / "lessons").glob("les_*.md")
            ):
                meta = self._parse(path)
                if not meta:
                    continue
                out.append({
                    "id": meta.get("id", path.stem),
                    "scope": meta.get("scope", ""),
                    "repo": meta.get("repo", ""),
                    "condition": meta.get("condition", ""),
                    "lesson": meta.get("lesson", ""),
                    "created_at": meta.get("created_at", ""),
                    "use_count": meta.get("use_count", "0"),
                    "last_used_at": meta.get("last_used_at", "") or None,
                })
                if len(out) >= limit:
                    break
        return out

    # —— 淘汰(housekeeping:记忆和代码一样需要维护) ——
    def evict_stale(self, max_idle_days: int = 30) -> list[str]:
        """归档从未被命中且超过 idle 天数的教训(移入 _archive/,人可查可恢复)。

        返回被归档的 lesson id 列表。max_idle_days <= 0 → 不做任何事。
        """
        if self._root is None or max_idle_days <= 0:
            return []
        now = datetime.now(timezone.utc)
        evicted: list[str] = []
        with self._lock:
            for lessons_dir in {p.parent for p in self._root.glob("repo/*/lessons/les_*.md")} | {
                p for p in [self._root / "lessons"] if p.is_dir()
            }:
                archive = lessons_dir / "_archive"
                for path in list(lessons_dir.glob("les_*.md")):
                    meta = self._parse(path)
                    if not meta:
                        continue
                    try:
                        count = int(meta.get("use_count", 0) or 0)
                        created = datetime.fromisoformat(meta.get("created_at", ""))
                    except (TypeError, ValueError):
                        continue
                    if count > 0 or created.tzinfo is None:
                        continue  # 命中过的保留;无时区的时间戳视为非法,保守不淘汰
                    if now - created < timedelta(days=max_idle_days):
                        continue
                    archive.mkdir(exist_ok=True)
                    os.replace(path, archive / path.name)
                    evicted.append(str(meta.get("id", path.stem)))
                if evicted:
                    self._rebuild_index(lessons_dir)
        if evicted:
            logger.info("memory evicted %d stale lessons: %s", len(evicted), evicted)
        return evicted
