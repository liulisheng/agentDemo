# Docker MySQL 8.4

这个 Compose 文件只用于本地开发和验证，不等于生产数据库部署。首次初始化时，MySQL 官方镜像会自动执行 `docs/migrations/V001__core_schema.sql`。

## 启动

```powershell
cd deploy/mysql
Copy-Item .env.example .env
# 编辑 .env，至少替换两个密码
docker compose up -d
docker compose ps
docker compose logs -f mysql
```

健康检查变为 `healthy` 后，验证表结构：

```powershell
docker compose exec mysql mysql -u harness -p harness -e "SHOW TABLES;"
docker compose exec mysql mysql -u harness -p harness -e "SHOW CREATE TABLE runs\G"
```

## 重新执行 migration

官方镜像只在数据目录为空时执行 `/docker-entrypoint-initdb.d` 文件。修改 migration 后，必须销毁验证卷再重建：

```powershell
docker compose down -v
docker compose up -d
```

这会删除本地验证数据，只能用于 disposable 环境。生产环境应使用 Flyway、Liquibase 或独立 migration runner，不要依赖 `down -v`。

## 验证记录

2026-11-10 已确认 Docker Desktop Linux Engine 可用，并在独立的一次性 MySQL 8.4.13 容器中通过 P4 集成测试。该容器未使用 Compose 数据卷，验证后已删除。上面的 Compose 配置仍是本地开发实例的启动方式；运行环境不同时，先用 `docker version` 确认 Client 和 Server 都可连接。
