# MySQL Migration 约定

`docs/migrations/` 是仓库中唯一的可执行 schema/migration SQL 目录。V001/V002 是历史迁移，禁止为补注释或兼容代码而修改；新增或修改表结构时必须新增 migration，并为每张新表和每个新建/变更字段添加中文 MySQL `COMMENT`。MySQL 集成测试读取 `information_schema.TABLES` 和 `information_schema.COLUMNS` 验证实际落库注释。

当前迁移面向 MySQL 8.4、InnoDB 和 `utf8mb4_0900_ai_ci`。V003 是在首次共享环境应用前完成的控制面迁移。如果任何共享环境已经应用较早的 V003 草案，不得重写 V003，应先核对 schema/checksum 并制定向前兼容的 V004。当前 V004 是资源中心向前迁移，V005 增加 Skill `INLINE`/`FILE_PACKAGE` 模式与文件清单；Runner 会在检测到部分 V005 DDL 或版本标记不一致时 fail closed。

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

测试覆盖 V003 正向/回滚、V004 资源中心和 V005 Skill 文件包迁移、外键与租户联合键、中文表/字段注释、重复迁移、checksum 变化和失败停止。V005 文件包清单外键确保文件行与当前租户 Skill 版本和 MediaAsset 同平台同租户。运行版本和最终 checksum 记录在 [`../验证记录.md`](../验证记录.md)。

## 备份与回滚

在升级前停止写入服务，交互式输入密码并备份数据库；对象文件必须和数据库在同一停写窗口内归档：

```bash
mysqldump --single-transaction -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  "$MYSQL_DATABASE" > /secure/backup/agent-platform.sql
tar -C "$AGENT_PLATFORM_DATA_ROOT" -czf /secure/backup/agent-platform-objects.tgz objects
```

[`V003__agent_platform_foundation.down.sql`](V003__agent_platform_foundation.down.sql) 仅用于 disposable database 验证；不得在有业务数据的环境执行。生产回滚采用兼容旧应用版本的代码回滚，并保留增量 schema。破坏性 DDL 需单独审批并先演练 DB/对象备份恢复。

项目级长期约定见 [`文档维护约定`](../文档维护约定.md) 和仓库根目录 [`AGENTS.md`](../../AGENTS.md)。
