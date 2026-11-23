-- V009 down 仅用于 disposable database 演练，不得用于生产或共享数据库。

ALTER TABLE sessions
    DROP FOREIGN KEY fk_session_runtime_config,
    DROP CHECK ck_session_runtime_profile_source,
    DROP CHECK ck_session_runtime_model_route_source,
    DROP INDEX idx_session_runtime_config,
    DROP COLUMN runtime_config_version,
    DROP COLUMN runtime_profile_source,
    DROP COLUMN runtime_model_route_source;

ALTER TABLE deployments
    DROP FOREIGN KEY fk_deployment_runtime_config,
    DROP CHECK ck_deployment_status,
    MODIFY COLUMN status VARCHAR(32) NOT NULL COMMENT 'Deployment 生命周期状态',
    DROP COLUMN runtime_config_version,
    ADD CONSTRAINT ck_deployment_status CHECK (status IN ('ACTIVE', 'DISABLED', 'ARCHIVED'));

DROP TABLE IF EXISTS deployment_runtime_config_versions;
DROP TABLE IF EXISTS model_route_bindings;

ALTER TABLE digital_employee_versions
    MODIFY COLUMN execution_profile_id CHAR(26) NOT NULL COMMENT '绑定的 ExecutionProfile 标识',
    MODIFY COLUMN execution_profile_version INT UNSIGNED NOT NULL COMMENT '绑定的 ExecutionProfile 版本号',
    MODIFY COLUMN model_route_id VARCHAR(64) NULL COMMENT '员工版本固定的模型路由标识',
    MODIFY COLUMN workspace_profile_id CHAR(26) NULL COMMENT '员工版本固定的工作区配置标识',
    MODIFY COLUMN workspace_profile_version INT UNSIGNED NULL COMMENT '员工版本固定的工作区配置版本',
    MODIFY COLUMN sandbox_profile_id CHAR(26) NULL COMMENT '员工版本固定的沙箱配置标识',
    MODIFY COLUMN sandbox_profile_version INT UNSIGNED NULL COMMENT '员工版本固定的沙箱配置版本',
    MODIFY COLUMN approval_policy_json JSON NOT NULL COMMENT '员工版本审批策略快照';

ALTER TABLE execution_profiles
    MODIFY COLUMN model_alias VARCHAR(128) NOT NULL COMMENT '默认模型别名';

DELETE FROM schema_migration_history WHERE version = 9;
DELETE FROM schema_version WHERE version = 9;
