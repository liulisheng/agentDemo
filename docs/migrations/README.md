# MySQL migration 验收说明

`V001__core_schema.sql` 是 Harness 控制面的核心 migration；`V002__conversation_messages.sql` 增加消息顺序字段、消息索引、流式分段索引、上传 intent 和对象清理队列。目标均为 MySQL 8.4、InnoDB 和 `utf8mb4_0900_ai_ci`。V001+V002 已于 2026-11-13 在一次性 MySQL 8.4.13 实例依序执行；本轮代表性 V002 集成检查通过，完整生产迁移流程仍待验证。两个文件必须按顺序运行。密码、OIDC Provider、浏览器会话和令牌签发仍由用户中心负责。

## 执行前提

- 使用一次性 MySQL 8.4 实例或临时数据库。
- 连接账号具备建表和外键权限。
- 在目标实例确认 `sql_mode` 未关闭 CHECK 约束执行。
- 不要把 SQLite 或其他兼容数据库的结果当作 MySQL 8.4 结果。

## 执行命令示例

```bash
mysql --protocol=tcp -h 127.0.0.1 -P 3306 -u harness_verify -p \
  -e "CREATE DATABASE harness_verify CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
mysql --protocol=tcp -h 127.0.0.1 -P 3306 -u harness_verify -p harness_verify \
  < docs/migrations/V001__core_schema.sql
mysql --protocol=tcp -h 127.0.0.1 -P 3306 -u harness_verify -p harness_verify \
  < docs/migrations/V002__conversation_messages.sql
```

DDL 会由 MySQL 独立提交；迁移工具必须在失败后停止，不要把半套表结构当成成功版本。生产接入 Flyway、Liquibase 或自有 runner 时，需要把文件名和校验和写入迁移历史表。

## 最小验收场景

1. 插入两个平台和各自租户、用户、session、run，确认同名 `tenant_id` 只在本平台内唯一。
2. 构造平台 A 的用户关联平台 B 的租户，确认联合外键失败。
3. 写入非法 `status` 或 `state`，确认 CHECK 失败。
4. 两个事务按 `platform_id + tenant_id` 领取 queued run，确认 `FOR UPDATE SKIP LOCKED` 只返回一条。
5. 使用旧 `fencing_token` 更新或完成 run，确认影响行数为 0。
6. 重复提交同一租户的 `idempotency_key`，确认返回原 run 或冲突，不产生第二条记录。
7. 通过连接池连续执行两个不同租户请求，确认第二个请求不会继承第一个请求的上下文；仓储 SQL 必须显式绑定 `platform_id` 和 `tenant_id`。

已由 Task 1–3 验证：V002 schema 与代表性 CHECK/外键、只读 doctor、MySQL 消息顺序分配、USER/ASSISTANT 发布事务、请求幂等、租户范围过滤、上传 lease 过期入队、过期后发布/续租拒绝，以及消息删除排入正文引用并清除匹配 `runs.result_ref`。这些仓储用例不等于并发竞争压力测试。

仍需验证/实现：`last_committed_chunk_sequence` 与 chunk 行的连续前缀；upload intent 过期/发布 barrier 竞争和死锁恢复；延迟 PUT 首次清理后完成时的孤儿重扫；删除队列 worker、session 级显式删除与所有关联 UPLOADING intents；对象清理重试；durable-tail replay/通知交界无漏段；孤儿扫描对旧 `runs.result_ref` 的保护。生产对象存储/KMS、备份恢复、最小权限和负载测试也仍待完成。

完整项目清单见 [真实验证矩阵](../verification-matrix.md)。2026-11-10 已在一次性 Docker MySQL 8.4.13 实例上执行 V001，并通过 `tests/test_mysql_integration.py`（1 passed）。2026-11-13 又在无持久卷的一次性 Docker MySQL 8.4.13 实例依序执行 V001+V002；最终 `tests/test_mysql_integration.py` 为 7 passed，覆盖 schema/doctor、消息与 chunk 事务、服务级流式完成/续读、租户隔离、worker 租约回收和上传 lease/GC enqueue。离线全套为 157 passed、2 skipped。DeepSeek 实时流、显式 session 删除、竞争压力、清理 worker、生产权限、备份恢复和负载测试仍待验证。
