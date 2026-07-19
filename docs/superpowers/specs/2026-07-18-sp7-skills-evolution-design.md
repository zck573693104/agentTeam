# SP7: Agent Skill 系统 + 自进化机制 Design Spec

**Date:** 2026-07-18
**Status:** Draft → Pending User Review
**Author:** Brainstorming session
**Implementation phases:** SP7a (Skill 系统) + SP7b (自进化)

---

## 0. 背景与目标

### 0.1 现状

AgentTeam 当前 Agent 模型(`agentteam/domain/agent.py`)支持:
- `system_prompt`: 身份描述
- `tools: list[str]`: 工具集(从 ToolRegistry 解析)
- `max_iterations` / `approval_policy`: 行为参数
- `subordinates`: 树形组织结构(supervisor)

**缺口**:
1. Agent 无法装备可复用的"行为指导"(类 superpowers 的 markdown skill 文件)
2. Agent 不能从执行历史中学习/优化自身,所有调优靠人工

### 0.2 目标

**SP7a — Skill 系统**: Agent 可装备 markdown skill 文件,skill 内容作为独立 SystemMessage 注入到 ReAct 循环中,指导 LLM 行为(不改工具集)。

**SP7b — 自进化系统**: 每次 run 终态后异步触发 4 维度进化(prompt 优化 / 参数调整 / skill 生成 / skill 推荐),版本化存储历史,支持回滚。

### 0.3 非目标

- 不做向量检索/RAG(skill 数量 < 100 时收益有限)
- 不做跨 Agent 的"集体学习"(每个 Agent 独立进化)
- 不做 web UI(本次仅 API + CLI)
- 不引入新依赖(向量数据库等)

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     API 层                                  │
│  POST /api/runs    GET /api/skills   POST /api/agents/{n}/rollback │
└────────┬───────────────┬──────────────────┬─────────────────┘
         │               │                  │
         ▼               ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│                  RunManager (现有)                           │
│  start_run → graph.invoke → _handle_invoke_result           │
│                                    │                         │
│              ┌─────────────────────┴──────────────┐         │
│              ▼                                    ▼         │
│       run_end (终态)                    EvolutionEngine.trigger │
│                                    (SP7b 异步,不阻塞响应)    │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│            TeamCompiler (现有 + SP7a 改造)                    │
│  compile(team)                                               │
│    ├─► SkillLoader.load(agent.skills) ← SP7a 从 skills/ 加载 │
│    └─► _compile_worker(agent, ..., skills=skill_contents)    │
│           └─► make_init_worker 注入 SystemMessage             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│             SP7b: EvolutionEngine (新增)                     │
│  trigger(run_id)                                             │
│    ├─► _optimize_prompt   (LLM 重写 system_prompt)           │
│    ├─► _tune_params       (统计历史 + 调整 max_iter 等)       │
│    ├─► _generate_skill    (成功 run 提炼 → auto_*.md)         │
│    └─► _select_skills     (任务匹配 → 软推荐)                 │
│  Agent.version += 1 (任一维度成功)                            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                       存储层 (SQLite)                        │
│  现有: runs / teams / library_agents / audit_events          │
│  SP7a 改: library_agents.spec_json 含 skills 字段            │
│  SP7b 新增: library_agents.version + evolution_history 表    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     文件系统                                │
│  skills/             ← SP7a 用户预置 skill 目录              │
│    code_review.md                                            │
│    error_handling.md                                         │
│    testing_strategy.md                                       │
│    auto_*.md          ← SP7b SkillGenerator 自动生成         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. SP7a: Skill 系统详细设计

### 2.1 数据模型

**Agent 加 `skills` 字段**(`agentteam/domain/agent.py`):

```python
class Agent:
    # 现有字段...
    name: str
    role: Literal["supervisor", "worker"]
    system_prompt: str
    tools: list[str]
    model: str | None
    max_iterations: int
    approval_policy: ApprovalPolicy
    subordinates: list[Agent | TeamRef]

    # SP7a 新增
    skills: list[str] = []  # skill 名列表,默认空(向后兼容)

    # SP7b 新增(见 §3.4)
    version: int = 1  # 进化代数,默认 1
```

**序列化**: `library_agents.spec_json` 自动包含 `skills` 字段(已有 to_dict / from_dict 机制)。`version` 字段 SP7b 阶段添加到序列化。

### 2.2 SkillLoader 组件

**新增文件**: `agentteam/runtime/skills.py`

```python
from pathlib import Path


class SkillLoader:
    """从 skills/ 目录加载 markdown skill 文件,内存缓存避免重读。

    skill 名 = 文件名 stem(如 skills/code_review.md → "code_review")。
    若 skill 不存在,load 时抛 KeyError(编译期 fail-fast)。
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir
        self._cache: dict[str, str] = {}
        self._scanned = False

    def _scan(self) -> None:
        """惰性扫描 skills_dir,构建 name → content 缓存。"""
        if self._scanned or self._skills_dir is None:
            self._scanned = True
            return
        for path in self._skills_dir.glob("*.md"):
            self._cache[path.stem] = path.read_text(encoding="utf-8")
        self._scanned = True

    def load(self, names: list[str]) -> dict[str, str]:
        """按名批量加载,返回 {name: content}。缺失抛 KeyError(列出缺失项)。"""
        if not names:
            return {}
        self._scan()
        missing = [n for n in names if n not in self._cache]
        if missing:
            raise KeyError(f"Skills not found: {missing}")
        return {n: self._cache[n] for n in names}

    def list_available(self) -> list[str]:
        """列出所有可用 skill 名(供 API / CLI 查询)。"""
        self._scan()
        return sorted(self._cache.keys())

    def reload(self) -> None:
        """清缓存重扫(支持 skills/ 目录热更新,SP7b SkillGenerator 写入后调用)。"""
        self._cache.clear()
        self._scanned = False
        self._scan()
```

### 2.3 TeamCompiler 集成

**修改**: `agentteam/runtime/graph.py`

```python
class TeamCompiler:
    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        library: AgentLibrary | None = None,
        run_manager=None,  # P4 已加
        skill_loader: SkillLoader | None = None,  # SP7a 新增
    ):
        ...
        self._skill_loader = skill_loader or SkillLoader()

    def _compile_worker(self, agent: Agent, default_model, trace_writer, audit_repo):
        skills = self._skill_loader.load(agent.skills)  # 加载该 agent 的 skills
        llm = self._mp.get_llm(agent.model or default_model)
        tools = self._tr.get_tools(agent.tools) if agent.tools else []
        return make_worker_node(
            agent, llm, tools, trace_writer, audit_repo,
            run_manager=self._run_manager,
            skills=skills,  # 透传
        )

    def _compile_supervisor(self, agent, ...):
        # 同样支持 supervisor 装备 skill(用于指导 plan/review)
        skills = self._skill_loader.load(agent.skills)
        ...
```

### 2.4 Skill 注入流程

**修改**: `agentteam/runtime/nodes.py` — `make_init_worker`

```python
def make_init_worker(agent: Agent, trace_writer=None, skills: dict[str, str] | None = None):
    def init_worker(state: dict) -> dict:
        # 原有初始化
        react_messages = [
            SystemMessage(content=agent.system_prompt),
            HumanMessage(content=state.get("task", "")),
        ]

        # SP7a: 把 skills 包装为 SystemMessage,插入到 react_messages[1]
        # (system_prompt 之后、task 之前)
        if skills:
            skill_text = "\n\n".join(
                f'<skill name="{name}">\n{content}\n</skill>'
                for name, content in skills.items()
            )
            react_messages.insert(1, SystemMessage(content=skill_text))

        return {
            "react_messages": react_messages,
            "tool_calls": [],
            "iteration": 0,
            "final_answer": "",
            # dag 模式字段保持不变
        }
    return init_worker
```

**react_messages 结构对比**:

```
【改造前】
┌────────────────────────────────┐
│ SystemMessage(system_prompt)   │  身份描述
├────────────────────────────────┤
│ HumanMessage(task)             │  用户任务
├────────────────────────────────┤
│ AIMessage / ToolMessage ...    │  ReAct 循环
└────────────────────────────────┘

【改造后(有 skills 时)】
┌────────────────────────────────┐
│ SystemMessage(system_prompt)   │  身份描述(不变)
├────────────────────────────────┤
│ SystemMessage(skill_text)      │  ← SP7a 新增:行为指导
│   <skill name="code_review">   │
│   ...                          │
│   </skill>                     │
├────────────────────────────────┤
│ HumanMessage(task)             │  用户任务
├────────────────────────────────┤
│ AIMessage / ToolMessage ...    │  ReAct 循环
└────────────────────────────────┘
```

**设计理由**:
- skill 放 system_prompt 之后:LLM 先建立身份,再接收行为指导
- skill 放 task 之前:LLM 处理任务时已"知道"该用什么模式
- 用 `<skill>` 标签包裹:便于 LLM 区分多个 skill 边界
- 不混入 system_prompt:身份描述与行为指导解耦,便于独立演进

### 2.5 make_worker_node / make_worker_subgraph 透传

**修改**: `agentteam/runtime/nodes.py`

```python
def make_worker_subgraph(
    agent, llm, tools, trace_writer=None, audit_repo=None,
    run_manager=None,
    skills: dict[str, str] | None = None,  # SP7a 新增
):
    ...
    sg.add_node("init_worker", make_init_worker(agent, trace_writer, skills=skills))
    ...

def make_worker_node(
    agent, llm, tools, trace_writer=None, audit_repo=None,
    run_manager=None,
    skills: dict[str, str] | None = None,  # SP7a 新增
):
    subgraph = make_worker_subgraph(
        agent, llm, tools, trace_writer, audit_repo,
        run_manager=run_manager, skills=skills,
    )
    # 现有 _ACCUMULATOR_KEYS / _RETURN_KEYS 逻辑保持不变
    ...
```

### 2.6 API / CLI

**新增 API**: `agentteam/api/routes/skills.py`

```python
def skills_router(skill_loader):
    router = APIRouter(prefix="/api/skills", tags=["skills"])

    @router.get("/")
    def list_skills():
        return {"skills": skill_loader.list_available()}

    @router.get("/{skill_name}")
    def get_skill(skill_name: str):
        contents = skill_loader.load([skill_name])
        if skill_name not in contents:
            raise HTTPException(404, f"Skill '{skill_name}' not found")
        return {"name": skill_name, "content": contents[skill_name]}

    return router
```

**server.py 改造**:

```python
def create_app(db_path, model_provider, tool_registry, skills_dir: Path | None = None):
    ...
    skill_loader = SkillLoader(skills_dir)
    compiler_factory = lambda: TeamCompiler(
        model_provider, tool_registry, library=lib,
        run_manager=run_manager, skill_loader=skill_loader,
    )
    app.include_router(skills_router(skill_loader))
```

**CLI 新增**:
- `agentteam list-skills`: 列出 skills/ 目录中所有 skill
- `agentteam validate-team`(改造): 检查 team spec 中引用的 skill 是否存在

### 2.7 预置 skill 示例

**新增目录**: `skills/`(项目根)

3 个示例 skill(简洁实用,作为文档与测试用例):

- `skills/code_review.md`: 代码审查 skill(优先检查安全/正确性/可读性,使用 git diff)
- `skills/error_handling.md`: 错误处理 skill(异常分类、重试策略、用户消息)
- `skills/testing_strategy.md`: 测试策略 skill(TDD、边界用例、覆盖率)

### 2.8 测试策略

**单元测试** `tests/runtime/test_skills.py`:
- SkillLoader 空目录返回空 list
- SkillLoader 扫描 .md 文件,stem 作为 name
- SkillLoader.load 空名列表返回空 dict
- SkillLoader.load 缺失 skill 抛 KeyError(含缺失名)
- SkillLoader.load 命中返回 {name: content}
- SkillLoader.reload 清缓存重扫
- SkillLoader.list_available 排序返回

**集成测试** `tests/runtime/test_plan_dag.py` 追加:
- Agent with skills 字段构造
- TeamCompiler + SkillLoader E2E(skill 注入到 react_messages)
- make_init_worker 多 skill 注入顺序正确
- 无 skills 时行为与改造前一致(向后兼容)

**API 测试** `tests/api/test_api_skills.py`:
- GET /api/skills 返回 skill 列表
- GET /api/skills/{name} 返回 skill 内容
- GET /api/skills/nonexistent 返回 404
- 空目录返回 {"skills": []}

**回归测试**:
- 全部现有测试 PASS(Agent.skills 默认 [],无 skill 注入)

---

## 3. SP7b: 自进化系统详细设计

### 3.1 EvolutionEngine 主控

**新增文件**: `agentteam/runtime/evolution.py`

```python
import threading
import time
from dataclasses import dataclass


@dataclass
class EvolutionResult:
    success: bool
    dimension: str
    reason: str
    error: str | None = None


class EvolutionEngine:
    """Run 终态后异步触发,协调 4 个进化维度。

    设计原则:
    - 异步: 不阻塞 RunManager 的 API 响应
    - 隔离: 4 维度独立 LLM 调用 + 独立写 history,互不影响
    - 失败保护: 任一维度失败仅记 error,不影响其他维度 / run 结果
    - 防抖: 同一 agent 短时间内(5 分钟)只触发一次,避免连续 run 雪崩
    - 版本原子性: 一次 trigger 内 4 维度全部尝试后,Agent.version += 1
    """

    DEBOUNCE_SECONDS = 300  # 5 分钟

    def __init__(
        self,
        model_provider: ModelProvider,
        agent_library: AgentLibrary,
        evolution_repo: EvolutionRepo,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        skill_loader: SkillLoader | None = None,
        skills_dir: Path | None = None,
    ):
        self._mp = model_provider
        self._lib = agent_library
        self._evo_repo = evolution_repo
        self._run_repo = run_repo
        self._audit = audit_repo
        self._skill_loader = skill_loader
        self._skills_dir = skills_dir
        self._last_trigger: dict[str, float] = {}
        self._lock = threading.Lock()

    def trigger(self, run_id: str) -> None:
        """RunManager 在 run 终态后异步调用。"""
        run = self._run_repo.get_run(run_id)
        if run is None:
            return
        agents = self._collect_agents_from_trace(run_id)
        if not agents:
            return
        for agent_name in agents:
            self._evolve_agent(agent_name, run_id)

    def _collect_agents_from_trace(self, run_id: str) -> list[str]:
        """从 audit_events 表扫描 worker_start / leader_plan 事件,
        提取涉及的 agent 名(去重)。失败返回空列表。"""

    def _evolve_agent(self, agent_name: str, run_id: str) -> None:
        # 防抖
        now = time.time()
        with self._lock:
            last = self._last_trigger.get(agent_name, 0)
            if now - last < self.DEBOUNCE_SECONDS:
                return
            self._last_trigger[agent_name] = now

        agent = self._lib.get(agent_name)
        if agent is None:
            return

        trace = self._audit.list_events(run_id)
        old_version = agent.version or 1

        # 4 维度顺序执行
        results = []
        results.append(self._optimize_prompt(agent, trace, run_id))
        results.append(self._tune_params(agent, trace, run_id))
        results.append(self._generate_skill(agent, trace, run_id))
        results.append(self._select_skills(agent, trace, run_id))

        if any(r.success for r in results):
            new_version = old_version + 1
            self._lib.update_version(agent_name, new_version)
```

### 3.2 4 个进化维度

#### 维度 1: PromptOptimizer

```python
def _optimize_prompt(self, agent, trace, run_id) -> EvolutionResult:
    """分析 trace + LLM 重写 system_prompt。"""
    try:
        old_prompt = agent.system_prompt
        trace_summary = self._summarize_trace(trace)

        llm = self._mp.get_llm(None)
        response = llm.invoke([
            SystemMessage(content=PROMPT_OPTIMIZER_INSTRUCTION),
            HumanMessage(content=f"""
当前 system_prompt:
{old_prompt}

本次 run trace:
{trace_summary}

请基于 trace 分析 prompt 是否需要优化。若需优化给出新版本,若已合理则原样返回。
"""),
        ])

        new_prompt = self._parse_prompt_from_response(response.content)
        if new_prompt == old_prompt:
            return EvolutionResult(True, "prompt", "no change needed")

        self._evo_repo.add_record(
            agent_name=agent.name, version=agent.version,
            dimension="prompt",
            before_value=old_prompt, after_value=new_prompt,
            diff=self._compute_diff(old_prompt, new_prompt),
            reason=response.content, run_id=run_id, success=True,
        )
        self._lib.update_prompt(agent.name, new_prompt)
        return EvolutionResult(True, "prompt", "prompt updated")
    except Exception as e:
        self._evo_repo.add_record(
            agent_name=agent.name, version=agent.version,
            dimension="prompt", before_value="", after_value="",
            diff="", reason=f"error: {e}", run_id=run_id,
            success=False, error=str(e),
        )
        return EvolutionResult(False, "prompt", "error", str(e))
```

#### 维度 2: ParamTuner

```python
def _tune_params(self, agent, trace, run_id) -> EvolutionResult:
    """统计历史 N 次 run + LLM 建议参数调整。"""
    try:
        history = self._evo_repo.list_recent_runs(agent.name, limit=5)
        stats = self._compute_stats(history)

        llm = self._mp.get_llm(None)
        response = llm.invoke([
            SystemMessage(content=PARAM_TUNER_INSTRUCTION),
            HumanMessage(content=f"""
当前参数: max_iterations={agent.max_iterations}, approval_policy={agent.approval_policy}
最近 5 次统计: {stats}
建议调整(只给必要改动,否则原样返回)?
"""),
        ])

        new_params = self._parse_params(response.content)
        # 边界保护: max_iterations 限制 [1, 20]
        new_params["max_iterations"] = max(1, min(20,
            new_params.get("max_iterations", agent.max_iterations)))

        if new_params == {"max_iterations": agent.max_iterations,
                          "approval_policy": agent.approval_policy}:
            return EvolutionResult(True, "params", "no change needed")

        old_params = {"max_iterations": agent.max_iterations,
                      "approval_policy": agent.approval_policy}
        self._evo_repo.add_record(
            agent_name=agent.name, version=agent.version,
            dimension="params",
            before_value=json.dumps(old_params),
            after_value=json.dumps(new_params),
            diff="", reason=response.content, run_id=run_id, success=True,
        )
        self._lib.update_params(agent.name, new_params)
        return EvolutionResult(True, "params", "params tuned")
    except Exception as e:
        # 同上 error 处理
        ...
```

#### 维度 3: SkillGenerator

```python
def _generate_skill(self, agent, trace, run_id) -> EvolutionResult:
    """仅在 run 成功时尝试提炼 skill。"""
    try:
        if not self._is_successful_run(trace):
            return EvolutionResult(True, "skill_gen", "run failed, skip")

        if self._skills_dir is None:
            return EvolutionResult(True, "skill_gen", "no skills_dir configured")

        llm = self._mp.get_llm(None)
        response = llm.invoke([
            SystemMessage(content=SKILL_GENERATOR_INSTRUCTION),
            HumanMessage(content=f"""
Agent: {agent.name} (role={agent.role})
Task: {self._extract_task(trace)}
Tool calls: {self._extract_tool_calls(trace)}
Final answer: {self._extract_final_answer(trace)}

从本次成功执行中提炼可复用的 skill 模式。
若无可复用模式则返回 SKIP。否则返回 markdown skill 内容 + 建议 skill 名。
命名规则: auto_<pattern>.md(避免覆盖用户预置 skill)。
"""),
        ])

        if response.content.strip() == "SKIP":
            return EvolutionResult(True, "skill_gen", "no reusable pattern")

        skill_name, skill_md = self._parse_skill_response(response.content)
        # 若 auto_X.md 已存在,附加版本号
        skill_path = self._skills_dir / f"{skill_name}.md"
        if skill_path.exists():
            version = 2
            while (self._skills_dir / f"{skill_name}_v{version}.md").exists():
                version += 1
            skill_path = self._skills_dir / f"{skill_name}_v{version}.md"

        skill_path.write_text(skill_md, encoding="utf-8")

        # 通知 SkillLoader 重载缓存
        if self._skill_loader:
            self._skill_loader.reload()

        self._evo_repo.add_record(
            agent_name=agent.name, version=agent.version,
            dimension="skill_gen", before_value="",
            after_value=str(skill_path),
            diff="", reason=f"Generated skill: {skill_name}",
            run_id=run_id, success=True,
        )
        return EvolutionResult(True, "skill_gen", f"generated {skill_name}")
    except Exception as e:
        ...
```

#### 维度 4: SkillSelector(软推荐)

```python
def _select_skills(self, agent, trace, run_id) -> EvolutionResult:
    """根据任务推荐 skill,临时装备(不改 Agent.skills)。

    此维度产生"建议",实际装备由用户 review history 后手动 apply。
    """
    try:
        available = self._skill_loader.list_available() if self._skill_loader else []
        candidates = [s for s in available if s not in agent.skills]
        if not candidates:
            return EvolutionResult(True, "skill_select", "no new skills to recommend")

        task = self._extract_task(trace)
        llm = self._mp.get_llm(None)
        response = llm.invoke([
            SystemMessage(content=SKILL_SELECTOR_INSTRUCTION),
            HumanMessage(content=f"""
Agent: {agent.name}, role={agent.role}
Task: {task}
Already equipped skills: {agent.skills}
Candidate skills: {candidates}

推荐本次任务应装备的 skill(可多选,空则不推荐)。返回 skill 名列表。
"""),
        ])

        recommended = self._parse_skill_list(response.content)
        if not recommended:
            return EvolutionResult(True, "skill_select", "no recommendation")

        # 写入 history(标记为"建议"),不直接改 Agent.skills
        self._evo_repo.add_record(
            agent_name=agent.name, version=agent.version,
            dimension="skill_select",
            before_value=json.dumps(agent.skills),
            after_value=json.dumps(recommended),
            diff="", reason=f"Recommended for tasks like: {task[:100]}",
            run_id=run_id, success=True,
        )
        return EvolutionResult(True, "skill_select", f"recommended {recommended}")
    except Exception as e:
        ...
```

### 3.3 触发机制

**修改**: `agentteam/api/run_manager.py`

```python
class RunManager:
    def __init__(
        self, run_repo, audit_repo, event_bus,
        evolution_engine=None,  # SP7b 新增
    ):
        ...
        self._evolution = evolution_engine

    def _handle_invoke_result(self, run_id, graph, config):
        # 现有逻辑:检查 graph state,update_status
        ...
        # SP7b: 终态(completed/failed)后异步触发进化
        if self._evolution is not None and final_status in ("completed", "failed"):
            threading.Thread(
                target=self._evolution.trigger,
                args=(run_id,),
                daemon=True,
            ).start()

    def _handle_error(self, run_id, error: BaseException):
        # 现有逻辑:区分 RunCancelledError vs 其他
        ...
        # SP7b: cancelled 不触发进化(用户主动取消,数据可能不完整)
        # failed 触发进化
        if self._evolution is not None and not isinstance(error, RunCancelledError):
            threading.Thread(
                target=self._evolution.trigger,
                args=(run_id,),
                daemon=True,
            ).start()
```

### 3.4 数据模型

**新增表**: `evolution_history`

```sql
CREATE TABLE IF NOT EXISTS evolution_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name   TEXT NOT NULL,
    version      INTEGER NOT NULL,        -- Agent 在该次进化后的 version
    dimension    TEXT NOT NULL,           -- 'prompt' | 'params' | 'skill_gen' | 'skill_select' | 'rollback'
    before_value TEXT,                    -- JSON 序列化
    after_value  TEXT,                    -- JSON 序列化
    diff         TEXT,                    -- 文本 diff(prompt 维度)或 ""
    reason       TEXT,                    -- LLM 分析理由
    run_id       TEXT,                    -- 触发此次进化的 run(rollback 时为 NULL)
    success      BOOLEAN NOT NULL,        -- False 表示该维度失败
    error        TEXT,                    -- 失败时的错误信息
    timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_evo_agent ON evolution_history(agent_name, version);
```

**library_agents 表加 version 字段**:

```sql
ALTER TABLE library_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
```

**AgentLibrary 新增方法**(SP7b):

```python
class AgentLibrary:
    # 现有方法(get/list_all/upsert/delete)...

    def update_version(self, name: str, version: int) -> None:
        """更新 Agent 的 version 字段。"""

    def update_prompt(self, name: str, new_prompt: str) -> None:
        """更新 Agent 的 system_prompt。"""

    def update_params(self, name: str, params: dict) -> None:
        """更新 Agent 的 max_iterations / approval_policy 等参数。"""
```

这三个方法封装了"读取 → 修改 → upsert"流程,供 EvolutionEngine 调用。

### 3.5 EvolutionRepo

**新增文件**: `agentteam/storage/evolution.py`

```python
class EvolutionRepo:
    def __init__(self, conn, lock=None):
        self._conn = conn
        self._lock = lock or threading.Lock()

    def add_record(self, agent_name, version, dimension,
                   before_value, after_value, diff, reason,
                   run_id, success, error=None) -> int:
        """插入一条 history 记录,返回 id。"""

    def list_history(self, agent_name: str, limit: int = 20) -> list[dict]:
        """按 timestamp 倒序返回该 agent 的 history。"""

    def get_version_snapshot(self, agent_name: str, version: int) -> list[dict]:
        """取指定 version 的所有 history 记录(可能多条,因 4 维度)。"""

    def list_recent_runs(self, agent_name: str, limit: int = 5) -> list[dict]:
        """取该 agent 最近 N 次成功的进化记录(用于 ParamTuner 统计)。"""
```

### 3.6 回滚 API

**新增**: `agentteam/api/routes/evolution.py`

```python
def evolution_router(evolution_repo, agent_library):
    router = APIRouter(prefix="/api/agents", tags=["evolution"])

    @router.get("/{agent_name}/history")
    def list_history(agent_name: str, limit: int = 20):
        return {"history": evolution_repo.list_history(agent_name, limit)}

    @router.get("/{agent_name}/versions/{version}")
    def get_version(agent_name: str, version: int):
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(404, f"Version {version} not found")
        return {"version": version, "records": records}

    @router.post("/{agent_name}/rollback")
    def rollback_agent(agent_name: str, version: int):
        # 1. 取目标 version 的所有 history 记录
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(404, f"Version {version} not found")

        # 2. 把 before_value 应用回 Agent
        agent = agent_library.get(agent_name)
        if agent is None:
            raise HTTPException(404, f"Agent '{agent_name}' not found")

        for record in records:
            if record["dimension"] == "prompt":
                agent.system_prompt = record["before_value"]
            elif record["dimension"] == "params":
                params = json.loads(record["before_value"])
                agent.max_iterations = params["max_iterations"]
                agent.approval_policy = params["approval_policy"]
            # skill_gen / skill_select 不回滚(已写入文件的 skill 保留)

        # 3. 写新 history 记录(类型: rollback)
        new_version = agent.version + 1
        evolution_repo.add_record(
            agent_name=agent_name, version=new_version, dimension="rollback",
            before_value=f"v{agent.version}", after_value=f"v{version}",
            diff="", reason=f"User rolled back to v{version}",
            run_id=None, success=True,
        )
        agent.version = new_version
        agent_library.update(agent)
        return {"ok": True, "new_version": new_version}

    return router
```

### 3.7 LLM 指令模板

4 个维度的 SystemMessage 指令模板放在 `agentteam/runtime/evolution_prompts.py`:

- `PROMPT_OPTIMIZER_INSTRUCTION`: 分析执行 trace,识别 prompt 不足(模糊/缺失约束/与工具不匹配),给出改进版
- `PARAM_TUNER_INSTRUCTION`: 基于统计指标(迭代次数/超时率/拒绝率)建议参数调整
- `SKILL_GENERATOR_INSTRUCTION`: 从成功执行中提炼可复用模式,生成 markdown skill
- `SKILL_SELECTOR_INSTRUCTION`: 根据任务描述从 skill 库推荐相关 skill

### 3.8 关键设计权衡

| 决策 | 选择 | 理由 |
|------|------|------|
| 4 维度执行方式 | 顺序 | 避免 LLM 并发抢资源;后维度可看前维度结果 |
| SkillSelector 是否自动 apply | 软推荐 | 不污染 Agent 配置;用户 review 后手动 apply |
| SkillGenerator 是否覆盖已有 skill | 不覆盖 | auto_*.md 与用户预置隔离;已存在则附加 _vN |
| 进化失败处理 | 独立记录 | 4 维度任一失败仅记 error,不影响其他维度 |
| version 递增时机 | 任一维度成功 | version 是进化代数,便于回滚定位 |
| 触发方式 | 异步 daemon thread | 不阻塞 API 响应;失败不影响 run 结果 |
| 防抖窗口 | 5 分钟 | 避免连续 run 雪崩触发 LLM 调用 |
| Cancelled run 是否触发 | 不触发 | 用户主动取消,数据可能不完整 |

### 3.9 文件结构

```
agentteam/
  runtime/
    evolution.py            ← 新增: EvolutionEngine + 4 维度
    evolution_prompts.py    ← 新增: LLM 指令模板
    skills.py               ← SP7a 已新增
    graph.py                ← SP7a 改: TeamCompiler 加 skill_loader
    nodes.py                ← SP7a 改: make_init_worker 加 skills 参数
  storage/
    evolution.py            ← 新增: EvolutionRepo
    db.py                   ← 改: create_table 加 evolution_history 表
    library.py              ← 改: Agent 加 version 字段
  api/
    routes/
      evolution.py          ← 新增: rollback + history endpoint
      skills.py             ← SP7a 新增
    run_manager.py          ← 改: _handle_invoke_result/_handle_error 触发 evolution
  server.py                 ← 改: create_app 创建 EvolutionRepo + EvolutionEngine

skills/                     ← SP7a 新增: 用户预置 skill 目录
  code_review.md
  error_handling.md
  testing_strategy.md
  auto_*.md                 ← SP7b SkillGenerator 自动生成

tests/
  runtime/
    test_skills.py          ← SP7a 新增
    test_evolution.py       ← SP7b 新增: 4 维度单元测试
  storage/
    test_evolution_repo.py  ← SP7b 新增
  api/
    test_api_skills.py      ← SP7a 新增
    test_api_evolution.py   ← SP7b 新增: rollback + history endpoint
```

### 3.10 测试策略

**单元测试** `tests/runtime/test_evolution.py`:
- EvolutionEngine.trigger 防抖(5 分钟内同 agent 不重复)
- _optimize_prompt: LLM 返回相同 prompt 时不写 history
- _optimize_prompt: LLM 返回新 prompt 时写 history + 更新 Agent
- _optimize_prompt: LLM 调用失败时写 error + 不影响其他维度
- _tune_params: 边界保护(max_iterations [1, 20])
- _tune_params: 统计历史为空时跳过
- _generate_skill: run 失败时跳过
- _generate_skill: 成功 run + LLM 返回 SKIP 时跳过
- _generate_skill: 生成 auto_*.md + 通知 SkillLoader.reload
- _generate_skill: 已存在 auto_X.md 时附加 _v2
- _select_skills: 没有候选 skill 时跳过
- _select_skills: 推荐写入 history,不直接改 Agent.skills
- version 递增逻辑(任一维度成功才递增)

**存储测试** `tests/storage/test_evolution_repo.py`:
- add_record 返回 id
- list_history 按 timestamp 倒序
- get_version_snapshot 返回指定 version 的所有记录
- list_recent_runs 返回最近 N 次成功记录

**API 测试** `tests/api/test_api_evolution.py`:
- GET /api/agents/{name}/history 返回 history 列表
- GET /api/agents/{name}/versions/{v} 返回 version 快照
- GET 不存在的 agent/version 返回 404
- POST /api/agents/{name}/rollback?version=N 成功回滚
- POST rollback 不存在的 version 返回 404
- POST rollback 后 Agent.version 递增
- POST rollback 不影响已生成的 skill 文件

**集成测试**:
- Run 终态后异步触发 evolution(用 fake LLM + blocking 验证)
- Cancelled run 不触发 evolution
- 4 维度失败时不互相影响

**回归测试**:
- 全部现有测试 PASS(EvolutionEngine 默认 None,不触发)

---

## 4. 实施阶段拆分

### SP7a: Skill 系统(本次实施)
1. Agent 加 skills 字段 + 序列化
2. SkillLoader 组件
3. TeamCompiler 集成
4. make_init_worker 注入
5. API + CLI
6. 预置 skill 示例
7. 测试

### SP7b: 自进化系统(下次实施)
1. evolution_history 表 + EvolutionRepo
2. Agent.version 字段
3. EvolutionEngine + 4 维度
4. LLM 指令模板
5. RunManager 触发机制
6. 回滚 API
7. 测试

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 进化方向错误(prompt 越改越差) | 版本化 history + 回滚 API;防抖避免快速漂移 |
| LLM 调用成本(每次 run 后 4 次) | 防抖 5 分钟;失败保护;后期可加"成功率阈值"跳过进化 |
| SkillGenerator 生成低质量 skill | auto_*.md 命名隔离;不直接装备;SkillSelector 软推荐 |
| 异步进化失败不可见 | evolution_history 记录 success/error;API 可查询 |
| 并发触发同一 agent 进化 | _lock + 防抖 dict 保护 |
| Skill 文件被外部修改 | SkillLoader.reload() 支持热更新;编译期 fail-fast 缺失 skill |

---

## 6. 成功标准

**SP7a**:
- Agent 可装备 skill,skill 内容作为 SystemMessage 注入 react_messages
- 现有测试全部通过(向后兼容)
- 新增测试覆盖 SkillLoader / 注入流程 / API

**SP7b**:
- Run 终态后异步触发 4 维度进化
- evolution_history 表记录所有变更
- 回滚 API 可恢复到任意 version
- 失败保护:任一维度失败不影响其他维度 / run 结果
- 现有测试全部通过(EvolutionEngine 默认 None 时不触发)
