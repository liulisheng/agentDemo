# identity-context Specification

## Purpose

为平台提供统一的身份、平台、租户、用户和授权上下文，当前使用 Fake 数据，后续可以替换为 Dadi User Center 而不改变业务服务契约。

## Requirements

### Requirement: Identity context is tenant scoped
系统 SHALL 在每个管理请求和 Run 中携带经过验证的 platform_id、tenant_id、user_id、角色、Scope 和认证来源。

#### Scenario: Resolve fake identity
- **WHEN** 本地环境使用 Fake IdentityProvider 处理请求
- **THEN** 系统返回与 Dadi 语义一致的 IdentityContext

### Requirement: Fake provider mirrors production fields
系统 SHALL 让 Fake provider 支持 Platform、Tenant、User、状态、角色、Scope 和 membership 数据，以覆盖正常和拒绝路径。

#### Scenario: Disabled user
- **WHEN** Fake 数据中的用户状态为 DISABLED
- **THEN** 新的管理操作和 Run 提交被拒绝

### Requirement: Authorization is explicit
系统 SHALL 根据 IdentityContext 和资源范围判断操作权限，不使用普通请求体字段或未验证请求头改变身份。

#### Scenario: Forged tenant header
- **WHEN** 请求携带与 Fake 身份不一致的 tenant_id
- **THEN** 系统拒绝请求并生成脱敏授权事件

### Requirement: Identity provider is replaceable
系统 SHALL 通过统一端口接入 Fake、Dadi User Center 或未来其他身份服务，并保持领域层使用相同的身份和授权语义。

#### Scenario: Switch to Dadi adapter
- **WHEN** 部署配置将 IdentityProvider 从 Fake 切换到 Dadi
- **THEN** 数字员工、Conversation、Session 和 Run 服务继续使用同一 IdentityContext 契约
