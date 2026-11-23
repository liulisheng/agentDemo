# Spec Delta

## MODIFIED Requirements

### Requirement: Scoped EngineProfile defaults persist and resolve after restart

系统 SHALL 将用户、租户和平台默认 ExecutionProfile 绑定持久化到既有 `execution_profile_bindings`，并在进程重启后按作用域读取相同绑定。

#### Scenario: Persist a user default
- **WHEN** 用户设置自己的 ACTIVE ExecutionProfile 默认并重启 API
- **THEN** 系统恢复相同的 user-scope binding 和精确 Profile 版本

#### Scenario: Persist a tenant default
- **WHEN** 租户管理员设置租户 ACTIVE ExecutionProfile 默认并重启 API
- **THEN** 系统恢复相同的 tenant-scope binding，且该值只影响对应租户

#### Scenario: Resolve defaults with the existing scope key
- **WHEN** 系统查询一个 Session 的默认 Profile
- **THEN** 它使用现有 `(platform_id, tenant_id, scope_type, scope_id)` 唯一作用域键，不创建重复绑定表或跨租户默认引用

#### Scenario: Binding references a profile from another tenant
- **WHEN** 持久化 binding 引用当前租户无权使用的 Profile
- **THEN** Repository 通过租户复合键拒绝写入且不改变已有默认绑定

### Requirement: Scoped ModelRoute defaults persist independently

系统 SHALL 使用租户隔离的 `model_route_bindings` 持久化用户、租户和平台范围的默认 ModelRoute，不与 EngineProfile 默认绑定混为同一值。

#### Scenario: Persist personal and tenant routes across restart
- **WHEN** 用户设置个人 ModelRoute 默认且管理员设置租户 ModelRoute 默认后 API 重启
- **THEN** 系统恢复各自的作用域绑定和精确 route_id，不相互覆盖

#### Scenario: Enforce one route binding per scope
- **WHEN** 同一 platform/tenant/scope 再次更新 ModelRoute 默认
- **THEN** 系统更新该作用域的单一绑定并记录审计，不创建重复的 active default 行

#### Scenario: Reject cross-tenant route binding
- **WHEN** Repository 保存不属于当前 platform/tenant 或未经授权的 route_id
- **THEN** 数据库复合外键或应用授权校验拒绝事务，原默认绑定保持不变

#### Scenario: Verify ModelRoute binding comments
- **WHEN** V009 在 MySQL 8.4 应用成功
- **THEN** `information_schema.TABLES` 与 `information_schema.COLUMNS` 返回 `model_route_bindings` 表和每个字段的非空中文 COMMENT

### Requirement: New ExecutionProfiles do not require a model alias

系统 SHALL 将 `execution_profiles.model_alias` 改为可空的历史兼容字段；新 ModelRoute 由独立绑定持久化，Worker 未消费的 Workspace/Sandbox/审批字段只为历史读取保留且 API 拒绝新非空值。

#### Scenario: Persist a profile without a model alias
- **WHEN** V009 后管理员创建只指定 EngineProvider 的 ExecutionProfile
- **THEN** Repository 保存空 `model_alias`，新 Session 从独立 ModelRoute 解析模型；旧 Profile alias 值保持原样

#### Scenario: Reject a Profile with worker-unwired environment fields
- **WHEN** 管理员创建、更新或发布包含 Workspace、Sandbox 或非 `none` 附加审批策略的 Profile
- **THEN** API 返回 `ENGINE_CAPABILITY_UNSUPPORTED` 且不发布或改写 Profile；历史草稿需先清除这些字段

#### Scenario: Verify the altered schema comment
- **WHEN** V009 在 MySQL 8.4 执行成功
- **THEN** `information_schema.COLUMNS` 显示 `execution_profiles.model_alias` 可空且 COMMENT 为 `历史兼容模型别名；新 Session 模型由独立 ModelRoute 或运行时默认解析`

### Requirement: Deployment runtime configuration revisions persist immutably

系统 SHALL 以平台、租户、Deployment 和递增修订持久化运行配置；每个修订固定精确 Profile、ModelRoute 和历史兼容 Workspace/Sandbox/审批字段，不能被后续配置覆盖。Worker 未接通的字段只为迁移兼容保留，API 不得接受新的非空覆盖并使其看似生效。

#### Scenario: Persist a configuration revision across restart
- **WHEN** 接收租户保存 Deployment 运行配置修订并重启 API
- **THEN** 系统恢复相同修订、资源引用、创建者和当前 Deployment 配置指针

#### Scenario: Existing deployments are migrated
- **WHEN** V009 前向迁移处理现有 ACTIVE/DISABLED Deployment
- **THEN** 系统以其原固定员工版本的现有运行配置创建修订 1 并关联 Deployment；可由当前 Worker 消费的 EngineProfile/ModelRoute 保持原行为

#### Scenario: Backfilled ModelRoute snapshot is readable by the new resolver
- **WHEN** V009 为历史 Deployment 回填 ModelRoute 快照
- **THEN** JSON 字段符合当前 ModelRouteSnapshot 契约，保留精确租户、连接/路由版本和受控 SecretRef；从回填 Deployment 创建的新 Session 可恢复同一快照

#### Scenario: Do not silently apply an unsupported historical override
- **WHEN** 历史 Deployment 的 Workspace/Sandbox/附加审批值与精确 Profile 不一致或不能由当前 Worker 执行
- **THEN** Session 创建在 Runtime 出站前返回 `ENGINE_CAPABILITY_UNSUPPORTED`，不忽略字段后继续运行

#### Scenario: New Deployment has no configuration
- **WHEN** 系统创建没有可复制本地配置的 Deployment
- **THEN** Deployment 保存为 `PENDING_CONFIGURATION` 且当前配置修订为空；状态不能伪装为可执行

#### Scenario: Runtime configuration uses composite tenant references
- **WHEN** 系统保存配置修订
- **THEN** 数据库使用 platform_id、tenant_id、deployment_id、config_version 复合键，并通过租户复合外键拒绝跨租户 Profile/Route/Workspace/Sandbox 引用

#### Scenario: Concurrent revision writes
- **WHEN** 两个事务使用同一当前修订写入配置
- **THEN** 数据库事务和期望修订校验只允许一个新修订提交，不产生重复编号或悬空 Deployment 指针

#### Scenario: Inspect schema comments
- **WHEN** V009 在 MySQL 8.4 成功执行
- **THEN** `information_schema.TABLES` 与 `information_schema.COLUMNS` 能查询新增配置表、变更表和每个新增/变更字段的非空中文 COMMENT
