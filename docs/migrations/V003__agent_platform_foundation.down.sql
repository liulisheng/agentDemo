-- 回滚由部署流程按逆序执行；生产环境先备份并确认没有活动 Run。
DROP TABLE IF EXISTS usage_ledger;
DROP TABLE IF EXISTS platform_trace_spans;
DROP TABLE IF EXISTS model_routes;
DROP TABLE IF EXISTS deployments;
DROP TABLE IF EXISTS digital_employee_versions;
DROP TABLE IF EXISTS digital_employees;
