"""quotas 表的读写 + 配额校验(P-A4 对标阿里云 AgentTeams "成本可控")。

设计:
- 每个 team 配置 token_limit(0=不限)/period_seconds(默认 86400=1 天)
- check_quota(team_name) 返回 (allowed, used, limit, period):
  统计当前 period 窗口内已完成 run 的 total_tokens 总和,与 limit 比较
- 超额时 RunManager.start_run 前拒绝,返回 429
- 未配置 quota 的 team 默认放行(token_limit=0 视为不限)
"""
from __future__ import annotations

import time
from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class QuotaRepo(BaseSqliteRepo):
    """quotas 表的读写 + 配额校验逻辑。

    配额校验依赖 runs.total_tokens + runs.ended_at:
    - 统计 period 窗口内 ended_at >= (now - period) 的 run 的 token 总和
    - 与 quotas.token_limit 比较
    """

    def upsert(
        self,
        team_name: str,
        token_limit: int,
        period_seconds: int = 86400,
        description: str = "",
    ) -> None:
        """INSERT OR UPDATE 配额配置。"""
        now = _now()
        self._execute(
            "INSERT INTO quotas (team_name, token_limit, period_seconds, created_at, updated_at, description) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(team_name) DO UPDATE SET "
            "token_limit=excluded.token_limit, period_seconds=excluded.period_seconds, "
            "updated_at=excluded.updated_at, description=excluded.description",
            (team_name, token_limit, period_seconds, now, now, description),
        )

    def get(self, team_name: str) -> dict | None:
        """读取单个 team 的配额配置,无配置返回 None。"""
        row = self._fetchone("SELECT * FROM quotas WHERE team_name = ?", (team_name,))
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        """列出所有配额配置。"""
        rows = self._fetchall("SELECT * FROM quotas ORDER BY team_name")
        return [dict(r) for r in rows]

    def delete(self, team_name: str) -> bool:
        """删除配额配置,返回是否删除成功。"""
        cur = self._execute("DELETE FROM quotas WHERE team_name = ?", (team_name,))
        return cur.rowcount > 0

    def check_quota(self, team_name: str) -> dict[str, Any]:
        """校验 team 当前是否可启动新 run。

        返回 dict:
        - allowed: bool 是否允许
        - used: int 当前周期已用 token
        - limit: int 配额上限(0=不限)
        - period: int 周期秒数
        - team_name: str

        逻辑:
        - team 无配额配置 → allowed=True, limit=0(视为不限)
        - token_limit=0 → allowed=True(显式不限)
        - used >= limit → allowed=False
        - 否则 allowed=True
        """
        quota = self.get(team_name)
        if quota is None:
            return {
                "allowed": True,
                "used": 0,
                "limit": 0,
                "period": 0,
                "team_name": team_name,
            }
        token_limit = int(quota["token_limit"])
        period = int(quota["period_seconds"])
        if token_limit <= 0:
            return {
                "allowed": True,
                "used": 0,
                "limit": 0,
                "period": period,
                "team_name": team_name,
            }
        # 计算 period 窗口内已用 token:ended_at 在窗口内的 run 求和
        # 用 ISO8601 字符串比较(SQLite TEXT 列),需保证 runs.created_at/ended_at 都是 ISO8601
        # 窗口起点:now - period_seconds(秒),转 ISO8601
        window_start_iso = _iso_offset_seconds(-period)
        row = self._fetchone(
            "SELECT COALESCE(SUM(total_tokens), 0) AS used "
            "FROM runs "
            "WHERE team_name = ? AND ended_at IS NOT NULL AND ended_at >= ?",
            (team_name, window_start_iso),
        )
        used = int(row["used"]) if row else 0
        allowed = used < token_limit
        return {
            "allowed": allowed,
            "used": used,
            "limit": token_limit,
            "period": period,
            "team_name": team_name,
        }


def _iso_offset_seconds(offset_seconds: int) -> str:
    """当前时间偏移 offset_seconds 秒的 ISO8601 字符串。

    与 storage/utils.py 的 utcnow_iso 保持一致格式(isoformat(),含微秒与 +00:00 后缀)。
    """
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat()
