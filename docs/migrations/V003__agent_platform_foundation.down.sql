-- V003 rollback for a disposable database only.
-- Never drop customer data in a shared or production database.

ALTER TABLE content_gc_queue
    DROP CHECK ck_content_gc_object_type,
    ADD CONSTRAINT ck_content_gc_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE')
    ),
    COMMENT='对象清理重试队列';

ALTER TABLE content_upload_intents
    DROP CHECK ck_content_upload_object_type,
    DROP CHECK ck_content_upload_retention,
    ADD CONSTRAINT ck_content_upload_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE')
    ),
    DROP COLUMN original_filename,
    DROP COLUMN mime_type,
    DROP COLUMN retention_expires_at,
    DROP COLUMN expected_size_bytes,
    DROP COLUMN expected_sha256,
    DROP COLUMN retention_policy,
    DROP COLUMN encryption_key_version,
    COMMENT='对象上传租约记录';

ALTER TABLE messages
    DROP CHECK ck_message_source,
    DROP FOREIGN KEY fk_message_sender,
    DROP FOREIGN KEY fk_message_conversation,
    DROP INDEX idx_message_conversation,
    DROP COLUMN sender_id,
    DROP COLUMN source,
    DROP COLUMN routing_depth,
    DROP COLUMN conversation_id,
    DROP COLUMN retention_policy,
    COMMENT='会话消息索引和正文对象引用';

ALTER TABLE runs
    DROP FOREIGN KEY fk_run_session,
    DROP FOREIGN KEY fk_run_employee_version,
    DROP FOREIGN KEY fk_run_execution_profile,
    DROP FOREIGN KEY fk_run_model_route,
    DROP INDEX uk_run_idempotency,
    DROP INDEX idx_run_trace,
    ADD UNIQUE KEY uk_run_idempotency (platform_id, tenant_id, idempotency_key),
    DROP COLUMN employee_id,
    DROP COLUMN employee_version,
    DROP COLUMN execution_profile_id,
    DROP COLUMN execution_profile_version,
    DROP COLUMN model_route_id,
    DROP COLUMN trace_id,
    DROP COLUMN snapshot_json,
    DROP COLUMN context_snapshot_json,
    DROP COLUMN runtime_session_ref,
    DROP COLUMN runtime_result_ref,
    COMMENT='Harness Run 状态与租约记录';

ALTER TABLE runs
    ADD CONSTRAINT fk_run_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id);

ALTER TABLE sessions
    DROP FOREIGN KEY fk_session_deployment,
    DROP FOREIGN KEY fk_session_employee_version,
    DROP FOREIGN KEY fk_session_execution_profile,
    DROP FOREIGN KEY fk_session_model_route,
    DROP INDEX idx_session_deployment,
    DROP COLUMN deployment_id,
    DROP COLUMN employee_id,
    DROP COLUMN employee_version,
    DROP COLUMN execution_profile_id,
    DROP COLUMN execution_profile_version,
    DROP COLUMN model_route_id,
    DROP COLUMN trace_id,
    DROP COLUMN composition_snapshot_json,
    DROP COLUMN runtime_thread_ref,
    DROP COLUMN runtime_workspace_ref,
    DROP COLUMN runtime_result_ref,
    COMMENT='Harness Runtime Session 索引';

DELETE FROM schema_version WHERE version = 3;

DROP TABLE IF EXISTS usage_adjustments;
DROP TABLE IF EXISTS usage_ledger;
DROP TABLE IF EXISTS platform_trace_spans;
DROP TABLE IF EXISTS media_assets;
DROP TABLE IF EXISTS agent_bindings;
DROP TABLE IF EXISTS conversation_participants;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS capability_bindings;
DROP TABLE IF EXISTS employee_tool_bindings;
DROP TABLE IF EXISTS employee_skill_bindings;
DROP TABLE IF EXISTS skill_tool_declarations;
DROP TABLE IF EXISTS deployments;
DROP TABLE IF EXISTS digital_employee_versions;
DROP TABLE IF EXISTS execution_profile_bindings;
DROP TABLE IF EXISTS execution_profiles;
DROP TABLE IF EXISTS sandbox_profiles;
DROP TABLE IF EXISTS workspace_profiles;
DROP TABLE IF EXISTS model_routes;
DROP TABLE IF EXISTS tool_versions;
DROP TABLE IF EXISTS skill_versions;
DROP TABLE IF EXISTS digital_employees;
DROP TABLE IF EXISTS schema_migration_history;
