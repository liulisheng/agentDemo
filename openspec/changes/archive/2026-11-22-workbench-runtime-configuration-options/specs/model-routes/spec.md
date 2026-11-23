# Spec Delta

## MODIFIED Requirements

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
