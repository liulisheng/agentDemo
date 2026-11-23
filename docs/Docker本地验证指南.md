# Docker 本地验证指南

本 Compose 栈供本地验收数字员工管理台、资源中心和员工运行配置使用，包含 MySQL 8.4.10、FastAPI 控制面、EngineProvider、独立 Run Worker、Nginx 前端和 LocalObjectStore。默认 `AGENT_PLATFORM_RUNTIME_ENGINE=deepseek`，使用 Fake Identity，不接入真实租户身份；对象文件和 MySQL 数据持久化在 Docker 命名卷中。将该变量显式设为 `fake` 并重启 backend/worker 可切回离线模拟。

## 架构与启动流程

```mermaid
flowchart LR
    Browser[浏览器 localhost:8088] --> Web[Nginx 静态管理台/API 代理]
    Web -->|/api/*| API[FastAPI 控制面]
    API --> Registry[DeepSeek Harness Provider]
    API --> DB[(MySQL 8.4.10)]
    DB --> Worker[独立 Run Worker]
    Worker --> Harness[DeepSeek Harness SDK]
    Harness --> Model[租户 ModelRoute / 本地 Mock]
    API --> Store[(LocalObjectStore)]
    DB --- DBVolume[(control-plane-mysql volume)]
    Store --- ObjectVolume[(control-plane-objects volume)]
```

```mermaid
sequenceDiagram
    participant Compose as Docker Compose
    participant DB as MySQL
    participant Bootstrap as Migration/Seed 容器
    participant API as FastAPI
    participant Web as Nginx
    Compose->>DB: 启动并等待健康检查
    Compose->>Bootstrap: 运行显式 migration apply
    Bootstrap->>DB: 应用所有尚未应用的 V001–V009
    Bootstrap->>DB: 幂等写入 Fake 身份、租户和用户
    Compose->>API: 启动并执行只读 schema preflight
    API->>DB: 创建 MySQL 仓储连接池
    API->>API: 注册 DeepSeek Harness Provider 和 LocalObjectStore
    Compose->>Worker: 注入同一 EngineProvider 配置
    Worker->>Harness: 按固定 RunSnapshot 调用 Harness
    Compose->>Web: 启动静态页面与 /api 反向代理
    Browser->>Web: 管理台路由
    Web->>API: 带 X-Fake-User 的 /api/v1 请求
```

## 数据与 API 状态

| 数据 | 所在位置 | 生命周期 |
|---|---|---|
| 租户、员工、Skill/Tool 版本、Hub/Grant、Deployment、Session、Run、Trace | `control_plane_mysql` 命名卷中的 MySQL | `docker compose down` 保留；`down -v` 删除 |
| ZIP 文件、Skill 辅助文件和其它 MediaAsset 内容 | `control_plane_objects` 命名卷中的 LocalObjectStore | `docker compose down` 保留；`down -v` 删除 |
| ZIP staging | 后端进程内存 | 后端重启或过期后需重新 Preview |

健康探针 `/api/health` 无需身份。管理 API 从 Nginx 同源转发到 FastAPI；`/api/v1/*` 通过 `X-Fake-User` 选择 `admin-local`、`member-local` 或 `admin-other`。页面默认管理员身份，可以在管理壳顶部切换租户和成员角色。MySQL 启动后，`bootstrap` 容器显式应用所有未应用 migration（当前至 V009）并幂等写入 Fake 身份；后端不会在每次启动时自动执行 DDL。

本地员工运行配置入口位于 `/enterprise/workbench` 当前员工旁的“运行配置”；个人/租户全局默认入口位于 `/enterprise/models?tab=runtime-defaults`。EngineProfile 与 ModelRoute 分开选择和保存，改变引擎不会替换模型；Deployment 新修订只影响之后创建的 Session。默认 MySQL/Compose Provider 清单只包含原生 `deepseek_harness`；显式切回 Fake 后只显示模拟 `fake`。ModelRoute 必须指向当前租户可访问的兼容 Endpoint，Worker 才能执行模型请求；Claude Code 尚未注册。

## 启动

首次启动时复制环境模板并生成本地随机密码。不要将 `.env` 密码提交到代码库：

```bash
cp deploy/control-plane/.env.example deploy/control-plane/.env
```

将 `.env` 中两个 `replace-with-...` 替换为随机密码，然后构建并启动：

```bash
docker compose --env-file deploy/control-plane/.env \
  -f deploy/control-plane/docker-compose.yml up --build -d
```

Compose 会等待 MySQL 健康检查，再显式执行所有未应用的 migration（当前至 V009）和 Fake 租户身份 seed，然后启动后端、Run Worker 和前端。backend 与 Worker 使用相同的 `AGENT_PLATFORM_RUNTIME_ENGINE`；DeepSeek profile/provider/model 参数也同步传入，两端不一致时不要开放新 Profile。租户模型 SecretRef 仅映射到 Worker 环境，API 和 Web 容器不接收模型密钥。API 仍以 Fake Identity header 校验当前身份；管理台顶部可切换租户 A 管理员、租户 A 成员和租户 B 管理员。

MySQL 仅绑定到本机 `127.0.0.1:3309`，可用于本机检查 schema 或运行 MySQL 集成测试；应用流量仍通过 Web 容器代理到后端。

Fake seed 只创建身份、租户和角色，不创建员工/Skill/Tool 演示数据，因此全新卷的列表为空。当前运行实例已添加一组可操作的验收数据：租户 A 的“Docker 验收样例”员工及 DRAFT v1、一个已发布 Skill 和一个已发布 Tool；已通过 API 创建/列表/详情/PATCH 验证。请使用“租户 A · 管理员”身份查看和编辑。租户 A 成员为只读，租户 B 与租户 A 数据隔离。

打开管理台：<http://localhost:8088/enterprise/employees>。可先在“数字员工”页面创建员工，再进入详情配置版本；“资源中心”提供租户资源发布、Grant、COPY/REFERENCE；“部署记录”查看部署与审计。健康检查端点为 <http://localhost:8088/api/health>，OpenAPI 为 <http://localhost:8088/docs>。

旧 `/workspace/gallery`、`/enterprise/platform` 地址由 Nginx 直接重定向到 `/enterprise/resources`；根路径 `/` 重定向到 `/enterprise/employees`。若浏览器仍停留在旧页，刷新该标签即可触发重定向。

已验证 Docker Compose 启动 `mysql`、`bootstrap`、`backend`、`worker`、`approval-worker`、`web` 服务；MySQL health check、migration 和身份 seed、后端 health check、Nginx 管理台路由、DeepSeek Provider 清单及带 Fake 身份的 MySQL 读取均成功。DeepSeek Harness 通过本机确定性 ModelRoute 完成的 Run/Trace 验收记录见[验证记录](验证记录.md)。当前浏览器 URL 只绑定 `127.0.0.1`。V009 本地验收已执行备份、preflight、apply 和 postflight；详情见[验证记录](验证记录.md)。

查看容器与日志：

```bash
docker compose --env-file deploy/control-plane/.env \
  -f deploy/control-plane/docker-compose.yml ps
docker compose --env-file deploy/control-plane/.env \
  -f deploy/control-plane/docker-compose.yml logs -f backend web
```

## 停止和重置

停止容器但保留员工、迁移和对象数据：

```bash
docker compose --env-file deploy/control-plane/.env \
  -f deploy/control-plane/docker-compose.yml down
```

彻底重置本地验收数据时，使用 `down -v` 删除 Compose 命名卷；该操作会删除此 Docker 验收栈的全部 MySQL 和对象文件数据：

```bash
docker compose --env-file deploy/control-plane/.env \
  -f deploy/control-plane/docker-compose.yml down -v
```

## 范围

- Fake Runtime 会返回可重复的模拟结果，不调用真实模型或工具执行器。
- Fake Identity 仅用于本机验收，不能公开暴露到网络。
- 对象存储使用 LocalObjectStore；MinIO、S3、OSS、COS 和生产密钥管理仍需单独配置与验证。
- ZIP Preview staging 是后端进程内存状态；若容器重启或预览过期，重新预览 ZIP 后提交。
- 前端生产打包当前有约 1.31 MB 主 JS chunk 的体积提示，但不影响本地验证功能。
- 本地页面仍使用 Fake Identity，不代表真实租户身份；默认 DeepSeek Harness Run 使用租户配置的本地确定性 ModelRoute，证明 SDK/Worker/Run 链路，不代表 DeepSeek 云端模型推理。云对象存储、生产 Secret Manager、TLS、生产网络策略和多实例 staging 协调仍未验证。
