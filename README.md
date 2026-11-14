# AgentTeam

以「节省人类注意力」为第一性原理的多 Agent 系统 —— 对标 [DeepSeek dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype) 的架构设计,自主实现的生产化内核。

> **一句话架构**: 前台单 Router 统一交互 + 后台多 Worker 隔离并发,Trigger/Thread/Ticket/Mandate/Subscription/Attention 一等公民,24h 事件驱动。

## 架构总览

```
Source(webhook/IM/GitHub) ──► SourceDaemon(asyncio 常驻轮询)
        │ Trigger{actor, thread_key, text}          不可变入站事件
        ▼
   gate 程序快筛                                     白名单/关键词/正则,毫秒级
        ▼
   Eval 轻量模型三事实研判                            针对本项目/有实施意图/方向已定
        │ accepted → Ticket(inferred Mandate)        观察模式默认:先记录 would-accept
        ▼
   Ticket(已授权工作单元)                             状态机独立于执行器
        ▼
   Router                                            接单/派工,无执行工具,批注 ≤30 词
        ▼
   Worker Session(每 Ticket 一个会话+独立工作目录)    ReAct 工具循环,transcript 即 checkpoint
        │
        ├─ Attention(审批门/结构化提问,一次一个)      人工批准 = Mandate 授权延续
        ├─ Outbox(先存后发,指数退避,同工单串行)      IM 通知可靠出站
        └─ Subscription(工单↔线程多对多)              同线程追问直达 worker,只入不回
```

**Ticket 状态机**: `pending → running ⇄ waiting_human → done | failed`,任意非终态可 `cancelled(人工撤权)`。

## 快速开始

```bash
# 1. 启动(需 Python 3.12+,web/dist 已构建)
AGENTTEAM_SKILLS_DIR=./skills uvicorn agentteam.api.server:create_app --factory --port 8000

# 2. 安装工人池预设(或经控制台「工人池」页添加)
agentteam install-preset dev_pool

# 3. 打开控制台
#    http://localhost:8000/  → 注意件收件箱 / 工单看板 / 线程 / 观察模式 / 工人池 / 入站源
```

提交请求(三选一):
- 控制台「工单看板 → 新请求」
- CLI: `agentteam submit "任务描述" [worker]`
- API: `POST /api/requests {"text": "...", "worker_name": "coder"}`

## API 一览(前缀 /api)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /requests | 提交请求(Router 决定派工或直接答复) |
| GET | /tickets?status= | 工单看板 |
| GET | /tickets/{id} | 工单详情(mandate/会话) |
| GET | /tickets/{id}/messages | 会话 transcript |
| GET | /tickets/{id}/events | 事件流(REST) |
| GET | /tickets/{id}/stream | 事件流(SSE,Last-Event-ID 断点续传) |
| POST | /tickets/{id}/messages | 追问直达 worker(只入不回) |
| POST | /tickets/{id}/cancel | 撤权(revoked_by_human) |
| GET | /attentions?status=pending | 注意件收件箱 |
| POST | /attentions/{id}/decide | 批准/拒绝/回答 |
| GET/POST/PUT/DELETE | /workers | 工人池 CRUD |
| GET/POST/PUT/DELETE | /sources | 入站源 CRUD |
| POST | /sources/{name}/webhook | 入站事件(X-Source-Secret 校验) |
| GET | /sources/observe/reviews | 观察模式 would-accept 账本 |
| POST | /sources/observe/{id}/confirm | 人工确认转正立项 |
| GET/POST | /threads · /threads/inbound | 线程聚合与入站记录 |
| GET | /tickets/{id}/notifications | IM 通知回执(outbox) |
| GET | /skills · /health | 技能列表 / 健康检查 |

## 配置(env_prefix=AGENTTEAM_)

| 变量 | 默认 | 说明 |
|---|---|---|
| DB_PATH | data/agentteam_v2.db | SQLite(独立于 v1 存档) |
| V2_MAX_SESSION_WORKERS | 8 | 同时执行的 Ticket 数上限 |
| V2_DEFAULT_PROVIDER / V2_DEFAULT_MODEL | qwen / qwen-max | Router 与默认 worker 模型 |
| NOTIFY_WEBHOOK_URL | (空) | IM 出站 webhook;空 = 关闭 |
| V2_SOURCE_POLL_INTERVAL | 10s | SourceDaemon 轮询间隔 |

模型 Key 走环境变量: DASHSCOPE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY(按 provider)。

## 测试与部署

```bash
pytest tests/ -q          # 全量测试(FakeLLM 全链路,无需真实 Key)
cd web && npm run build   # 控制台构建 → FastAPI 静态托管
docker compose up         # 容器化部署(Dockerfile 多阶段构建)
```

## 设计文档

- dsh-superagent 精读 spec: `docs/superpowers/specs/2026-11-03-sp12-superagent-attention-design.md`
- v1(langgraph 团队树引擎)已退役: 代码存于 git 历史(d622d6c..9732136),`data/agentteam.db` 为只读存档。
