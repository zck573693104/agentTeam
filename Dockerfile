# syntax=docker/dockerfile:1
# AgentTeam 控制平面：FastAPI + SQLite 审计 + React 控制台（同源挂在 /，API 在 /api）。
# 执行底座 opencode server 不在本镜像内 —— 见 docker/opencode.Dockerfile。

# 控制台构建。产物 COPY 进运行镜像后由 FastAPI 静态挂载，
# 前端一律用相对路径 /api/*，因此同源部署无需任何 base 配置。
FROM node:22-alpine AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 非 root：worker 侧的文件与命令即使逃逸到应用用户，也不是 uid 0。
RUN useradd -m -u 10001 agentteam
WORKDIR /app

# 只拷运行所需：agentteam（含 presets）与 examples 都是 setuptools 打包项，
# skills/ 是 SP7 技能目录，运行时以只读方式挂载即可。
COPY pyproject.toml README.md ./
COPY agentteam ./agentteam
COPY examples ./examples
COPY skills ./skills
RUN pip install .

RUN mkdir -p /app/data && chown -R 10001:10001 /app
USER agentteam

# create_app 的默认 SQLite 路径是相对 CWD 的 data/agentteam.db，故卷挂 /app/data。
ENV AGENTTEAM_SKILLS_DIR=/app/skills \
    AGENTTEAM_OPENCODE_URL=http://127.0.0.1:4117

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/dashboard', timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["uvicorn", "agentteam.api.server:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
