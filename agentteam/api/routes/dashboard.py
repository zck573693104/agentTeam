"""GET /api/dashboard 端点。"""
from __future__ import annotations

from fastapi import APIRouter

from agentteam.api.routes.runs import run_to_dict
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo


def dashboard_router(run_repo: RunRepo, audit_repo: AuditRepo) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["dashboard"])

    @router.get("/dashboard")
    def get_dashboard():
        # SQL 聚合替代 Python 端遍历(原实现 list_runs() 全表返回再循环统计,
        # 大数据量下内存与 CPU 双重浪费)。三条聚合查询均走索引:
        # - by_status 走 idx_runs_status
        # - by_team 走 idx_runs_team_name
        # - total_tokens 走全表扫描但单次聚合,远优于 N 次行读
        by_status = run_repo.aggregate_by_status()
        by_team = run_repo.aggregate_by_team()
        total_tokens = run_repo.sum_total_tokens()
        total_runs = sum(by_status.values())

        # recent_runs 只需最近 10 条,用 LIMIT 10 避免全表加载
        recent_rows = run_repo.list_runs(limit=10)
        recent = [run_to_dict(r) for r in recent_rows]
        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "by_status": by_status,
            "by_team": by_team,
            "recent_runs": recent,
        }

    @router.get("/dashboard/multi_dim")
    def get_dashboard_multi_dim():
        """P-B8 多维统计端点(对标阿里云 AgentTeams "运维仪表盘多维分析")。

        返回:
        - by_status: 按 run status 分组计数
        - by_team: 按 team_name 分组计数
        - tokens_by_team: 按 team_name 汇总 token 用量(识别 top 消费团队)
        - by_chain: 按 run_events.chain 分组计数(call/tool/decision 三链分布)
        - top_tools: 工具调用频次 top 10(从 run_events.payload 提取)
        - total_tokens: 全局 token 总用量
        - total_runs: 全局 run 总数
        """
        by_status = run_repo.aggregate_by_status()
        by_team = run_repo.aggregate_by_team()
        total_tokens = run_repo.sum_total_tokens()
        total_runs = sum(by_status.values())

        # P-B8: 按 team 汇总 token 用量(识别 top 消费团队)
        tokens_by_team = run_repo.sum_tokens_by_team()

        # P-B8: 按 chain 分组计数(三链分布,识别工具密集/决策密集型 run)
        by_chain = audit_repo.aggregate_by_chain()

        # P-B8: 工具调用频次 top 10(从 tool_call 事件的 payload.tools 提取)
        top_tools = audit_repo.aggregate_top_tools(limit=10)

        return {
            "total_runs": total_runs,
            "total_tokens": total_tokens,
            "by_status": by_status,
            "by_team": by_team,
            "tokens_by_team": tokens_by_team,
            "by_chain": by_chain,
            "top_tools": top_tools,
        }

    return router
