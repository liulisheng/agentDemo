# Proposal

## Why

当前平台 API 的目录、Deployment、Session、Run、Trace 和模型路由主要由进程内内存仓储支撑，服务重启会丢失控制面状态；V003 目前只有迁移设计，尚未由 backend Repository 实际读写。需要先建立可在本地复现的 MySQL 持久化和 LocalObjectStore 链路，为管理功能和后续真实 Runtime 提供稳定的数据基础。

## What Changes

- 将数字员工、版本、Skill、Tool、ExecutionProfile、ModelRoute、Deployment、Conversation、Session、Run、Trace 和 Usage 的应用服务接入 MySQL Repository。
- 把 V003 迁移落成可重复执行和可核对版本的部署迁移；所有新表和查询继续使用 `platform_id + tenant_id` 复合边界。
- 将可执行 schema migration 统一放在独立的 `docs/migrations/` SQL 目录；本 Change 新增或变更的表和字段均使用中文 MySQL `COMMENT` 说明用途，并通过 `information_schema` 校验。
- 将现有创建、发布、部署、回滚、Session/Run 和 Trace API 接到持久化仓储，保持 Fake Runtime 可离线执行。
- 将消息正文、Runtime 结果和可选 Raw Payload 通过 LocalObjectStore 保存；MySQL 只保存对象引用、哈希、大小、状态和保留元数据。
- 验证幂等键、事务回滚、Worker lease/fencing、服务重启恢复、租户隔离、对象上传 intent、删除和 GC 重试。
- 保持 ObjectStore provider-neutral 接口，当前只要求本地存储可运行；MinIO、S3、OSS、COS 和其他云端存储适配器留待后续 Change。
- 为持久化后的目录 API 和运行 API 提供稳定契约，作为后续数字员工管理台与聊天工作台的基础。

## Capabilities

### New Capabilities

- `control-plane-persistence`: 将目录、部署、Session、Run、Trace 和 Usage 状态可靠地持久化到 MySQL，并定义租户边界、幂等、租约和恢复行为。
- `local-object-content-storage`: 使用 LocalObjectStore 持久化消息正文、Runtime 结果和策略允许的 payload，同时维护对象引用、上传 intent、租户授权、删除和 GC 语义。

### Modified Capabilities

无。当前 `openspec/specs/` 没有正式的主规格；本 Change 通过新增能力规格建立持久化行为契约。

## Impact

- 影响 `backend/src/agent_platform/application/`、`infrastructure/`、`api/`、`workers/`、MySQL Repository 与 ObjectStore adapter。
- 扩展 `docs/migrations/` 和 `deploy/mysql/`，明确本地迁移、启动、校验和回滚流程。
- 影响现有数字员工、Skill、Tool、Deployment、Session、Run、Conversation、Trace、Usage API 的数据来源，但尽量保持请求/响应结构稳定。
- 增加 backend MySQL 和 LocalObjectStore 集成测试，离线 Fake 测试仍可独立运行。
- 本 Change 不实现管理台页面；管理功能页面在持久化 API 契约稳定后单独推进。当前不接入 MinIO/S3/OSS/COS、真实模型网关、真实 DeepSeek/Codex Runtime、Dadi、IM 或生产 Kubernetes 沙箱。
