# platform-api Specification

## Purpose

为管理端和未来前端提供稳定的 HTTP 控制面，使平台资源能够按平台、租户、用户范围进行配置、发布、部署和查询。

## Requirements

### Requirement: Management API exposes platform resources
系统 SHALL 通过版本化 HTTP API 管理数字员工、技能、工具、执行配置、模型路由、部署、Conversation、Session、Run 和审计资源。

#### Scenario: List tenant resources
- **WHEN** 已认证用户查询当前租户的数字员工
- **THEN** API 只返回该用户有权访问的当前 platform_id 和 tenant_id 范围内资源

#### Scenario: Unknown resource
- **WHEN** 用户请求不存在或不属于当前租户的资源
- **THEN** API 返回统一的 not found 结果，不泄露资源是否存在于其他租户

### Requirement: API uses explicit authorization context
系统 SHALL 从已验证身份或 IdentityProvider 生成 platform、tenant、user 和授权上下文，不信任请求体中的身份字段作为授权依据。

#### Scenario: Body attempts to switch tenant
- **WHEN** 请求体携带与认证上下文不同的 tenant_id
- **THEN** API 拒绝请求并记录脱敏授权事件

#### Scenario: Fake identity context
- **WHEN** 当前环境启用 Fake IdentityProvider
- **THEN** API 使用 Fake 数据生成与生产相同结构的授权上下文

### Requirement: API errors are stable and actionable
系统 SHALL 对参数错误、未认证、无权限、资源冲突、依赖未发布和执行能力不可用返回稳定错误类别及可关联的 request_id。

#### Scenario: Publish dependency failure
- **WHEN** 发布请求引用不存在或未发布的 Skill 或 Tool
- **THEN** API 返回 dependency validation error，并列出可安全展示的缺失引用

### Requirement: Mutating operations are idempotent where applicable
系统 SHALL 对创建 Run、接收外部消息、发布版本和渠道投递等可能重试的操作支持显式幂等键。

#### Scenario: Retry with same payload
- **WHEN** 客户端使用相同幂等键和相同请求摘要重试
- **THEN** API 返回原操作结果且不创建重复资源或重复执行

#### Scenario: Reuse key with changed payload
- **WHEN** 相同幂等键对应不同请求摘要
- **THEN** API 返回 idempotency conflict 并拒绝第二次操作

### Requirement: User and tenant can manage scoped runtime defaults

系统 SHALL 提供作用域化 ExecutionProfile 默认值 API；profile 解析结果包括来源范围，且只接受认证上下文可授权的 Profile。

#### Scenario: Read effective profile default
- **WHEN** 用户请求当前运行默认值
- **THEN** API 按用户、租户、平台/系统顺序返回第一个 ACTIVE Profile 及解析来源，不返回凭据明文

#### Scenario: User changes personal default
- **WHEN** 用户为自己的作用域提交租户授权的 ACTIVE ExecutionProfile
- **THEN** API 保存用户默认绑定并记录审计；该默认只影响之后由该用户创建的 Session

#### Scenario: Tenant administrator changes tenant default
- **WHEN** 租户管理员提交本租户授权的 ACTIVE ExecutionProfile 作为租户默认
- **THEN** API 保存租户默认绑定并记录审计；没有用户默认的后续 Session 使用此配置

#### Scenario: Member tries to change tenant default
- **WHEN** 普通成员提交租户级默认值更新
- **THEN** API 返回 403 且不修改任何作用域绑定

#### Scenario: Binding points to unauthorized profile
- **WHEN** 请求绑定其他租户、未授权、已停用或不存在的 ExecutionProfile
- **THEN** API 拒绝更新，按 404/依赖错误处理且不泄露跨租户资源信息

#### Scenario: No effective profile exists
- **WHEN** 用户创建 Session 时 Deployment 为 `INHERIT` 且所有允许的作用域均无 ACTIVE 默认
- **THEN** API 返回 `RUNTIME_CONFIGURATION_REQUIRED`（409），不创建 Session、Conversation 消息或 Run

### Requirement: ModelRoute defaults are managed independently from EngineProfile

系统 SHALL 为用户和租户分别提供 ModelRoute 默认绑定 API；更新 Route 默认不得隐式修改 ExecutionProfile 默认。

#### Scenario: User changes personal ModelRoute default
- **WHEN** 用户为自己的作用域绑定一个 ACTIVE 且当前租户授权的 ModelRoute
- **THEN** API 只更新该用户的 `model_route_bindings`，不修改用户 EngineProfile 绑定或租户默认

#### Scenario: Tenant administrator changes tenant ModelRoute default
- **WHEN** 租户管理员为当前租户设置 ModelRoute 默认
- **THEN** API 保存租户级 Route 绑定并记录审计，没有个人 Route 默认的新 Session 才继承该值

#### Scenario: Member attempts to change tenant ModelRoute default
- **WHEN** 普通成员提交租户级 ModelRoute 默认修改
- **THEN** API 返回 403 且保留原绑定

#### Scenario: Route default belongs to another tenant
- **WHEN** 请求绑定其他租户未授权的 ModelRoute
- **THEN** API 按资源不存在/未授权拒绝，不泄露连接或 SecretRef

#### Scenario: Resolved Route is incompatible with the selected engine
- **WHEN** Session 创建时解析出的默认 ModelRoute 不受当前 EngineProvider 支持
- **THEN** API 返回 `ENGINE_MODEL_INCOMPATIBLE` 且不创建 Session 或 Run

### Requirement: Deployment runtime configuration is tenant managed

系统 SHALL 提供 Deployment 运行配置读取和版本化保存接口；保存接口只接受认证租户可使用的运行资源，并且不得返回凭据明文。

#### Scenario: Read current Deployment configuration
- **WHEN** 已授权用户读取当前租户 Deployment 的运行配置
- **THEN** API 返回当前修订、Profile/Route/Workspace/Sandbox 的精确资源引用及配置状态，不返回 Secret 值

#### Scenario: Save first valid runtime configuration
- **WHEN** 租户管理员为 `PENDING_CONFIGURATION` Deployment 提交有效配置和当前修订期望值
- **THEN** API 创建配置修订 1、写入审计并将 Deployment 置为 `ACTIVE`

#### Scenario: Update an active Deployment configuration
- **WHEN** 租户管理员以匹配的当前修订提交有效更新
- **THEN** API 原子创建下一不可变修订、更新 Deployment 当前修订指针并写入审计

#### Scenario: Member attempts to change configuration
- **WHEN** 无配置管理权限的租户成员提交运行配置变更
- **THEN** API 返回 403，且不创建修订或改变 Deployment 状态

#### Scenario: Configuration references another tenant
- **WHEN** 请求引用其他租户或无平台授权的 Profile、ModelRoute、Workspace 或 Sandbox
- **THEN** API 返回统一的资源不存在/未授权错误且不泄露跨租户资源信息

#### Scenario: Runtime resource is inactive or incompatible
- **WHEN** 请求引用已撤销、未发布或当前租户无权使用的运行资源
- **THEN** API 拒绝保存并返回可安全展示的依赖校验错误，Deployment 保持原配置

#### Scenario: Save independent engine and model values
- **WHEN** 管理员分别提交有效的 ExecutionProfile 和 ModelRoute，即使当前组合可能不兼容
- **THEN** API 保留两项独立选择，不自动替换 ModelRoute；Session 创建时验证组合，不兼容则拒绝启动

#### Scenario: Reject Deployment environment overrides not consumed by Worker
- **WHEN** 管理员提交非空 Deployment Workspace/Sandbox 覆盖或附加 `require_approval_for` 策略
- **THEN** API 返回 `ENGINE_CAPABILITY_UNSUPPORTED`（409），不创建运行配置修订；历史迁移字段只有在与固定 ExecutionProfile 一致时才允许创建 Session

#### Scenario: Concurrent configuration update
- **WHEN** 两个管理员基于同一旧修订并发保存
- **THEN** 只有一个保存成功，另一个收到稳定的配置修订冲突且不能覆盖新修订

### Requirement: Unconfigured Deployment rejects new execution

系统 SHALL 在新建 Session 或 Run 前验证 Deployment 为 `ACTIVE` 且拥有当前运行配置修订；缺配置时不得创建部分 Message、Session 或 Run。

#### Scenario: Create a Session for a pending Deployment
- **WHEN** 调用方为 `PENDING_CONFIGURATION` Deployment 创建 Session
- **THEN** API 返回 `RUNTIME_CONFIGURATION_REQUIRED`（409），且不创建 Session

#### Scenario: Submit a Run through an existing pinned Session
- **WHEN** 调用方从已固定运行配置修订的有效 Session 提交 Run
- **THEN** API 使用该 Session 的配置修订创建 RunSnapshot，不读取 Deployment 后续“最新”配置覆盖既有 Session

### Requirement: ModelRoute lifecycle API enforces version and reference boundaries

系统 SHALL 提供经认证租户上下文校验的 ModelRoute 创建、更新和逻辑删除接口；更新需比较期望版本，删除需原子检查可运行引用并审计，响应不得包含凭据明文。

#### Scenario: Update a route at the current version
- **WHEN** 当前租户管理员提交带匹配 `expected_route_version` 的有效路由更新
- **THEN** API 保存路由并返回递增后的 `route_version`，已固定的 RunSnapshot 不变

#### Scenario: Update with a stale route version
- **WHEN** 请求基于过期 `expected_route_version` 更新路由
- **THEN** API 返回 `MODEL_ROUTE_VERSION_CONFLICT`（409），且不覆盖最新路由

#### Scenario: Delete an unreferenced route
- **WHEN** 当前租户管理员删除未被可运行配置引用的路由
- **THEN** API 原子将路由状态改为 `REVOKED`、写入一次审计并返回保留的路由摘要，不执行物理删除

#### Scenario: Delete a referenced route
- **WHEN** 路由仍被作用域默认、当前 Deployment 修订或可供新 Session 使用的已发布 Profile 历史字段引用
- **THEN** API 返回 `MODEL_ROUTE_IN_USE`（409）及同租户内可安全展示的依赖摘要，不改变路由或引用

#### Scenario: Delete an already revoked route
- **WHEN** 管理员重复删除已撤销的路由
- **THEN** API 返回当前 `REVOKED` 状态，不重复递增版本或写入删除审计

#### Scenario: Member or cross-tenant deletion
- **WHEN** 普通成员删除路由，或请求引用另一个租户的 route ID
- **THEN** API 分别返回 403 或统一 404，不泄露跨租户资源与依赖信息

#### Scenario: Revoke races with a new runtime binding
- **WHEN** 路由删除与默认值或 Deployment 绑定并发提交
- **THEN** 路由行锁及同一事务使操作串行化；只能一个操作成功，另一个得到 `MODEL_ROUTE_IN_USE` 或稳定的配置冲突

### Requirement: Runtime configuration APIs expose typed answer strategy
系统 SHALL 通过默认运行环境和 Deployment 运行配置 API 读写受白名单约束的回答策略字段，并按当前认证租户和作用域返回来源。

#### Scenario: Save a valid strategy revision
- **WHEN** 管理员提交有效的思考模式、推理强度、预算、答复详细程度、输出/工具/超时限制和期望配置版本
- **THEN** API 创建下一不可变修订并返回策略摘要、配置版本和来源，不返回凭据明文

#### Scenario: Reject unknown strategy fields
- **WHEN** 请求包含未在 RuntimeOptions 白名单中的字段或非法范围
- **THEN** API 返回稳定参数错误，不修改已有修订

#### Scenario: Concurrent strategy update
- **WHEN** 管理员使用过期 `expected_config_version` 保存策略
- **THEN** API 返回配置修订冲突，保留当前修订和当前 Deployment 指针

### Requirement: Session and Run APIs return fixed strategy snapshots
系统 SHALL 在 Session 和 RunSnapshot 响应中返回实际生效的回答策略、来源和配置修订，且后续默认值变化不改变历史快照。

#### Scenario: Create a Session with deep thinking
- **WHEN** Session 创建请求解析出兼容的深度思考策略
- **THEN** API 返回固定策略和来源；后续 Run 使用同一 Session 策略

#### Scenario: Submit a per-run strategy override
- **WHEN** 工作台提交允许范围内的本轮回答策略覆盖
- **THEN** API 将覆盖写入该 RunSnapshot，不修改 Session 或 Deployment 默认

#### Scenario: Strategy is incompatible
- **WHEN** Session 或 Run 的策略与 EngineProvider/ModelRoute 不兼容
- **THEN** API 在 Runtime 出站前返回能力错误，不创建伪成功 Run

### Requirement: Strategy panel context is returned as one auditable view
系统 SHALL 提供策略面板所需的组合视图，包含 Deployment 修订、有效 RuntimeOptions、每个字段来源、EngineProvider capability、ModelRoute 摘要和新 Session 可用性；读取和保存均按认证 platform、tenant、user 上下文执行。

#### Scenario: Read panel context
- **WHEN** 已授权用户打开当前租户的运行策略面板
- **THEN** API 返回当前修订、有效策略、字段来源、能力摘要和不兼容原因，不返回 Secret 明文

#### Scenario: Save with stale revision
- **WHEN** 管理员使用过期 `expected_config_version` 保存策略
- **THEN** API 返回 `RUNTIME_CONFIG_REVISION_CONFLICT`，不覆盖其他管理员的新修订

#### Scenario: Cross-tenant panel request
- **WHEN** 用户使用其他租户的 Deployment 或 ModelRoute 标识读取/保存面板
- **THEN** API 按资源不存在或授权拒绝处理，不泄露资源存在性

### Requirement: Real model session launch uses the saved strategy snapshot
系统 SHALL 允许策略面板在保存成功后请求新 Conversation/Session，并只使用服务端解析的 EngineProfile、ModelRoute、RuntimeOptions 和配置修订创建 Run。

#### Scenario: Launch a new session from panel
- **WHEN** 管理员点击“保存并新建对话”且策略和 ModelRoute 均兼容
- **THEN** API 创建带固定策略快照的 Session/Conversation，并返回可提交 turn 的上下文

#### Scenario: Existing conversation remains pinned
- **WHEN** 用户在策略修订后继续发送旧 Conversation 消息
- **THEN** API 继续使用旧 Session/RunSnapshot，不将新策略静默注入旧对话
