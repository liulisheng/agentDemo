-- 仅限 disposable database 演练；生产环境回滚保留已写入的团队业务数据。
ALTER TABLE orchestration_task_attempts
    DROP COLUMN fencing_token,
    DROP COLUMN worker_id;

DELETE FROM schema_version WHERE version = 12;
