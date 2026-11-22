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
        │                                              ↑ 注入【历史教训】(按任务检索记忆库)
        ├─ Attention(审批门/结构化提问/技能提案)       人工批准 = Mandate 授权延续
        ├─ Outbox(先存后发,指数退避,同工单串行)      IM 通知可靠出站
        └─ Subscription(工单↔线程多对多)              同线程追问直达 worker,只入不回
        │
        ▼ 终态(done/failed)
   Postmortem(独立单线程池,sleep-time 蒸馏)          教训 → 文件式记忆库(L0/L2)
        │                                             可复用流程 → 技能提案 → 注意件审批 → 晋升 skills/
        ▼
   MemoryStore(memory/ 目录,markdown,人可审)         OpenViking 范式:零依赖打分检索
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
| GET | /memory | 经验记忆库(复盘蒸馏的教训清单) |
| GET | /skills · /health | 技能列表 / 健康检查 |

## 配置(env_prefix=AGENTTEAM_)

| 变量 | 默认 | 说明 |
|---|---|---|
| DB_PATH | data/agentteam_v2.db | SQLite(独立于 v1 存档) |
| V2_MAX_SESSION_WORKERS | 8 | 同时执行的 Ticket 数上限 |
| V2_DEFAULT_PROVIDER / V2_DEFAULT_MODEL | qwen / qwen-max | Router 与默认 worker 模型 |
| V2_AUX_PROVIDER / V2_AUX_MODEL | (空) | 轻量辅助模型(Eval 研判/复盘蒸馏);空 = 回落默认模型 |
| V2_MEMORY_DIR | (空) | 经验记忆库根目录;空 = db 同目录 memory/,`off` 显式关闭 |
| V2_POSTMORTEM_ENABLED | true | 工单终态后自动复盘蒸馏 |
| V2_MEMORY_EVICT_DAYS | 30 | 从未命中教训归档天数;0 = 关闭 |
| V2_WORKSPACE_ARCHIVE_DAYS | 7 | 终态工单工作区归档天数;0 = 关闭 |
| V2_EVAL_DISTILL_THRESHOLD | 5 | 观察模式确认样本蒸馏阈值;0 = 关闭 |
| NOTIFY_WEBHOOK_URL | (空) | IM 出站 webhook;空 = 关闭 |
| V2_SOURCE_POLL_INTERVAL | 10s | SourceDaemon 轮询间隔 |

模型 Key 走环境变量: DASHSCOPE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY(按 provider)。

## 外部数据渠道接入（部门晨报 / 代码 / 禅道 / 考勤）

领导每日晨报由 **cron 源**驱动：到点发 Trigger →（可 skip_eval）→ 定向派给部门领导 worker，自动附上**昨日每人工单统计**（完成/失败/在途）：

```json
{"name": "briefing_pms", "kind": "cron", "config": {
  "schedule": "09:00",
  "text": "生成今日部门晨报:按下属逐人汇总昨日工单完成/失败/在途…",
  "stats": true, "worker": "pms_lead",
  "allowed_actors": ["cron"], "mode": "auto", "skip_eval": true
}}
```

其他三个渠道都是**现成接口**，无需改代码，按渠道特性接入：

| 渠道 | 接入方式 |
|---|---|
| **代码情况** | `github` 源（config.repos 填下属仓库，配 GITHUB_TOKEN）→ issue/评论自动进线程；自有 Git 服务可用 webhook 源推 commit/PR 事件 |
| **禅道任务** | 禅道侧配置 webhook 或轮询脚本调 `POST /api/sources/{name}/webhook` 推任务状态变更（actor=禅道用户名，同 thread_key 自动聚合） |
| **考勤数据** | 考勤系统 webhook 推每日出勤摘要（缺勤/迟到），或由晨报 worker 经 MCP 工具拉取 |

渠道推送的文本会进对应线程（thread_key 聚合），晨报 worker 下钻线程即得上下文。**安全铁律**：外部文本只是数据，过 gate 后才进 prompt，且不改变授权。



> 范式来源: [Letta sleep-time compute](https://www.letta.com/blog/sleep-time-compute)(主 agent 无权写记忆,独立角色异步蒸馏) + [OpenViking](https://github.com/volcengine/OpenViking)(上下文即文件系统,L0 摘要/L2 全文分级)。

```
工单终态 → Postmortem(独立线程池,辅助模型)
             ├─ 教训(≤3条) → memory/lessons/<id>.md(L2) + _index.md(L0 一行账本)
             └─ 可复用流程 → proposal 注意件 → 人工批准 → 写入 skills/ 并热加载
新工单派工 → Router 注入 L0 摘要(避坑) + Worker 注入【历史教训】top-k(零依赖相关性打分)

观察模式人工确认(would_accept 转正) → 确认样本入库
             → 攒够阈值 → 蒸馏「入站研判校准规则」→ EvalEngine prompt 注入(gate/Eval 自我校准)
housekeeping(sleep-time 兼职保洁,每次复盘顺带执行):
             ├─ 从未命中且超期的教训 → memory/_archive/(淘汰)
             └─ 超期终态工单工作区 → workspaces/_archive/(归档,产出零丢失)
```

- 记忆是**人可读可改的 markdown 文件**(`memory/`),不是黑盒向量库;`use_count` 命中计数驱动淘汰
- 复盘幂等(每工单最多一次尝试)、失败只落 `postmortem_failed` 事件,绝不影响工单状态
- 琐碎会话(无工具交互,3 行 transcript)不值得蒸馏,自动跳过
- 控制台「记忆库」页(`/v2/memory`):教训清单 + 校准规则可视化

## 测试与部署

```bash
pytest tests/ -q          # 全量测试(FakeLLM 全链路,无需真实 Key)
cd web && npm run build   # 控制台构建 → FastAPI 静态托管
docker compose up         # 容器化部署(Dockerfile 多阶段构建)
```

## 设计文档

- dsh-superagent 精读 spec: `docs/superpowers/specs/2026-11-03-sp12-superagent-attention-design.md`
- v1(langgraph 团队树引擎)已退役: 代码存于 git 历史(d622d6c..9732136),`data/agentteam.db` 为只读存档。
