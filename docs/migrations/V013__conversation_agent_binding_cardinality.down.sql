-- 仅用于 disposable database 回滚演练。
-- 若同一 TEAM Conversation 内已存在同员工的多个 TeamMember，恢复旧唯一索引会失败；生产应保留向前迁移。
ALTER TABLE agent_bindings
    DROP INDEX uk_conversation_team_member,
    DROP INDEX uk_conversation_single_agent_employee,
    DROP COLUMN single_agent_employee_id,
    ADD UNIQUE KEY uk_conversation_employee (platform_id,tenant_id,conversation_id,employee_id);

DELETE FROM schema_version WHERE version = 13;
