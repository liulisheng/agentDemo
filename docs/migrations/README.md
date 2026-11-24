# MySQL Migration 约定

`docs/migrations/` 是仓库中唯一的可执行 schema/migration SQL 目录。V001/V002 是历史迁移，禁止为补注释或兼容代码而修改；新增或修改表结构时必须新增 migration，并为每张新表和每个新建/变更字段添加中文 MySQL `COMMENT`。MySQL 集成测试读取 `information_schema.TABLES` 和 `information_schema.COLUMNS` 验证实际落库注释。

当前迁移面向 MySQL 8.4、InnoDB 和 `utf8mb4_0900_ai_ci`。V003 是在首次共享环境应用前完成的控制面迁移。如果任何共享环境已经应用较早的 V003 草案，不得重写 V003，应先核对 schema/checksum 并制定向前兼容的 V004。V004 增加资源中心，V005 增加 Skill `INLINE`/`FILE_PACKAGE` 模式与文件清单，V006 增加 MCP Stdio Profile、一次性 Tool Approval、Run 提交者字段和审批等待状态，V007 增加每 Session Harness Home 执行租约，V008 增加租户模型连接、多模型目录、ModelRoute 绑定及异步模型测试记录；Runner 会在检测到部分 DDL 或版本标记不一致时 fail closed。

V006 也为按 Session 读取最新 Run、按 Conversation 聚合 PENDING Approval 数新增复合索引，供员工工作台游标列表使用；并为 Worker 的平台级 QUEUED claim 增加 `(platform_id, state, created_at, run_id)` 索引。列表查询和更新时间推进不新增重复的 Conversation 索引表。

V007 `session_runtime_leases` 为同一 Session Home 持有 ACTIVE 或 QUARANTINED 租约，租约唯一键使用 platform/tenant/session 范围。Run claim、Run heartbeat、Session lease heartbeat、确定终态释放和 UNKNOWN 隔离必须在事务中维护同一 fencing token。QUARANTINED 不自动过期，Session 转为 INTERRUPTED；管理员需要创建新的 Session 才能继续，不能让另一个 Worker 自动复用可能仍被旧 Harness 写入的 Home。

V008 新增 `model_connections`、`model_catalog_items` 和 `model_test_records`，并为 `model_routes` 增加连接、目录模型和单调递增路由版本引用。迁移会把同租户内旧路由按 provider/protocol/endpoint/SecretRef 确定性归并到连接，按 model name 回填目录项；API Key 等明文不参与回填。ModelRoute 仍保留已有 endpoint/provider 字段供回滚期间旧应用读取。回滚先移除新增外键和字段，再删除 V008 表；已写入的 V008 测试记录随 V008 回滚删除，因此 down 仅用于 disposable database 演练。

V009 新增租户隔离的 `model_route_bindings` 和 Deployment 不可变运行配置修订表；从旧 `execution_profile_bindings`、EmployeeVersion 和 Profile 回填模型路由/Deployment 配置，保留已运行员工的本地设置。旧 Deployment 的 `model_route_snapshot_json` 按当前 `ModelRouteSnapshot` 契约回填 tenant/route/connection 版本、模型 ID、协议、端点和受控 SecretRef 对象；凭据明文不迁移，迁移后 Session 可恢复该快照。V009 还为 `deployments`/`sessions` 添加运行配置修订和来源字段，使 EmployeeVersion 的旧运行字段可空，并将 `execution_profiles.model_alias` 改为可空的历史兼容字段。新 ModelRoute 选择独立保存；迁移不创建或复制连接、模型凭据。Runner 使用两张新表及 Deployment/Session 指针字段识别 V009 部分 DDL；迁移完成后还需从 `information_schema` 验证所有新增/变更字段的中文注释、可空性、租户复合外键和配置回填。V009 down 仅可在 disposable database 演练；生产回滚保留配置修订、表和列，不能回退到要求新旧字段非空的旧二进制。

V010 新增租户范围的 `runtime_strategy_bindings`，独立保存用户/租户/平台的回答策略默认值。回答策略不写入 EngineProfile 或 ModelRoute；`runtime_options_json` 只允许类型化白名单字段。V010 需要通过 `information_schema.TABLES`/`COLUMNS` 验证表和字段中文 COMMENT、租户复合外键、作用域唯一键和 down 边界。生产回滚保留已写入的策略 JSON 和配置修订；只回滚兼容代码，不删除历史绑定。

升级到 V007 前先停止新 Run claim 并排空 Worker；迁移会把既有 STARTING/RUNNING/CANCELLING Run 的 lease 写入新表。如果同一 Session 仍有多个活动 Run，Session 主键冲突会使迁移停止，必须先人工核对这些 Run，不能由迁移任意选一个继续。

## 显式预检和迁移

先配置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER` 和 `MYSQL_PASSWORD`。API/Worker 启动只做只读 schema 预检，不会执行 DDL。迁移需要人工显式运行：

```bash
cd backend
python -m pip install -e '.[mysql]'
cd ..
agent-platform-migration preflight --migrations-dir docs/migrations
agent-platform-migration apply --migrations-dir docs/migrations
agent-platform-migration preflight --migrations-dir docs/migrations
```

`preflight` 读取 `schema_version`、migration checksum 和 `information_schema`。`apply` 才会取得 MySQL advisory lock、逐版本执行 DDL、写入 checksum history 并在第一个失败处停止。V001/V002 没有旧 checksum history 时，preflight 会显示 legacy warning，不会声称已核验历史文件。

如果 preflight 发现版本、checksum 或部分 DDL 不一致，先备份并调查目标库；不要重放已应用 migration。不要把空库或半迁移库当作可运行应用的数据库。

## 一次性 MySQL 集成验证

集成测试只有在显式设置 `AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1` 时才会创建随机命名 disposable database，并在 teardown 删除。该开关只能连接一次性 MySQL：

```bash
cd backend
AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 \
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=agent_platform_test \
MYSQL_PASSWORD='<一次性数据库密码>' \
python -m pytest tests/integration/test_production_control_plane_migration.py \
  tests/integration/test_mysql_migration_runner.py -q
```

测试覆盖 V003 正向/回滚、V004 资源中心、V005 Skill 文件包、V006 MCP/审批迁移、V007 Session lease、V008 模型连接/目录/测试迁移、V009 运行配置及历史回填、外键与租户联合键、中文表/字段注释、重复迁移、checksum 变化和失败停止。V006/V007/V008/V009 down 脚本只在 disposable database 验证；生产回滚保留增量 schema。运行版本和最终 checksum 记录在 [`../验证记录.md`](../验证记录.md)。

## 备份与回滚

在升级前停止写入服务，交互式输入密码并备份数据库；对象文件必须和数据库在同一停写窗口内归档：

```bash
mysqldump --single-transaction -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  "$MYSQL_DATABASE" > /secure/backup/agent-platform.sql
tar -C "$AGENT_PLATFORM_DATA_ROOT" -czf /secure/backup/agent-platform-data.tgz objects harness-sessions
```

[`V003__agent_platform_foundation.down.sql`](V003__agent_platform_foundation.down.sql) 仅用于 disposable database 验证；不得在有业务数据的环境执行。生产回滚采用兼容旧应用版本的代码回滚，并保留增量 schema。破坏性 DDL 需单独审批并先演练 DB/对象备份恢复。

项目级长期约定见 [`文档维护约定`](../文档维护约定.md) 和仓库根目录 [`AGENTS.md`](../../AGENTS.md)。
