# M5b Web UI 设计文档

## 1. 背景与目标

M5a 已完成 FastAPI 后端 API 层（团队注册、任务提交、SSE 实时推送、审批续跑、用量统计），167 测试通过。

M5b 的目标：为 AgentTeam 构建 React 前端控制台，消费已有 API，提供可视化操作界面：
- 团队注册 / 查看 / 删除
- 任务提交 / Run 列表 / 状态监控
- 实时执行轨迹（SSE 事件流可视化）
- 审批交互（通过 / 拒绝中断的 run）
- Dashboard 用量统计

## 2. 技术栈

| 层面 | 选型 | 理由 |
|------|------|------|
| 框架 | React 18 + TypeScript | 生态成熟，类型安全 |
| 构建 | Vite 5 | 快速 HMR，配置简单 |
| UI 组件库 | Ant Design 5 | 组件最全（Table/Form/Modal/Tabs/Badge/Statistic），中文文档成熟 |
| 路由 | React Router 6 | React 生态标准路由 |
| 状态管理 | React 原生 useState/useEffect | 本地工具，不引入额外状态库 |
| 图表 | @ant-design/charts | Dashboard 统计可视化 |
| SSE | 浏览器原生 EventSource API | 无需额外依赖 |

## 3. 架构与托管模型

### 3.1 项目结构

```
agentteam/              # Python 后端（已有）
  api/
    server.py           # create_app() 末尾挂载 StaticFiles(web/dist)
    routes/             # 已有 API 不变
web/                    # React 前端（新建）
  package.json
  vite.config.ts        # proxy /api → http://localhost:8000
  tsconfig.json
  index.html
  src/
    main.tsx            # 入口
    App.tsx             # 路由 + 布局（Sider + Content）
    api/
      client.ts         # fetch 封装 + 类型定义
    hooks/
      useFetch.ts       # 通用数据获取 hook
    pages/
      Dashboard.tsx
      Teams.tsx
      Runs.tsx
      RunDetail.tsx
    components/
      SSEViewer.tsx     # EventSource 实时事件流
      ApprovalDialog.tsx
      StatusBadge.tsx
      JsonView.tsx
  dist/                 # 构建产物（.gitignore）
```

### 3.2 开发模式

双进程并行：
- 终端 1: `uvicorn agentteam.api.server:create_app --factory`（端口 8000）
- 终端 2: `cd web && npm run dev`（端口 5173）
- Vite 配置 `/api` 代理到 `http://localhost:8000`
- 浏览器访问 `http://localhost:5173`，享受热更新

### 3.3 生产模式

- `cd web && npm run build` → 生成 `web/dist/`
- `create_app()` 检测 `web/dist/` 存在则 `app.mount("/", StaticFiles(directory="web/dist", html=True))`
- 单进程 `uvicorn` 同时服务 API + UI
- 访问 `http://localhost:8000`，前后端同源

### 3.4 后端改动

仅 `agentteam/api/server.py` 末尾加 ~10 行静态文件挂载逻辑：

```python
import os
from starlette.staticfiles import StaticFiles

WEB_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist")
if os.path.isdir(WEB_DIST):
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
```

API 路由零改动。

## 4. 页面设计

4 个页面，左侧导航栏（antd Layout.Sider）+ 右侧内容区（Layout.Content）布局。

### 4.1 Dashboard（`/`）

- 顶部 4 个 Statistic 卡片：总 run 数 / 总 token / 运行中 / 已完成
- 按状态分布饼图（@ant-design/charts Pie）
- 按团队分布柱状图（@ant-design/charts Column）
- 最近 10 条 run 表格（run_id 缩略 / team / task / 状态 Badge / 创建时间），点击行跳转 `/runs/:id`
- 数据来源：`GET /api/dashboard`

### 4.2 Teams（`/teams`）

- 团队列表表格：name / description / worker 数 / 操作（删除）
- "注册团队"按钮 → Modal 内含 JSON 编辑器（Textarea），粘贴 Team JSON → `POST /api/teams`
- 删除按钮（Popconfirm 确认）→ `DELETE /api/teams/{name}`
- 点击团队名展开 leader/workers 详情（antd Descriptions 组件）
- 数据来源：`GET /api/teams`、`POST /api/teams`、`DELETE /api/teams/{name}`

### 4.3 Runs（`/runs`）

- Run 列表表格：run_id 缩略 / team / task / 状态 Badge / 创建时间 / 操作（查看）
- 顶部"提交任务"按钮 → Modal：选团队（Select 下拉，从 `/api/teams` 加载）+ 输入 task（TextArea）→ `POST /api/runs`
- 状态 Badge 颜色映射：
  - `pending`：灰色（default）
  - `running`：蓝色（processing，脉冲动画）
  - `completed`：绿色（success）
  - `failed`：红色（error）
  - `interrupted`：橙色（warning）
- 点击行跳转 `/runs/:id`
- 数据来源：`GET /api/runs`、`POST /api/runs`

### 4.4 RunDetail（`/runs/:id`）— 核心页面

布局分上下两区：

**上半区**：Run 元信息（antd Descriptions）
- 字段：run_id / team_name / task / status（StatusBadge）/ created_at / updated_at / ended_at / total_tokens
- 数据来源：`GET /api/runs/{id}`

**下半区**：Tabs 两个标签页

#### Tab 1：实时轨迹（SSEViewer 组件）

- 连接 `GET /api/runs/:id/stream`（EventSource API）
- 连接状态指示器：连接中 / 已连接 / 已断开 / 已结束
- 事件按时间线垂直排列（antd Timeline 或自定义列表），每条显示：
  - 事件类型 Badge（颜色区分）+ 时间戳 + payload（JSON 可折叠展开）
- 事件类型颜色映射：
  - `run_start`：蓝色
  - `run_end`：绿色
  - `error`：红色
  - `step_started` / `worker_started`：青色
  - `step_completed` / `worker_completed`：绿色
  - `run_interrupted`：橙色
  - 其他：默认色
- `run_end` / `error` 事件后自动 `eventSource.close()`，状态指示器变为"已结束"
- 页面离开（组件卸载）时 `eventSource.close()` 清理
- `run_interrupted` 事件后显示提示："等待审批..."

#### Tab 2：审批记录

- 表格列出所有审批请求（`GET /api/runs/{id}/approvals`）：
  - step_label / requested_at / decision / decided_at / reason
- 当 run 状态为 `interrupted` 时，顶部显示审批操作栏：
  - "通过"按钮（绿色）→ `POST /api/runs/{id}/approve` `{approved: true}`
  - "拒绝"按钮（红色）→ 弹出 Modal 输入原因 → `POST /api/runs/{id}/approve` `{approved: false, reason}`
- 审批成功后刷新 run 状态，SSEViewer 自动重连继续推送
- 审批失败（400/409）→ `message.error()` 显示 detail

## 5. API 层与状态管理

### 5.1 API Client（`api/client.ts`）

轻量 fetch 封装，统一处理 JSON 解析、错误提取：

```typescript
const BASE = ""; // 同源：开发走 Vite 代理，生产走 FastAPI 静态

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}
```

TypeScript 类型定义（与后端 Pydantic 模型对齐）：

```typescript
interface Run {
  run_id: string;
  team_name: string;
  task: string;
  status: "pending" | "running" | "completed" | "failed" | "interrupted";
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  total_tokens: number;
}

interface CreateRunRequest { team_name: string; task: string; }
interface ApproveRequest { approved: boolean; reason?: string | null; }
interface Dashboard {
  total_runs: number;
  total_tokens: number;
  by_status: Record<string, number>;
  by_team: Record<string, number>;
  recent_runs: Run[];
}
interface TraceEvent {
  id: number;
  run_id: string;
  event_type: string;
  source: string;
  payload: string | null;
  created_at: string;
}
interface Approval {
  id: number;
  run_id: string;
  step_label: string;
  requested_at: string;
  decision: string | null;
  decided_at: string | null;
  reason: string | null;
}
```

### 5.2 状态管理

不引入额外状态库，用 React 原生 hooks：

- **`useFetch<T>(path)`**：通用数据获取 hook，封装 loading / error / data 状态 + 手动 `refetch()`
- Dashboard / Runs 列表：挂载时 fetch + 手动刷新按钮
- RunDetail：挂载时 fetch 元信息，SSE 独立管理实时数据
- 审批操作后调用 `refetch()` 刷新

### 5.3 SSE 处理（`SSEViewer.tsx`）

```typescript
// 核心逻辑
useEffect(() => {
  const es = new EventSource(`/api/runs/${runId}/stream`);
  setConnected(true);

  es.addEventListener("run_start", handler);
  es.addEventListener("run_end", handler);
  es.addEventListener("error", handler);
  es.addEventListener("run_interrupted", handler);
  // ... 其他事件类型

  es.onerror = () => { setConnected(false); };

  return () => { es.close(); }; // 清理
}, [runId]);
```

- 事件存入 `useState` 数组，追加渲染
- `run_end` / `error` 事件后主动 `es.close()`
- 审批成功后父组件触发 key 变化，SSEViewer 重新挂载重连

### 5.4 错误处理

- fetch 非 2xx → 提取 `detail` 字段 → antd `message.error()` 提示
- SSE 连接失败 → 状态指示器变红 + 手动重连按钮
- 审批 400（非 interrupted 状态）→ `message.error()` 显示 detail
- 审批 409（服务重启后 graph 丢失）→ `message.error()` 显示 detail

## 6. 依赖清单

### 前端（`web/package.json`）

```json
{
  "dependencies": {
    "react": "^18.3",
    "react-dom": "^18.3",
    "react-router-dom": "^6.26",
    "antd": "^5.21",
    "@ant-design/icons": "^5.5",
    "@ant-design/charts": "^2.2"
  },
  "devDependencies": {
    "@types/react": "^18.3",
    "@types/react-dom": "^18.3",
    "@vitejs/plugin-react": "^4.3",
    "typescript": "^5.6",
    "vite": "^5.4"
  }
}
```

### 后端

无新增 Python 依赖。`starlette.staticfiles` 已随 FastAPI 安装。

## 7. 测试策略

前端测试以手动验证为主，辅以少量自动化测试：

- **后端回归**：`server.py` 静态文件挂载不影响已有 167 个测试（挂载在所有 `/api` 路由之后）
- **前端构建验证**：`npm run build` 无 TypeScript 错误
- **手动验证清单**：
  1. Dashboard 数据正确渲染
  2. 注册/删除团队
  3. 提交任务 → 列表显示新 run
  4. Run 详情页 SSE 实时推送
  5. 中断 run 审批通过 → 续跑 → 完成
  6. 中断 run 审批拒绝 → 完成
  7. 生产模式 `uvicorn` 单进程服务 UI + API

## 8. 非目标

- 用户认证 / RBAC
- 前端单元测试 / E2E 测试（手动验证即可）
- 国际化（中文界面）
- 暗色主题切换
- 团队 JSON 可视化编辑器（Textarea 粘贴即可）
- WebSocket（SSE 足矣）
- 前端状态管理库（Redux/Zustand 等）
