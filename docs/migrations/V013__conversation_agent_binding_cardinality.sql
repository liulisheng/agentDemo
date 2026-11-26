-- 支持一个 TEAM Conversation 中同一数字员工绑定多个固定 TeamMember Session。
-- 单员工 Conversation 继续按员工唯一；团队 Conversation 按 TeamMember 唯一。

ALTER TABLE agent_bindings
    DROP INDEX uk_conversation_employee,
    ADD COLUMN single_agent_employee_id CHAR(26)
        GENERATED ALWAYS AS (CASE WHEN team_member_id IS NULL THEN employee_id ELSE NULL END) STORED
        COMMENT '单员工 Conversation 的唯一员工绑定键，TEAM 成员使用独立 TeamMember 标识',
    ADD UNIQUE KEY uk_conversation_single_agent_employee
        (platform_id,tenant_id,conversation_id,single_agent_employee_id),
    ADD UNIQUE KEY uk_conversation_team_member
        (platform_id,tenant_id,conversation_id,team_member_id);

INSERT INTO schema_version (version) VALUES (13);
