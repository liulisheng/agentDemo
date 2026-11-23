# Spec Delta

## MODIFIED Requirements

### Requirement: Model Management exposes independent scoped engine and model defaults

系统 SHALL 在现有模型管理页增加“默认运行环境”页签，分别管理用户/租户 EngineProfile 默认绑定和 ModelRoute 默认绑定；两个值可以独立设置。

#### Scenario: Open default runtime tab
- **WHEN** 用户在模型管理页面打开“默认运行环境”页签
- **THEN** 系统显示个人/租户的 EngineProfile 默认与 ModelRoute 默认、各自来源和解析优先级；连接和 ModelRoute 目录仍由原有页签管理

#### Scenario: User changes personal engine default
- **WHEN** 用户选择当前租户授权且 ACTIVE 的 ExecutionProfile 作为个人默认并保存
- **THEN** 系统只更新该用户的 EngineProfile 默认；之后创建且继承默认的 Session 使用该引擎配置，ModelRoute 仍按独立作用域解析

#### Scenario: User changes personal model default independently
- **WHEN** 用户保留当前 EngineProfile 默认并独立选择另一条本租户已授权的 ACTIVE ModelRoute
- **THEN** 系统只更新该用户的 ModelRoute 默认；Profile、租户默认和其他用户绑定不变

#### Scenario: Changing the engine does not lock or replace the model
- **WHEN** 用户更换默认 ExecutionProfile 或 Deployment 执行引擎
- **THEN** ModelRoute 选项保持独立且当前选择不变；系统只在新 Session 创建前检查组合兼容性，不自动换模型

#### Scenario: Tenant administrator updates tenant default
- **WHEN** 租户管理员分别保存本租户 EngineProfile 默认和/或 ModelRoute 默认
- **THEN** 系统只更新请求中指定的作用域值；没有更高优先级个人绑定的用户在 INHERIT Deployment 下使用租户值

#### Scenario: Member cannot update tenant default
- **WHEN** 普通成员打开默认运行环境页
- **THEN** 系统允许其查看租户默认并修改个人默认，但不提供租户默认保存入口

#### Scenario: Default references a profile outside the tenant allowlist
- **WHEN** 用户或管理员尝试设置其他租户、未授权、已停用或使用未注册 EngineProvider 的 Profile
- **THEN** 系统拒绝保存并显示稳定的权限或依赖错误，不暴露 SecretRef

#### Scenario: Route default references another tenant or creates an incompatible pair
- **WHEN** 用户或管理员设置其他租户未授权的 ModelRoute，或独立默认解析后与 EngineProvider 不兼容
- **THEN** 跨租户 Route 绑定被拒绝；不兼容组合在创建 Session 时返回兼容性错误，不静默更换模型

#### Scenario: Global default changes
- **WHEN** 个人或租户 EngineProfile 默认或 ModelRoute 默认分别更新成功
- **THEN** 页面显示两个独立值及来源；已有 Session/Run 保持快照不变，新的 INHERIT Session 分别按新默认解析

### Requirement: Model Management manages versioned EngineProfiles

系统 SHALL 允许租户管理员在“默认运行环境”页签创建可复用 ExecutionProfile，并通过稳定 Profile ID 的不可变版本管理 EngineProvider 与环境策略。

#### Scenario: Create a profile from a registered engine
- **WHEN** 租户管理员选择已注册 EngineProvider 创建 ExecutionProfile
- **THEN** 系统创建不可变的 PUBLISHED Profile，`model_alias` 和 ModelRoute 均可为空；用户/租户/Deployment 在独立字段中选择本地模型

#### Scenario: Create a draft version for an existing profile
- **WHEN** 租户管理员对 PUBLISHED Profile 选择“创建新版本”并修改 EngineProvider
- **THEN** 系统创建相同 Profile ID 下递增的 DRAFT 版本；保存 DRAFT 不改变现有默认绑定或 Deployment

#### Scenario: Current Worker has not integrated Profile environment extensions
- **WHEN** 管理员新建、编辑或发布 Profile 时提交 Workspace、Sandbox 或非 `none` 附加审批策略
- **THEN** API 返回 `ENGINE_CAPABILITY_UNSUPPORTED`，不发布一个 Worker 不会应用的 Profile；历史版本必须先清除这些字段再发布新版本

#### Scenario: Publish a revised profile version
- **WHEN** 管理员发布有效的 DRAFT Profile 版本
- **THEN** 系统将该版本设为 PUBLISHED；历史版本保持可读，默认绑定和 Deployment 仍固定旧版本直到显式切换

#### Scenario: Edit a published profile in place
- **WHEN** 用户直接修改 PUBLISHED Profile 的字段
- **THEN** 系统拒绝修改并要求通过“创建新版本”继续

#### Scenario: Select a model independently from a profile
- **WHEN** 用户已选择一个 EngineProfile 并在默认或 Deployment 配置中选择另一个本租户已授权 ModelRoute
- **THEN** 系统保留两项独立选择；若 EngineProvider 不支持该 Route，则在 Session 创建前报告不兼容且不替换模型

#### Scenario: Profile contains recipient credentials
- **WHEN** 系统保存或展示 ExecutionProfile
- **THEN** Profile 仅引用接收方 SecretRef，不保存或返回凭据明文
