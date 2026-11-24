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


def test_v009_runtime_configuration_migration_has_tenant_scoped_schema_and_reversal() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V009__deployment_runtime_config.sql"
    down = root / "docs/migrations/V009__deployment_runtime_config.down.sql"
    text = up.read_text(encoding="utf-8")
    rollback = down.read_text(encoding="utf-8")

    assert "CREATE TABLE model_route_bindings" in text
    assert "CREATE TABLE deployment_runtime_config_versions" in text
    assert "uk_model_route_binding_scope" in text
    assert "pk_deployment_runtime_config_versions" in text
    assert "fk_runtime_config_deployment" in text
    assert "fk_runtime_config_profile" in text
    assert "fk_runtime_config_route" in text
    assert "ck_runtime_config_inherit_no_engine_route_override" in text
    assert "ck_runtime_config_override_has_value" in text
    assert "execution_profiles.model_alias" in text
    assert "MODIFY COLUMN model_alias VARCHAR(128) NULL" in text
    assert "ADD COLUMN runtime_config_version" in text
    assert "ADD COLUMN runtime_profile_source" in text
    assert "ADD COLUMN runtime_model_route_source" in text
    assert "历史兼容模型别名；新 Session 模型由独立 ModelRoute 或运行时默认解析" in text
    assert "INSERT INTO schema_version (version) VALUES (9)" in text
    assert "DROP TABLE IF EXISTS deployment_runtime_config_versions" in rollback
    assert "DROP TABLE IF EXISTS model_route_bindings" in rollback
    assert "DELETE FROM schema_version WHERE version = 9" in rollback

    for table in ("model_route_bindings", "deployment_runtime_config_versions"):
        assert f"DROP TABLE IF EXISTS {table}" in rollback


def test_v010_runtime_strategy_defaults_has_chinese_comments_and_reversal() -> None:
    root = Path(__file__).parents[2]
    up = root / "docs/migrations/V010__runtime_strategy_defaults.sql"
    down = root / "docs/migrations/V010__runtime_strategy_defaults.down.sql"
    text = up.read_text(encoding="utf-8")
    rollback = down.read_text(encoding="utf-8")

    assert "CREATE TABLE runtime_strategy_bindings" in text
    assert "runtime_options_json JSON NOT NULL COMMENT" in text
    assert "uk_runtime_strategy_scope" in text
    assert "fk_runtime_strategy_tenant" in text
    assert "ck_runtime_strategy_scope" in text
    assert "INSERT INTO schema_version (version) VALUES (10)" in text
    assert "DROP TABLE IF EXISTS runtime_strategy_bindings" in rollback
