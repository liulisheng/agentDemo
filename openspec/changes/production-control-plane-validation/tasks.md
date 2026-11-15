# Tasks

## 0. 项目级 SQL Schema 约定

- [x] 0.1 将可执行 SQL 固定在 docs/migrations/，并要求后续新建或变更表/字段使用中文 MySQL COMMENT；同步 AGENTS.md、docs/文档维护约定.md 和 openspec/config.yaml，验证三处规则一致且 OpenSpec 严格校验通过。

## 1. Migration 基线与 MySQL schema

- [x] 1.1 增加只读 migration preflight，读取 schema_version 和 migration checksum 并报告 V003 是否可首次应用；验证空库、V001/V002 库、V003 已应用库分别选择 V003、V003 或 forward-only V004，应用启动不执行 DDL。
- [x] 1.2 在唯一可执行 SQL 目录 `docs/migrations/` 完成控制面 migration，覆盖 Skill/Tool/Skill 声明 Tool 的版本关系、员工能力绑定、ExecutionProfile、CapabilityBinding、Conversation/AgentBinding、Platform Session/Run 快照、Trace/Usage 和 MediaAsset 元数据；复用 V001/V002 的 sessions、runs、messages、message_chunks、upload intent、GC 表，不创建重复的 Session/Run 主表；验证所有外键和索引包含 platform_id + tenant_id。
- [x] 1.3 为本 Change 新增/变更的每张表和每个字段添加中文 MySQL COMMENT；增加 MySQL 集成检查查询 information_schema.TABLES/COLUMNS，验证对象备注非空且包含中文字符，V001/V002 文件保持不变。
- [x] 1.4 实现显式 migration runner 的版本、checksum、锁、失败停止和只读状态查询；验证重复执行已应用迁移无副作用、checksum 改变时拒绝继续、部分失败不会被标记成功。
- [x] 1.5 更新中文迁移说明和回滚说明，列出 SQL 目录约定、V001/V002 不可修改、V003/V004 选择规则、备份前提和部署命令；验证命令可在一次性 MySQL 8.4 实例复现。

## 2. 目录与部署 MySQL Repository

- [x] 2.1 定义 backend-owned CatalogRepository/DeploymentRepository 端口及 MySQL 实现，覆盖员工、不可变版本、Skill、Tool、ExecutionProfile、ModelRoute 和 Deployment；为映射 V001/V002 CHAR(26) 的 Session/Run/Message ID 使用 26 字符不透明生成器；验证服务层不导入 Harness persistence 类型且 DB 往返不截断 ID。
- [x] 2.2 将租户范围 CRUD、草稿更新/删除、发布依赖校验、部署和回滚接入 MySQL；验证 Skill/Tool/Profile/ModelRoute/Workspace/Sandbox 缺失或跨租户时发布失败，已发布版本不能修改。
- [x] 2.3 将 /tools、/skills、/digital-employees、/execution-profiles、/model-routes 和 /deployments API 接到 MySQL-backed services；验证现有响应结构、body 伪造 tenant 拒绝、跨租户资源按 404 返回。
- [x] 2.4 增加目录重启集成测试：创建并发布 Skill/Tool/员工版本，重启应用后读取相同数据并执行部署回滚；验证旧 RunSnapshot 保持原版本。

## 3. Session、Run、Trace 与 Usage 持久化

- [x] 3.1 实现 Platform Session/Run Repository，复用 V001 sessions/runs 并持久化 Deployment、DigitalEmployeeVersion、ExecutionProfile、ModelRoute、ContextSnapshot、trace_id 和 opaque Runtime references；验证运行记录可跨进程查询。
- [x] 3.2 实现 MySQL 幂等提交和 lease/fencing 状态转换；验证相同 key+digest 返回原 Run、不同 digest 冲突、旧 fencing token 的 heartbeat/event/finish 写入影响 0 行。
- [x] 3.3 将 TraceSpan、ModelCall、ToolInvocation、UsageRecord 和 UsageAdjustment 接入租户范围 Repository；验证完成用量不可覆盖、调整记录保留原记录、分页顺序稳定。
- [x] 3.4 将 Run 状态、Trace/Usage 索引和结果引用按设计事务边界提交；注入每个写入阶段的数据库故障，验证不会出现可查询的伪成功 Run。
- [x] 3.5 验证 Worker 丢失和 Runtime 结果未知：lease 过期后状态变为 UNKNOWN，旧 Worker 被 fencing，未验证 Runtime resume 的情况下不自动重放执行。

## 4. LocalObjectStore 持久化与 GC

- [x] 4.1 将 LocalObjectStore 改为可跨进程恢复：MySQL 保存 MediaAsset、tenant scope、哈希、大小、retention、加密 key version；本地文件只使用服务端生成 key 并位于 web root 之外；验证服务重启后授权读取成功且任意客户端路径不可用。
- [x] 4.2 实现显式加密策略：密钥只通过 SecretProvider/SecretRef 注入，密钥版本随对象元数据保存；缺少密钥时拒绝要求加密的 payload，Raw Payload 默认关闭；验证密钥不进入 MySQL 普通字段、日志或 API 响应。
- [x] 4.3 按 upload intent -> 临时文件写入/fsync -> 原子发布 -> MySQL 引用提交的顺序完成上传；为 V002 CHAR(26) upload/cleanup ID 使用 26 字符不透明生成器；验证大小、摘要、租户、intent lease、DB 提交失败、对象写入失败和 ID 往返场景。
- [x] 4.4 实现 MySQL-backed delete/GC worker：删除先撤销读取并入队，unlink 幂等可重试；扫描保护有效 Message/chunk/Run result/Runtime refs 和上传 intent；验证失败重试、迟到 PUT、已删对象重现后重新入队。
- [x] 4.5 验证消息正文/Runtime 结果/Raw Payload 保存与读取：MySQL 只保存引用和元数据，Raw Payload policy 关闭时不写正文对象；跨租户读取/删除均按不存在处理。

## 5. 应用装配与持久化 E2E

- [x] 5.1 为 API/Worker 增加显式 persistence 配置：MySQL 未配置时只允许测试或显式 Fake mode；MySQL 配置失败时应用启动/请求失败，不静默切回内存仓储；添加显式、幂等的本地 Fake identity seed 命令/fixture，API 启动不得暗中创建平台、租户或用户行。
- [x] 5.2 将 Application Services 和 LocalObjectStore 通过 app factory 注入 MySQL 实现；验证 dependency wiring、连接池关闭和事务边界，不在 domain/API 路由中直接创建数据库连接。
- [x] 5.3 增加完整 API E2E：创建/发布 Tool 与 Skill、创建/发布员工、部署、创建 Session/Run、Fake Runtime 执行、写入 Message/Trace/Usage，重启服务后查询结果；验证 idempotency、tenant isolation 和 result reference 全链路。
- [x] 5.4 在一次性 MySQL 8.4 + LocalObjectStore 环境运行迁移、完整后端测试和故障注入；记录 MySQL 版本、schema checksum、测试命令和日志位置，验证离线 Fake 测试仍能独立运行。

## 6. 文档、运维与验收

- [x] 6.1 更新中文架构图、Session/Run/对象上传/删除时序、数据模型和状态图，覆盖 MySQL、LocalObjectStore、Worker lease/fencing、GC 与重启恢复；验证文档链接到对应模块和测试。
- [x] 6.2 更新本地启动、migration preflight、apply、backup/restore、加密 key 配置和故障恢复 runbook；验证敏感密钥不进入仓库且按文档可启动一次性环境。
- [x] 6.3 更新外部适配器矩阵：LocalObjectStore 标记已验证，MinIO/S3/OSS/COS 保持未验证且 provider 缺失时显式失败；验证文档不把 Fake 或 Local 测试描述成云端/生产验收。
- [x] 6.4 完成发布前审计：后端 pytest/Ruff/compileall、Harness Engine 回归、前端测试/build、OpenSpec strict validation、迁移/回滚演练；记录通过、跳过和未验证项。

## Workflow follow-up

- 管理台页面作为后续独立 Change，在 MySQL-backed 目录 API 的请求/响应和重启语义稳定后启动。
- Session/聊天工作台页面在 Message、Run、Trace 跨进程持久化验收后启动。
- MinIO/S3/OSS/COS provider adapters、真实 Runtime、Dadi、IM 和 Kubernetes Sandbox 通过各自独立 Change 接入。
