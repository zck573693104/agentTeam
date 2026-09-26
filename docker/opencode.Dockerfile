# syntax=docker/dockerfile:1
# 执行底座：opencode server（agent loop / 工具 / 模型接入 / MCP）。
#
# 版本必须钉住 —— AgentTeam 的引擎契约只在 1.18.x 上实测过（1.18.32），
# 上游 HTTP 面按版本漂移；run 提交时有版本兼容门，主版本偏离会直接 502。
# 升级步骤：改 ARG → 起 server → 重跑 tests/harness/test_real_opencode.py。
FROM node:22-bookworm-slim

ARG OPENCODE_VERSION=1.18.32
RUN npm i -g opencode-ai@${OPENCODE_VERSION} --no-audit --no-fund

# opencode 的 postinstall 装的是 glibc 构建的平台二进制，别用 alpine（musl 跑不起来）。
# git 是硬依赖：v2 回合的 snapshot（start/end/files）靠它做工作区差分。
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 10001 opencode
USER opencode
WORKDIR /workspace

# worker 的文件操作与命令都发生在 /workspace —— 挂进目标仓库即“代码不出域”。
# 配置与数据卷里放 opencode.jsonc / auth（provider key 用 {env:VAR} 引用，
# 不要把明文密钥写进文件或提交进仓库）。
ENV XDG_CONFIG_HOME=/home/opencode/.config \
    XDG_DATA_HOME=/home/opencode/.local/share

EXPOSE 4117

ENTRYPOINT ["opencode"]
# 默认只听 loopback：compose 里它与 agentteam 共享 network namespace，
# 因此不对外暴露；要跨主机访问需显式改 --hostname 并务必配
# OPENCODE_SERVER_PASSWORD（Basic Auth，用户名固定为 opencode）。
CMD ["serve", "--port", "4117", "--hostname", "127.0.0.1", "--log-level", "INFO"]
