-- Agent 团队 Attempt 租约元数据向前迁移。
-- 只允许显式 migration runner 执行；已应用的 V011 保持不变。

ALTER TABLE orchestration_task_attempts
    ADD COLUMN worker_id VARCHAR(255) NULL COMMENT '当前领取该 Attempt 的 Worker 标识',
    ADD COLUMN fencing_token BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Attempt 租约围栏序号，阻止过期 Worker 覆盖新结果';

INSERT INTO schema_version (version) VALUES (12);
