"""gate 程序快筛(dsh: 毫秒级白名单/关键词,不进 LLM)。"""
from __future__ import annotations

import re


class Gate:
    """程序化快筛。

    config 字段(全部可省,省略 = 通过):
    - allowed_actors: list[str], ["*"] 或包含 actor 即通过(白名单,Source 自己的身份体系)
    - keywords: list[str], 文本包含任一关键词即通过;["*"] = 全部通过
    - regex: str, 文本匹配即通过
    """

    def __init__(self, config: dict) -> None:
        self.allowed_actors: list[str] = list((config or {}).get("allowed_actors", ["*"]))
        self.keywords: list[str] = list((config or {}).get("keywords", ["*"]))
        self.regex: str | None = (config or {}).get("regex")

    def check(self, actor: str, text: str) -> tuple[bool, str]:
        if "*" not in self.allowed_actors and actor not in self.allowed_actors:
            return False, f"actor '{actor}' 不在白名单"
        if "*" not in self.keywords:
            if not any(kw in text for kw in self.keywords):
                return False, "文本未命中关键词"
        if self.regex and not re.search(self.regex, text):
            return False, "文本未匹配正则"
        return True, "passed"
