"""IM 通知出口(P-A5 落地)。

run 生命周期关键事件(等待审批/完成/失败)推送到 IM webhook,
支持飞书/企业微信/钉钉 bot 与通用 JSON 端点。
"""
from agentteam.notify.service import NotificationService, format_payload

__all__ = ["NotificationService", "format_payload"]
