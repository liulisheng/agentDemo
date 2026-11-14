# 方案评审记录

评审日期：2026-11-09  
评审范围：`design.md`、`implementation-plan.md`、`sandbox-design.md`、`decisions.md`  
结论：**有条件通过接口冻结阶段，暂不具备直接进入生产实现的条件。**

## 结论摘要

整体方向成立：用统一的 Python 引擎协议封装官方 DeepSeek Harness，用 entry points 和 MCP 做扩展，当前服务器阶段采用 Docker/Kubernetes 承载受控任务，控制面使用 MySQL/InnoDB 保存租户和运行元数据；microVM 后端延后评估。

当前方案最需要关注的是三件事：MySQL 不提供本方案所依赖的自动租户行过滤，因此所有仓储查询都必须带认证得到的 `platform_id + tenant_id`；用户中心身份和 Harness 任务授权之间还缺少冻结后的协议；Docker/Kubernetes 沙箱的安全策略和官方 Harness 的真实启动、网络、恢复、MCP 调用链还没有经过验证。

## Findings

### P0：MySQL 租户隔离完全依赖应用代码

当前 MySQL 设计没有数据库级 RLS。任何遗漏 `tenant_id` 的查询都有可能读到其他租户数据，遗漏写入条件还可能修改或删除其他租户记录。

参考 `D:\GitHub\dadi\dadi-user-center` 后，身份边界还应包含 `platform_id`。平台用于区分平台实例和平台级管理权限，tenant_id 用于 Agent 数据隔离，user_id 用于记录操作人。默认用户绑定一个租户，跨租户访问通过显式授权关系表达；组织、岗位、用户组、客户端绑定和服务端会话不能被简单的 membership 表替代。

实施时必须把租户上下文做成仓储层的强制输入，禁止业务代码直接拿裸连接拼 SQL；每个租户表都要有复合索引，关键关系使用包含 `platform_id + tenant_id` 的复合外键或一致性校验。验收必须包含跨平台、跨租户读取、更新、删除、列表、取消任务和产物下载。

### P0：容器沙箱方案还不能被当作生产安全边界

Docker/Kubernetes 运行时策略、网络代理、凭据代理、对象存储和节点回收器都只是设计项。当前没有证明容器内的 Harness、MCP 工具、工作区恢复和产物导出能在该边界内正常工作，也没有证明节点失联时不会留下可执行的孤儿容器或 Pod。Kubernetes namespace 不能单独作为不可信代码的安全边界。

进入生产前必须完成单容器/单 Pod 链路、节点重启、孤儿回收、网络绕过、凭据撤销和对抗测试。没有满足容器安全策略的隔离后端时，调度器必须拒绝不可信任务。

### P1：官方 Harness 适配层缺少版本锁定和可替换的进程边界

文档要求使用官方 Python SDK，但尚未给出 SDK/runtime 的准确版本、Python 版本支持矩阵、启动参数、取消行为和 MCP 配置格式。若这些接口随上游变化，业务层会被动受影响。

实施前应建立上游兼容性记录：固定 SDK/runtime 版本，封装所有原生类型，验证启动、通知、取消、恢复、审批和关闭；官方 SDK 进程应由引擎适配器独占管理，业务层不能直接持有原生客户端。

### P1：用户中心到 Harness 的认证协议还没有冻结

文档已经确定 Harness 不负责密码和 OIDC Provider，但没有冻结 token 的 issuer、audience、JWKS 来源、租户声明、角色声明、撤销检查和长任务能力令牌格式。只验证 JWT 签名和过期时间不足以处理用户被禁用、租户暂停或角色撤销。

进入实现前需要确定：浏览器会话由用户中心管理；Harness API 接受哪一种服务端会话或访问令牌；平台和租户如何从服务端目录解析；跨服务调用如何防止伪造普通 `X-*` 请求头；长任务如何使用短期能力令牌和租约。

### P1：平台级和租户级外键边界还需落实

只在单列上建立 `tenant_id` 外键，不能保证它与 `platform_id` 属于同一平台。用户、组织、岗位、会话、运行和审计表应使用平台加租户的联合键或等价一致性校验，防止把平台 A 的用户关联到平台 B 的租户。

### P1：会话状态和任务幂等规则还不够具体

文档提到租约、fencing token 和“不自动重放”，但没有定义运行状态、租约过期、节点失联和重复请求的最终状态转换。

至少应固定：业务幂等键的唯一范围、租约持有者、心跳间隔、过期判断、取消后的状态、未知结果状态，以及哪些操作允许用户显式重试。数据库状态变更必须幂等，旧 worker 带 fencing token 的更新必须被拒绝。

### P1：数据存储边界需要落到 schema 级别

控制面元数据进 MySQL，Harness 历史和工作区进隔离持久存储，这个方向正确，但还缺少对象引用格式、租户/会话前缀、删除策略、备份恢复和加密密钥归属。

实施前应定义 MySQL 表和对象存储的最小字段，明确软删除或保留期、租户删除的级联行为、恢复点，以及对象下载必须再次校验租户和会话归属。

### P1：schema migration 已准备但尚未真实执行

`mysql-schema.md` 已冻结字段、键和状态，`docs/migrations/V001__core_schema.sql` 已落成可执行 migration，但尚未在真实 MySQL 8.4 上验证联合外键、CHECK、SKIP LOCKED 和连接池事务边界。Docker Engine 可用后应先执行 migration 和验收矩阵，再进入仓储实现。

### P2：官方 SDK 仍是预发布版本

本地已验证 `deepseek-harness-sdk==0.2.1rc2` 可以初始化、启动和关闭，但尚未验证真实 prompt、恢复、审批、MCP 和 Docker/Kubernetes 容器链路。该版本不能作为生产兼容性承诺，必须在实现前锁定版本和回归矩阵。

### P1：迁移材料已补齐，但尚未在数据库上执行

已新增 `docs/migrations/V001__core_schema.sql` 和 [真实验证矩阵](verification-matrix.md)。它们把联合外键、状态 CHECK、幂等键和运行表落成可执行材料；当前机器没有可连接的 MySQL 8.4，因此迁移正确性仍是待验证状态。

### P2：本地基础版和服务器版边界清楚，但共享接口还需冻结

文档正确地禁止把本地 SDK/CLI 直接开放给不可信租户。建议在实现前冻结 `HarnessEngine`、`SandboxProvider`、`SessionRef`、`RunResult` 和事件类型的版本，服务器控制面只依赖这些接口，不把 Docker/Kubernetes 或官方 SDK 类型泄露到业务层。

## 通过项

- 引擎、模型和工具插件被分开，替换引擎不会强制修改业务层。
- Fake 引擎和真实引擎契约测试的方向合理。
- 不自动重放未知结果、默认拒绝高风险审批、独立 Harness home 等安全默认值合理。
- 当前先采用 Docker/Kubernetes，但已明确普通容器不能自动视为不可信任务的最终安全边界。
- 已明确不把 API Key、完整 prompt、工具参数写入数据库或日志。

## 用户与租户模型

用户和租户必须分开建模。`users` 表表示平台内的用户及默认租户，`tenants` 表表示隔离边界，`tenant_memberships` 表只表示经授权的额外跨租户访问。会话、运行、产物和审计记录归属平台和租户；审计记录另外保存操作人 `actor_id`。

## 进入实现前的准入条件

1. 固定并验证官方 SDK/runtime 版本及实际接口。
2. 冻结 MySQL schema 中的 platform/tenant 联合外键、用户默认租户和跨租户授权。
3. 冻结 `TenantContext(platform_id, tenant_id, user_id, roles, auth_source)` 和用户中心到 Harness 的认证协议。
4. 明确定义运行状态、租约、fencing token 和幂等规则。
5. 完成单 Docker 容器和单 Kubernetes Pod 的 Harness/MCP/恢复/产物链路验证。
6. 通过跨租户越权、网络绕过、凭据撤销、资源耗尽和孤儿回收测试。

## 最终建议

可以继续进入“接口和 schema 设计”阶段，但不要直接进入服务器生产实现。优先实现 MySQL 仓储边界和 Fake 引擎契约，同时并行做官方 Harness 的最小真实运行验证；沙箱调度器在隔离后端验证通过前保持拒绝调度状态。
