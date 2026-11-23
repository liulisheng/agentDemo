# Spec Delta

## MODIFIED Requirements

### Requirement: Conversation sessions inherit the Deployment runtime configuration

系统 SHALL 在 Conversation 创建 Session 时分别解析 Deployment/User/Tenant EngineProfile 与 ModelRoute 配置，并将解析结果和来源固定到该 Session 的后续 Run。

#### Scenario: Start a conversation with configured Deployment
- **WHEN** 用户为 `ACTIVE` 且已配置的 Deployment 新建 Conversation/Session
- **THEN** Session 固定该 Deployment 当前运行配置修订，工作台显示其 Profile、ModelRoute 和配置修订摘要

#### Scenario: Inherit the Session creator's user default
- **WHEN** 用户为 `INHERIT` Deployment 创建 Conversation/Session 且用户与租户都有默认 Profile
- **THEN** Session 使用创建者的用户默认 Profile，并显示 `USER_DEFAULT` 来源

#### Scenario: Resolve engine and model from separate scopes
- **WHEN** Session 创建者的个人 EngineProfile 默认存在，但个人 ModelRoute 默认未设置且租户配置了 ModelRoute 默认
- **THEN** Session 使用用户 EngineProfile 与租户 ModelRoute，分别显示两个解析来源

#### Scenario: Shared conversation does not switch to another participant's default
- **WHEN** 另一位参与者向已创建的 Conversation 提交新 Run，且其个人默认引擎与 Session Profile 不同
- **THEN** 新 Run 继续使用该 Session 固定的引擎和模型，不按提交人的个人默认重新解析

#### Scenario: Deployment configuration changes after Session creation
- **WHEN** 管理员为 Deployment 保存新的运行配置修订
- **THEN** 已有 Conversation/Session 和 Run 保持旧修订，新建 Conversation/Session 使用新修订

#### Scenario: Global default changes after Session creation
- **WHEN** 用户或管理员更换用户/租户 EngineProfile 默认或 ModelRoute 默认
- **THEN** 后续新 Session 分别按新作用域解析，已有 Session/Run 继续使用创建时固定的 Profile、ModelRoute 和两个来源

#### Scenario: User expects new configuration in an existing conversation
- **WHEN** 用户在配置更新后继续提交到旧 Session 的 Conversation
- **THEN** 工作台明确提示该对话仍使用旧修订，并提供创建新 Conversation 的入口；系统不得静默改变该 Session 的运行环境

#### Scenario: Deployment still needs configuration
- **WHEN** 用户尝试为 `PENDING_CONFIGURATION` Deployment 创建 Conversation/Session
- **THEN** 工作台展示“请先配置运行环境”并链接到有权限的配置入口，不保存空白会话或用户消息

#### Scenario: Member cannot edit Deployment configuration
- **WHEN** 普通运行成员查看对话运行信息
- **THEN** 系统允许其查看该 Session 使用的配置修订摘要，但不展示 SecretRef 明文或配置保存操作

#### Scenario: No profile can be resolved for an inherited Deployment
- **WHEN** 用户为 INHERIT Deployment 创建 Session 但所有可用作用域都没有 ACTIVE ExecutionProfile
- **THEN** 系统返回 `RUNTIME_CONFIGURATION_REQUIRED` 且不创建空 Session 或 Conversation
