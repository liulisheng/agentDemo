from pathlib import Path


def test_agent_platform_migration_is_ordered_and_has_reversal() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V003__agent_platform_foundation.sql"
    down = root / "docs/migrations/V003__agent_platform_foundation.down.sql"
    text = up.read_text()
    assert "platform_id" in text and "tenant_id" in text
    for table in ("digital_employees", "deployments", "model_routes", "platform_trace_spans", "usage_ledger"):
        assert f"CREATE TABLE {table}" in text
        assert f"DROP TABLE IF EXISTS {table}" in down.read_text()


def test_v006_mcp_approval_migration_is_a_forward_schema_change_with_chinese_comments() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V006__mcp_profiles_and_approvals.sql"
    down = root / "docs/migrations/V006__mcp_profiles_and_approvals.down.sql"
    text = up.read_text(encoding="utf-8")
    rollback = down.read_text(encoding="utf-8")

    assert "CREATE TABLE mcp_server_profiles" in text
    assert "CREATE TABLE tool_approval_requests" in text
    assert "ADD COLUMN submitted_by_user_id" in text
    assert "ADD COLUMN mcp_server_profile_id" in text
    assert "mcp_risk_level" in text
    assert "confirmation_idempotency_key" in text
    assert "rejected_by" in text
    assert "active_run_id" in text
    assert "idx_run_session_latest (platform_id, tenant_id, session_id, created_at, run_id)" in text
    assert "idx_run_platform_claim (platform_id, state, created_at, run_id)" in text
    assert "idx_tool_approval_conversation (platform_id, tenant_id, conversation_id, state)" in text
    assert "'AWAITING_APPROVAL'" in text
    assert "COMMENT='" in text
    assert "COMMENT='租户范围内不可变的 Tool 版本及 MCP 调用配置'" in text
    assert "COMMENT='Run 状态、幂等、租约、平台执行快照和审批等待状态'" in text
    assert "INSERT INTO schema_version (version) VALUES (6)" in text
    assert "DROP TABLE IF EXISTS tool_approval_requests" in rollback
    assert "DROP TABLE IF EXISTS mcp_server_profiles" in rollback
    assert "DROP INDEX idx_run_session_latest" in rollback
    assert "DROP INDEX idx_run_platform_claim" in rollback


def test_v007_session_runtime_lease_is_a_tenant_scoped_forward_migration() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V007__session_runtime_leases.sql"
    down = root / "docs/migrations/V007__session_runtime_leases.down.sql"
    text = up.read_text(encoding="utf-8")
    rollback = down.read_text(encoding="utf-8")

    assert "CREATE TABLE session_runtime_leases" in text
    for column in (
        "platform_id",
        "tenant_id",
        "session_id",
        "run_id",
        "worker_id",
        "fencing_token",
        "lease_expires_at",
        "state",
    ):
        assert f"{column} " in text
    assert "FOREIGN KEY (platform_id, tenant_id, session_id)" in text
    assert "FOREIGN KEY (platform_id, tenant_id, run_id)" in text
    assert "QUARANTINED" in text
    assert "COMMENT='" in text
    assert "INSERT INTO schema_version (version) VALUES (7)" in text
    assert "DROP TABLE IF EXISTS session_runtime_leases" in rollback
    assert "DELETE FROM schema_version WHERE version = 7" in rollback
