# SP6 演进路线图 Master Plan（P0-P4 总体实施计划）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实施 SP6 路线图 5 项改进（P0 Run 可恢复性 / P1 Plan DAG / P2 ToolRegistry 缓存 key / P3 ModelProvider 注册表 / P4 Run 取消），补齐架构扩展性短板。

**Architecture:** 5 个独立子项目，按 P2 → P3 → P0 → P1 → P4 顺序实施。每个子项目独立 spec 章节（见 `docs/superpowers/specs/2026-07-18-sp6-roadmap-design.md` §2-6）、独立 sub-plan、独立 commit。所有子项目完成后做全量回归。

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, SQLite, pytest, langchain-mcp-adapters

---

## 文件结构

| 文件 | 责任 | 动作 | 所属子项目 |
|------|------|------|-----------|
| `agentteam/tools/registry.py` | ToolRegistry 缓存 key 改为 tuple | 修改 | P2 |
| `tests/tools/test_mcp_leak.py` | 缓存 key 测试更新 | 修改 | P2 |
| `tests/tools/test_registry_cache_key.py` | 同名不同配置测试 | 新建 | P2 |
| `agentteam/models/provider.py` | ModelProvider 注册表化 | 修改 | P3 |
| `agentteam/models/adapters/base.py` | BaseAdapter ABC | 新建 | P3 |
| `agentteam/models/adapters/__init__.py` | 内置 adapter 自动注册 | 修改 | P3 |
| `agentteam/models/adapters/{qwen,openai,anthropic,ollama}.py` | 继承 BaseAdapter | 修改 | P3 |
| `tests/models/test_provider_registry.py` | 注册表测试 | 新建 | P3 |
| `agentteam/api/run_manager.py` | +checkpointer +recompile_and_resume +has_graph | 修改 | P0 |
| `agentteam/api/routes/runs.py` | approve_run lazy recompile 路径 | 修改 | P0 |
| `agentteam/api/server.py` | RunManager 注入 checkpointer | 修改 | P0 |
| `tests/api/test_run_resumability.py` | lazy recompile 测试 | 新建 | P0 |
| `agentteam/runtime/nodes.py` | PlanStep + Plan DAG 模型 + dag 路由 | 修改 | P1 |
| `agentteam/runtime/state.py` | +completed_steps +skipped_steps | 修改 | P1 |
| `agentteam/runtime/graph.py` | TeamCompiler 支持 dag 路由 | 修改 | P1 |
| `tests/runtime/test_plan_dag.py` | DAG 执行测试 | 新建 | P1 |
| `agentteam/api/run_manager.py` | +cancel_run +is_cancelled +_cancel_events | 修改 | P4 |
| `agentteam/api/routes/runs.py` | +POST /cancel endpoint | 修改 | P4 |
| `agentteam/runtime/nodes.py` | make_agent_step +run_manager 参数 | 修改 | P4 |
| `agentteam/runtime/graph.py` | TeamCompiler 传递 run_manager | 修改 | P4 |
| `tests/api/test_run_cancel.py` | 取消机制测试 | 新建 | P4 |

---

## 实施顺序（5 个 Phase，每个独立 commit）

### Phase 1: P2 ToolRegistry 缓存 key 修正
**Sub-plan:** `docs/superpowers/plans/2026-07-18-sp6-p2-toolregistry-cache-key.md`
**预计测试新增:** 4 个
**关键变更:** `_loaded_servers: set[str]` → `set[tuple]`，新增 `_server_cache_key()` helper
**风险:** 低，纯小改动

### Phase 2: P3 ModelProvider 注册表化
**Sub-plan:** `docs/superpowers/plans/2026-07-18-sp6-p3-modelprovider-registry.md`
**预计测试新增:** 5 个
**关键变更:** if/elif → class-level registry + BaseAdapter ABC + 内置 adapter 自动注册
**风险:** 中，需注意 import 副作用不产生循环依赖

### Phase 3: P0 Run 可恢复性
**Sub-plan:** `docs/superpowers/plans/2026-07-18-sp6-p0-run-resumability.md`
**预计测试新增:** 4 个
**关键变更:** RunManager +checkpointer +recompile_and_resume；approve_run lazy recompile 路径
**风险:** 中，是最大可用性提升但改动 approve_run 核心路径

### Phase 4: P1 Plan DAG
**Sub-plan:** `docs/superpowers/plans/2026-07-18-sp6-p1-plan-dag.md`
**预计测试新增:** 5 个
**关键变更:** PlanStep +depends_on +condition +id；Plan +execution_mode；dag 路由算法；state schema 扩展
**风险:** 高，改 state schema 需注意 checkpoint 兼容

### Phase 5: P4 Run 取消机制
**Sub-plan:** `docs/superpowers/plans/2026-07-18-sp6-p4-run-cancel.md`
**预计测试新增:** 5 个
**关键变更:** RunManager +cancel_run +is_cancelled；+POST /cancel endpoint；make_agent_step +run_manager 参数
**风险:** 中，需注意 RunCancelledError 不被吞掉

---

## 每个 Phase 的标准 TDD 流程

每个 Phase 严格按 sub-plan 中的 task 顺序执行：
1. 读 sub-plan
2. 对每个 Task: RED（写失败测试）→ GREEN（最小实现）→ COMMIT
3. Phase 完成后跑全量回归 `python -m pytest -q`
4. 确认 418 + N 新测试通过，无回归
5. Phase 级 commit（若 sub-plan 内已逐 task commit，则无额外 commit）

---

## 验收清单

- [ ] Phase 1 (P2) 完成：4 个新测试 + 全量回归通过
- [ ] Phase 2 (P3) 完成：5 个新测试 + 全量回归通过
- [ ] Phase 3 (P0) 完成：4 个新测试 + 全量回归通过
- [ ] Phase 4 (P1) 完成：5 个新测试 + 全量回归通过
- [ ] Phase 5 (P4) 完成：5 个新测试 + 全量回归通过
- [ ] 总测试数: 418 → 441+ (新增 23+ 测试)
- [ ] 工作树 clean
- [ ] 所有 commit 消息符合 conventional commits 规范

---

## 执行选择

**Plan complete and saved to `docs/superpowers/plans/2026-07-18-sp6-roadmap-master-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 我为每个 Phase 派一个 fresh subagent，逐 task 实施 + 两阶段 review（spec compliance + code quality）。Fast iteration，每个 Phase 独立验证。

**2. Inline Execution** - 在当前 session 内按 Phase 顺序实施，每 Phase 完成后 checkpoint review。Context 连续但占用主上下文。

**Which approach?**

> 注: 5 个 sub-plan 文档由并行 subagent 同时生成（基于 §spec 的对应章节），生成后我会汇总通知用户审查。
