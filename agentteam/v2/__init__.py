"""AgentTeam v2 — dsh-superagent 式内核（Ticket/Thread/Attention 一等公民）。

设计来源: docs/superpowers/specs/2026-11-03-sp12-superagent-attention-design.md
架构: Source → Trigger → gate → Eval → Ticket → Router → Worker Session
      + Attention(审批/提问) + Subscription(追问直通) + Outbox(可靠出站)

v1 已退役(代码存于 git 历史): v2 内核接管 /api,独立 DB。
"""
