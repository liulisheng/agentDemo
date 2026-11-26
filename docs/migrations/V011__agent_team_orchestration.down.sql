-- V011 down 仅用于 disposable database 演练，不得用于生产或共享数据库。

ALTER TABLE runs
    DROP FOREIGN KEY fk_run_parent_run,
    DROP FOREIGN KEY fk_run_task,
    DROP FOREIGN KEY fk_run_orchestration,
    DROP INDEX idx_run_task,
    DROP INDEX idx_run_orchestration,
    DROP COLUMN attempt_no,
    DROP COLUMN parent_run_id,
    DROP COLUMN task_id,
    DROP COLUMN orchestration_id,
    COMMENT='Run 状态、幂等、租约和平台执行快照';

ALTER TABLE agent_bindings
    DROP INDEX idx_agent_binding_team_member,
    DROP COLUMN capabilities_snapshot_json,
    DROP COLUMN team_member_role,
    DROP COLUMN team_member_id,
    COMMENT='Conversation 中员工与独立 Session 的绑定';

ALTER TABLE conversations
    DROP INDEX idx_conversation_mode,
    DROP CHECK ck_conversation_mode,
    DROP COLUMN conversation_mode,
    COMMENT='用户可见的多消息对话容器';

DROP TABLE IF EXISTS artifact_deliveries;
DROP TABLE IF EXISTS artifact_links;
DROP TABLE IF EXISTS execution_artifacts;
DROP TABLE IF EXISTS orchestration_task_events;
DROP TABLE IF EXISTS orchestration_task_attempts;
DROP TABLE IF EXISTS orchestration_task_dependencies;
DROP TABLE IF EXISTS orchestration_tasks;
DROP TABLE IF EXISTS orchestration_runs;
DROP TABLE IF EXISTS conversation_team_bindings;
ALTER TABLE team_versions
    DROP FOREIGN KEY fk_team_version_finalizer,
    DROP FOREIGN KEY fk_team_version_router;
DROP TABLE IF EXISTS team_members;
DROP TABLE IF EXISTS team_versions;
DROP TABLE IF EXISTS teams;

DELETE FROM schema_version WHERE version = 11;
