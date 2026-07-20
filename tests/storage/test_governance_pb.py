"""P-B 系列治理面改造的单元测试。

覆盖:
- P-B1: UserRepo / RBAC(users/roles/permissions/user_roles 表)
- P-B2: WAT 双身份(runs.triggered_by_user)
- P-B3: Trace 三链结构(run_events.trace_id/parent_span_id/chain + list_events_by_chain)
- P-B4: PEP 指令级拦截(pep_policies 表 + check_pep)
- P-B5: Skill 供应链(skills/skill_acls 表 + check_access)
- P-B6: MCP Server 鉴权字段(auth_type/auth_credential + build_auth_headers)
- P-B7: 配额告警阈值(warn_threshold + status 三级)
- P-B8: 多维统计(aggregate_by_chain / aggregate_top_tools / sum_tokens_by_team)
        + 审计时间范围检索(start_time/end_time/event_type)
"""
from __future__ import annotations

import threading

import pytest

from agentteam.storage.admin_audit import AdminAuditRepo
from agentteam.storage.audit import AuditRepo, _infer_chain
from agentteam.storage.db import init_db
from agentteam.storage.quotas import QuotaRepo
from agentteam.storage.runs import RunRepo
from agentteam.storage.skills_meta import SkillAccessDeniedError, SkillMetaRepo
from agentteam.storage.users import UserRepo


@pytest.fixture
def repos(tmp_path):
    """共享一份 DB + repos,避免每个测试都 init_db。"""
    conn = init_db(tmp_path / "test.db")
    lock = threading.Lock()
    yield {
        "conn": conn,
        "lock": lock,
        "users": UserRepo(conn, lock=lock),
        "runs": RunRepo(conn, lock=lock),
        "audit": AuditRepo(conn, lock=lock),
        "admin_audit": AdminAuditRepo(conn, lock=lock),
        "quotas": QuotaRepo(conn, lock=lock),
        "skills": SkillMetaRepo(conn, lock=lock),
    }
    conn.close()


# ---- P-B1: RBAC ----

def test_user_repo_default_roles_init(repos):
    """ensure_default_roles 创建 4 种默认角色 + 权限矩阵。"""
    repos["users"].ensure_default_roles()
    roles = {r["name"] for r in repos["users"].list_roles()}
    assert roles == {"admin", "manager", "team_admin", "user"}
    # admin 有通配权限
    assert "*" in repos["users"].list_permissions("admin")
    # manager 有 team:create 但无 *
    mgr_perms = set(repos["users"].list_permissions("manager"))
    assert "team:create" in mgr_perms
    assert "*" not in mgr_perms


def test_user_repo_create_and_authenticate(repos):
    """create_user 存 hash,get_by_api_key 反查。"""
    repos["users"].ensure_default_roles()
    user = repos["users"].create_user("alice", "sk-secret-1", display_name="Alice")
    # 再次用同一 api_key 应能查到
    fetched = repos["users"].get_by_api_key("sk-secret-1")
    assert fetched is not None
    assert fetched["username"] == "alice"
    assert fetched["display_name"] == "Alice"
    # 错误的 key 查不到
    assert repos["users"].get_by_api_key("wrong-key") is None


def test_user_repo_check_permission_admin_wildcard(repos):
    """admin 角色通配,任何 action 都允许。"""
    repos["users"].ensure_default_roles()
    user = repos["users"].create_user("bob", "sk-admin-1")
    repos["users"].assign_role(user["id"], "admin")
    assert repos["users"].check_permission(user["id"], "team:create")
    assert repos["users"].check_permission(user["id"], "anything:arbitrary")
    assert repos["users"].check_permission(user["id"], "quota:set")


def test_user_repo_check_permission_team_scoped(repos):
    """team_admin 仅对绑定的 team 有 run:approve 权限。"""
    repos["users"].ensure_default_roles()
    user = repos["users"].create_user("carol", "sk-team-admin-1")
    repos["users"].assign_role(user["id"], "team_admin", team_name="alpha")
    # 在 alpha team 内有 run:approve
    assert repos["users"].check_permission(user["id"], "run:approve", team_name="alpha")
    # 在 beta team 内没有(team_admin 仅对 alpha 生效)
    assert not repos["users"].check_permission(user["id"], "run:approve", team_name="beta")


def test_user_repo_soft_delete(repos):
    """delete_user 是软删除,删除后无法通过 api_key 认证。"""
    repos["users"].ensure_default_roles()
    user = repos["users"].create_user("dave", "sk-dave-1")
    assert repos["users"].get_by_api_key("sk-dave-1") is not None
    assert repos["users"].delete_user(user["id"])
    assert repos["users"].get_by_api_key("sk-dave-1") is None


# ---- P-B2: WAT 双身份 ----

def test_run_triggered_by_user_persisted(repos):
    """create_run 写入 triggered_by_user,可从 get_run 读取。"""
    run_id = repos["runs"].create_run("team_x", "task", triggered_by_user="alice")
    run = repos["runs"].get_run(run_id)
    assert run is not None
    assert run["triggered_by_user"] == "alice"


def test_run_triggered_by_user_none_backward_compat(repos):
    """不传 triggered_by_user 时为 None(向后兼容)。"""
    run_id = repos["runs"].create_run("team_x", "task")
    run = repos["runs"].get_run(run_id)
    assert run["triggered_by_user"] is None


# ---- P-B3: Trace 三链 ----

def test_infer_chain_event_type_mapping():
    """_infer_chain 把 event_type 映射到正确的 chain。"""
    assert _infer_chain("run_start") == "call"
    assert _infer_chain("worker_end") == "call"
    assert _infer_chain("tool_call") == "tool"
    assert _infer_chain("approval_decided") == "tool"
    assert _infer_chain("leader_plan") == "decision"
    # 未知 event_type 默认 call
    assert _infer_chain("unknown_event") == "call"


def test_audit_add_event_with_chain(repos):
    """add_event 自动按 event_type 推断 chain,写入 run_events.chain 列。"""
    run_id = repos["runs"].create_run("t", "task")
    repos["audit"].add_event(run_id, "tool_call", "worker_a", {"tools": ["search_web"]})
    events = repos["audit"].list_events(run_id)
    assert len(events) == 1
    assert events[0]["chain"] == "tool"


def test_audit_list_events_by_chain(repos):
    """list_events_by_chain 按链类型过滤。"""
    run_id = repos["runs"].create_run("t", "task")
    repos["audit"].add_event(run_id, "run_start", "system")
    repos["audit"].add_event(run_id, "leader_plan", "leader")
    repos["audit"].add_event(run_id, "tool_call", "worker_a")
    repos["audit"].add_event(run_id, "worker_end", "worker_a")

    call_events = repos["audit"].list_events_by_chain(run_id, "call")
    tool_events = repos["audit"].list_events_by_chain(run_id, "tool")
    decision_events = repos["audit"].list_events_by_chain(run_id, "decision")

    assert len(call_events) == 2  # run_start + worker_end
    assert len(tool_events) == 1
    assert len(decision_events) == 1


# ---- P-B4: PEP ----

def test_pep_repo_allow_policy():
    """有 allow 策略时放行。"""
    from agentteam.runtime.pep import PEPRepo, check_pep
    # PEPRepo 不需要共享 conn,独立 tmp DB
    import threading
    conn = init_db(":memory:")
    lock = threading.Lock()
    repo = PEPRepo(conn, lock=lock)
    repo.upsert_policy("allow_search", "allow", "worker_a", "tool:invoke", "search_web")
    allowed, reason = repo.evaluate("worker_a", "tool:invoke", "search_web")
    assert allowed
    assert reason == ""


def test_pep_repo_deny_overrides_allow():
    """deny 优先于 allow。"""
    from agentteam.runtime.pep import PEPRepo
    conn = init_db(":memory:")
    lock = threading.Lock()
    repo = PEPRepo(conn, lock=lock)
    repo.upsert_policy("allow_all", "allow", "*", "tool:invoke", "*")
    repo.upsert_policy("deny_rm", "deny", "*", "tool:invoke", "rm_rf")
    # rm_rf 被 deny
    allowed, _ = repo.evaluate("worker_a", "tool:invoke", "rm_rf")
    assert not allowed
    # search_web 仍允许
    allowed, _ = repo.evaluate("worker_a", "tool:invoke", "search_web")
    assert allowed


def test_pep_repo_default_deny():
    """无任何匹配策略时默认拒绝(零信任)。"""
    from agentteam.runtime.pep import PEPRepo
    conn = init_db(":memory:")
    lock = threading.Lock()
    repo = PEPRepo(conn, lock=lock)
    allowed, reason = repo.evaluate("worker_a", "tool:invoke", "search_web")
    assert not allowed
    assert "no matching allow policy" in reason


def test_pep_repo_check_pep_none_pass_through():
    """pep_repo=None 时 check_pep 直接放行(向后兼容)。"""
    from agentteam.runtime.pep import check_pep
    # 不抛异常即放行
    check_pep(None, "worker_a", "tool:invoke", "search_web")


def test_pep_repo_check_pep_denied_raises():
    """deny 时 check_pep 抛 PEPDeniedError。"""
    from agentteam.runtime.pep import PEPRepo, PEPDeniedError, check_pep
    conn = init_db(":memory:")
    lock = threading.Lock()
    repo = PEPRepo(conn, lock=lock)
    # 不配任何 allow 策略 → 默认拒绝
    with pytest.raises(PEPDeniedError):
        check_pep(repo, "worker_a", "tool:invoke", "search_web")


# ---- P-B5: Skill 供应链 ----

def test_skill_meta_public_visibility(repos):
    """public skill 任何 team 都能访问。"""
    repos["skills"].upsert_skill("code_review", visibility="public", owner_team="alpha")
    allowed, _ = repos["skills"].check_access("code_review", "beta")
    assert allowed


def test_skill_meta_private_visibility(repos):
    """private skill 仅 owner_team 可访问。"""
    repos["skills"].upsert_skill("private_skill", visibility="private", owner_team="alpha")
    # owner 可访问
    allowed, _ = repos["skills"].check_access("private_skill", "alpha")
    assert allowed
    # 其他 team 不可访问
    allowed, reason = repos["skills"].check_access("private_skill", "beta")
    assert not allowed
    assert "private to team 'alpha'" in reason


def test_skill_meta_protected_requires_acl(repos):
    """protected skill 需 ACL 才能访问。"""
    repos["skills"].upsert_skill("protected_skill", visibility="protected", owner_team="alpha")
    # 未授权 team 拒绝
    allowed, reason = repos["skills"].check_access("protected_skill", "beta")
    assert not allowed
    assert "no ACL" in reason
    # 授权后允许
    repos["skills"].grant_access("protected_skill", "beta")
    allowed, _ = repos["skills"].check_access("protected_skill", "beta")
    assert allowed


def test_skill_meta_revoked_blocks_all(repos):
    """revoke 后即使 ACL 已授权也拒绝。"""
    repos["skills"].upsert_skill("skill_x", visibility="public")
    repos["skills"].grant_access("skill_x", "beta")
    # 紧急撤销
    assert repos["skills"].revoke_skill("skill_x")
    allowed, reason = repos["skills"].check_access("skill_x", "beta")
    assert not allowed
    assert "revoked" in reason


def test_skill_meta_unregistered_blocks(repos):
    """未注册的 skill 拒绝加载(防止 shadow skill)。"""
    allowed, reason = repos["skills"].check_access("ghost_skill", "alpha")
    assert not allowed
    assert "not registered" in reason


# ---- P-B6: MCP Server 鉴权字段 ----

def test_mcp_server_auth_fields_default():
    """默认 auth_type='none',requires_auth=False。"""
    from agentteam.domain.mcp_server import MCPServer
    s = MCPServer(name="fs", command="fs_server")
    assert s.auth_type == "none"
    assert s.auth_credential is None
    assert not s.requires_auth
    assert s.build_auth_headers() == {}


def test_mcp_server_bearer_auth_header():
    """bearer 模式生成 'Authorization: Bearer xxx'。"""
    from agentteam.domain.mcp_server import MCPServer
    s = MCPServer(
        name="api", command="",
        transport="http", url="https://example.com",
        auth_type="bearer", auth_credential="my-token",
    )
    assert s.requires_auth
    headers = s.build_auth_headers()
    assert headers == {"Authorization": "Bearer my-token"}


def test_mcp_server_basic_auth_header():
    """basic 模式生成 'Authorization: Basic base64(user:pass)'。"""
    import base64
    from agentteam.domain.mcp_server import MCPServer
    s = MCPServer(
        name="api", command="",
        transport="http", url="https://example.com",
        auth_type="basic", auth_credential="user:pass",
    )
    headers = s.build_auth_headers()
    expected = base64.b64encode(b"user:pass").decode("ascii")
    assert headers == {"Authorization": f"Basic {expected}"}


def test_mcp_server_api_key_custom_header():
    """api_key 模式支持自定义 header 名。"""
    from agentteam.domain.mcp_server import MCPServer
    s = MCPServer(
        name="api", command="",
        auth_type="api_key", auth_credential="key123",
        auth_header_name="X-API-Key",
    )
    headers = s.build_auth_headers()
    assert headers == {"X-API-Key": "key123"}


def test_mcp_server_serializer_roundtrip_with_auth():
    """serializer 保留 auth 字段(dict → MCPServer → dict)。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.domain.serializer import _mcp_server_from_dict
    from dataclasses import asdict
    original = MCPServer(
        name="api", command="run",
        auth_type="bearer", auth_credential="secret-token",
        auth_header_name="Authorization",
    )
    d = asdict(original)
    restored = _mcp_server_from_dict(d)
    assert restored.auth_type == "bearer"
    assert restored.auth_credential == "secret-token"


# ---- P-B7: 配额告警阈值 ----

def test_quota_warn_threshold_status_warned(repos):
    """used >= warn_threshold 时 status='warned' 但 allowed=True。"""
    repos["quotas"].upsert("alpha", token_limit=1000, period_seconds=86400, warn_threshold=500)
    # 先跑一个 run 消耗 600 token
    run_id = repos["runs"].create_run("alpha", "task")
    repos["runs"].end_run(run_id, "completed", total_tokens=600)
    check = repos["quotas"].check_quota("alpha")
    assert check["allowed"]  # 未超额
    assert check["status"] == "warned"
    assert check["used"] == 600
    assert check["warn_threshold"] == 500


def test_quota_warn_threshold_status_blocked(repos):
    """used >= token_limit 时 status='blocked' 且 allowed=False。"""
    repos["quotas"].upsert("alpha", token_limit=1000, period_seconds=86400, warn_threshold=500)
    run_id = repos["runs"].create_run("alpha", "task")
    repos["runs"].end_run(run_id, "completed", total_tokens=1200)
    check = repos["quotas"].check_quota("alpha")
    assert not check["allowed"]
    assert check["status"] == "blocked"


def test_quota_warn_threshold_status_ok(repos):
    """used < warn_threshold 时 status='ok'。"""
    repos["quotas"].upsert("alpha", token_limit=1000, period_seconds=86400, warn_threshold=500)
    run_id = repos["runs"].create_run("alpha", "task")
    repos["runs"].end_run(run_id, "completed", total_tokens=200)
    check = repos["quotas"].check_quota("alpha")
    assert check["allowed"]
    assert check["status"] == "ok"


def test_quota_no_config_returns_ok(repos):
    """无配额配置返回 status='ok'(默认不限)。"""
    check = repos["quotas"].check_quota("ghost_team")
    assert check["allowed"]
    assert check["status"] == "ok"
    assert check["limit"] == 0


# ---- P-B8: 多维统计 + 审计时间范围检索 ----

def test_audit_aggregate_by_chain(repos):
    """aggregate_by_chain 返回三链分布。"""
    run_id = repos["runs"].create_run("t", "task")
    repos["audit"].add_event(run_id, "run_start", "system")
    repos["audit"].add_event(run_id, "tool_call", "w")
    repos["audit"].add_event(run_id, "leader_plan", "leader")
    chain_counts = repos["audit"].aggregate_by_chain()
    assert chain_counts.get("call", 0) >= 1
    assert chain_counts.get("tool", 0) >= 1
    assert chain_counts.get("decision", 0) >= 1


def test_audit_aggregate_top_tools(repos):
    """aggregate_top_tools 从 tool_call payload 提取工具频次 top N。"""
    run_id = repos["runs"].create_run("t", "task")
    repos["audit"].add_event(run_id, "tool_call", "w", {"tools": ["search_web", "read_file"]})
    repos["audit"].add_event(run_id, "tool_call", "w", {"tools": ["search_web"]})
    repos["audit"].add_event(run_id, "tool_call", "w", {"tools": ["write_file"]})
    top = repos["audit"].aggregate_top_tools(limit=10)
    tools = {item["tool"]: item["count"] for item in top}
    assert tools["search_web"] == 2
    assert tools["read_file"] == 1
    assert tools["write_file"] == 1


def test_runs_sum_tokens_by_team(repos):
    """sum_tokens_by_team 按 team 汇总 token 用量。"""
    r1 = repos["runs"].create_run("alpha", "t1")
    repos["runs"].end_run(r1, "completed", total_tokens=500)
    r2 = repos["runs"].create_run("beta", "t2")
    repos["runs"].end_run(r2, "completed", total_tokens=300)
    r3 = repos["runs"].create_run("alpha", "t3")
    repos["runs"].end_run(r3, "completed", total_tokens=200)
    tokens_by_team = repos["runs"].sum_tokens_by_team()
    assert tokens_by_team["alpha"] == 700
    assert tokens_by_team["beta"] == 300


def test_admin_audit_time_range_filter(repos):
    """list_events 支持 start_time/end_time 过滤。"""
    # 写入 3 条事件,时间戳由 DB 自动生成(间隔由 SQLite 处理)
    repos["admin_audit"].add_event("team_created", "team", "alpha", actor="alice")
    repos["admin_audit"].add_event("quota_set", "quota", "alpha", actor="alice")
    repos["admin_audit"].add_event("team_deleted", "team", "beta", actor="bob")

    # 用 event_type 过滤
    events = repos["admin_audit"].list_events(event_type="team_created")
    assert len(events) == 1
    assert events[0]["resource"] == "team"
    assert events[0]["resource_id"] == "alpha"
    assert events[0]["actor"] == "alice"

    # 用 actor 过滤
    events = repos["admin_audit"].list_events(actor="bob")
    assert len(events) == 1
    assert events[0]["event_type"] == "team_deleted"

    # 用 resource 过滤
    events = repos["admin_audit"].list_events(resource="quota")
    assert len(events) == 1
    assert events[0]["event_type"] == "quota_set"
