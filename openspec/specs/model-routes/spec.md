# model-routes Specification

## Purpose

为平台提供统一的模型路由和连接配置，使当前的直接模型接入能够平滑切换到 new-api 或 Dadi Model Gateway，并让 Run 记录实际使用的模型来源和用量。

## Requirements

### Requirement: Model route is provider-neutral
系统 SHALL 以模型路由、模型别名、协议入口和认证引用描述模型接入，不要求平台为每家供应商维护独立兼容性矩阵。

#### Scenario: Configure direct model route
- **WHEN** 管理员配置有效的直接模型入口和 Secret 引用
- **THEN** 系统创建可绑定到 ExecutionProfile 的模型路由

### Requirement: Model gateway adapters are replaceable
系统 SHALL 允许通过适配器使用直接模型、new-api 或 Dadi Model Gateway，业务领域对象不因网关替换而改变。

#### Scenario: Switch gateway adapter
- **WHEN** 租户将模型路由从直接入口切换到 new-api
- **THEN** 新 Run 使用新适配器，已开始的 Run 保留原模型路由快照

### Requirement: Model call returns normalized usage
系统 SHALL 在模型供应商或网关返回用量时记录输入 Token、输出 Token、总 Token、请求引用和费用信息；无法获得精确值时必须标记为估算。

#### Scenario: Gateway returns usage
- **WHEN** new-api 返回 provider request id 和 usage
- **THEN** 平台保存标准化 ModelCallUsage 并关联 Run、Trace 和模型路由

#### Scenario: Gateway omits usage
- **WHEN** 模型响应没有精确 usage
- **THEN** 平台可以保存估算值，但必须标记 usage_status 为 ESTIMATED

### Requirement: Secret is not part of model configuration payload
系统 SHALL 只在模型路由中保存 Secret 引用，不将 API Key 写入数据库、日志、版本快照或用户可见响应。

#### Scenario: Secret reference resolution
- **WHEN** Worker 启动模型调用
- **THEN** Worker 通过受控 SecretProvider 解析凭据并只在进程作用域内使用

### Requirement: Model route binds to one model in a tenant connection
系统 SHALL 以连接引用和外部模型 ID 标识 ModelRoute；连接、目录模型和路由必须属于同一平台与租户。

#### Scenario: One connection provides multiple models
- **WHEN** 管理员从同一连接目录创建多个 ModelRoute
- **THEN** 每个路由引用各自的模型 ID，并可使用同一连接的协议、endpoint 和 SecretRef

#### Scenario: Route references another tenant's connection
- **WHEN** 管理员尝试把其他平台或租户的连接模型绑定到当前路由
- **THEN** 系统拒绝创建或更新该路由

### Requirement: Run snapshot pins the complete model route configuration
系统 SHALL 在 Run 创建时固定路由版本、连接版本、模型 ID、provider、protocol、endpoint、参数和 SecretRef；快照不得包含凭据明文。

#### Scenario: Worker executes a queued Run after route configuration changes
- **WHEN** Worker 执行 Run 时，连接或 ModelRoute 已发布了新版本
- **THEN** Worker 仍使用该 Run 快照中的模型配置和凭据引用

#### Scenario: Administrator rotates a model credential
- **WHEN** 管理员更新连接凭据引用并创建新的连接版本
- **THEN** 后续 Run 使用新版本，已创建 Run 保留原 SecretRef 版本且不暴露 Secret 明文

### Requirement: ModelRoute defaults are independent scope bindings

系统 SHALL 将 ModelRoute 的用户/租户默认作为独立作用域绑定解析；改变 EngineProfile 默认不得自动替换、锁定或缩减 ModelRoute 选择。

#### Scenario: Resolve user model default independently
- **WHEN** Deployment 处于 `INHERIT`，Session 创建者有个人 ModelRoute 默认，EngineProfile 则来自租户默认
- **THEN** Session 使用个人 ModelRoute 和租户 EngineProfile，并分别记录 Route 与 Profile 的来源

#### Scenario: Deployment overrides only the model
- **WHEN** Deployment `OVERRIDE` 修订只设置 ModelRoute，不设置 ExecutionProfile
- **THEN** Session 使用作用域解析出的 EngineProfile 和 Deployment 显式 ModelRoute

#### Scenario: Changing engine default does not change route binding
- **WHEN** 用户或管理员更新个人/租户 EngineProfile 默认
- **THEN** ModelRoute 绑定保持不变；之后的 Session 独立解析两个值并执行兼容性检查

#### Scenario: Incompatible scoped defaults are rejected
- **WHEN** 独立解析的 ModelRoute 不受最终 EngineProvider 支持
- **THEN** Session 创建失败并返回 `ENGINE_MODEL_INCOMPATIBLE`，不静默替换 ModelRoute

### Requirement: ModelRoute must be compatible with the selected EngineProvider

系统 SHALL 在 Session 创建前验证独立选择的 ModelRoute、执行引擎和请求能力兼容，并把用户实际选择的路由版本固定在 Session/Run 快照；不兼容时拒绝运行且不得静默切换路由。

#### Scenario: Select a route independently after selecting an engine
- **WHEN** 用户已选择一个 ExecutionProfile，并从 ModelRoute 字段单独选择本租户已授权的 ACTIVE 路由
- **THEN** 系统保留两项选择，ExecutionProfile 不会自动改变 ModelRoute

#### Scenario: Legacy Profile has no independent Route default
- **WHEN** 迁移后的旧 Profile 只保留历史 `model_alias`，且没有任何独立 ModelRoute 默认
- **THEN** 系统可在兼容期间用旧 alias 作为 fallback 并标记 `LEGACY_PROFILE` 来源；用户显式选择 ModelRoute 后始终以该 Route 为准

#### Scenario: Deployment supplies a compatible route override
- **WHEN** Deployment OVERRIDE 提供与 EngineProvider 能力匹配的本租户 ModelRoute
- **THEN** 新 Session 固定该路由；之后发布的路由配置不改变该 Session/Run 快照

#### Scenario: ModelRoute protocol is unsupported by EngineProvider
- **WHEN** 用户或管理员绑定的路由协议/Provider 不在选定 EngineProvider 支持范围内
- **THEN** 系统返回 `ENGINE_MODEL_INCOMPATIBLE`，不创建 Session 或 Run，也不切换到另一个模型

#### Scenario: Route belongs to another tenant
- **WHEN** Deployment 配置引用其他租户未授权的 ModelRoute 或连接
- **THEN** 系统按资源不存在/未授权拒绝，且不泄露发布方 endpoint 或 SecretRef

#### Scenario: Model route credential is resolved
- **WHEN** Worker 根据 RunSnapshot 执行支持的 ModelRoute
- **THEN** Worker 只用接收方已授权的 SecretRef 向 SecretProvider 解析凭据，不将明文写入 Snapshot、Trace、日志或 API 响应

### Requirement: ModelRoute updates pin a new route version without changing existing snapshots

系统 SHALL 为租户管理员提供 ModelRoute 编辑能力；有效编辑递增当前路由版本，新 Session 使用新版本，已创建 Session/RunSnapshot 保持原路由快照。

#### Scenario: Edit an active route
- **WHEN** 当前租户管理员基于最新路由版本保存别名、允许的连接目录模型或非敏感参数变更
- **THEN** 系统递增 `route_version`，后续 Session 解析新路由；已有 RunSnapshot 的模型、连接和凭据引用不变

#### Scenario: Edit using a stale route version
- **WHEN** 管理员基于已被其他管理员更新的路由版本提交编辑
- **THEN** API 返回稳定的版本冲突，不覆盖较新的路由配置

#### Scenario: Member edits a route
- **WHEN** 当前租户普通成员尝试编辑路由
- **THEN** API 返回 403，路由和版本保持不变

### Requirement: ModelRoute logical deletion preserves references and historical execution

系统 SHALL 将管理员删除操作持久化为 `REVOKED` 终态，不物理删除路由行或历史快照；REVOKED 路由不得用于新的默认绑定、Deployment 覆盖、Session 或 Run。

#### Scenario: Revoke an unreferenced route
- **WHEN** 租户管理员删除未被可运行配置引用的路由
- **THEN** 路由状态变为 `REVOKED`，路由行保留以满足审计和历史外键，且该路由从后续可选目录中移除

#### Scenario: Revoke a route referenced by a default or deployment
- **WHEN** 路由仍被作用域默认、当前 Deployment 修订，或新 Session 可消费的已发布 Profile 历史路由字段引用
- **THEN** API 返回 `MODEL_ROUTE_IN_USE`（409）和当前租户内的引用说明，不改变路由状态

#### Scenario: Revoke a route with historical RunSnapshots
- **WHEN** 路由已被历史 Session/RunSnapshot 固定，但没有当前可运行配置引用
- **THEN** 系统允许路由进入 `REVOKED`；历史 Run/Trace 仍可读取快照中的路由与模型信息

#### Scenario: Repeat deletion of a revoked route
- **WHEN** 管理员再次删除已为 `REVOKED` 的同一路由
- **THEN** API 返回当前 REVOKED 状态且不重复产生状态变更审计

#### Scenario: Member or another tenant revokes a route
- **WHEN** 普通成员删除路由，或其他租户访问该 route ID
- **THEN** 前者收到 403，后者按资源不存在返回 404；均不改变路由状态或泄露引用信息
