DROP TABLE IF EXISTS tool_approval_requests;

ALTER TABLE tool_versions
    DROP FOREIGN KEY fk_tool_mcp_profile,
    DROP CHECK ck_tool_mcp_profile_binding,
    DROP CHECK ck_tool_mcp_risk_level,
    DROP INDEX idx_tool_mcp_profile,
    DROP COLUMN idempotency_support,
    DROP COLUMN mcp_risk_level,
    DROP COLUMN preview_tool_name,
    DROP COLUMN input_schema_sha256,
    DROP COLUMN remote_tool_name,
    DROP COLUMN mcp_server_profile_version,
    DROP COLUMN mcp_server_profile_id,
    COMMENT='租户范围内不可变的 Tool 版本';

ALTER TABLE runs
    DROP FOREIGN KEY fk_run_submitter,
    DROP CHECK ck_run_state,
    DROP INDEX idx_run_session_latest,
    DROP INDEX idx_run_platform_claim,
    ADD CONSTRAINT ck_run_state CHECK (
        state IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCELLING', 'SUCCEEDED',
                  'FAILED', 'CANCELLED', 'UNKNOWN')
    ),
    DROP COLUMN submitted_by_user_id,
    COMMENT='Run 状态、幂等、租约和平台执行快照';

DROP TABLE IF EXISTS mcp_server_profiles;

DELETE FROM schema_migration_history WHERE version = 6;
DELETE FROM schema_version WHERE version = 6;
