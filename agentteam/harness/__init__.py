"""agentteam.harness —— opencode 套壳执行内核（SP8）。

把 AgentTeam 控制平面（Team schema / 审批语义 / 审计 / API）架在
opencode（MIT, https://github.com/sst/opencode）headless server 之上：

- opencode_client: REST + SSE 客户端
- translator:      Team JSON → opencode 会话/权限/MCP/provider 配置
- approval:        三级审批（step/worker 控制平面门 + tool 级 permission 桥）
- engine:          HarnessRunner（plan→dispatch→review 编排，graph 协议适配）
- events:          opencode 事件 → AgentTeam trace 事件词表

设计依据：docs/opencode-harness-design.md
"""
