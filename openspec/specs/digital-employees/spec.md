# digital-employees Specification

## Purpose

为平台提供可配置、可发布、可部署的数字员工模板，并让每次运行绑定明确的不可变版本以支持审计、回滚和多业务线复用。

## Requirements

### Requirement: Digital employee has a stable identity
系统 SHALL 为数字员工维护稳定标识、名称、描述、归属租户、负责人、状态和可管理标签。

#### Scenario: Create employee draft
- **WHEN** 有权限的用户创建数字员工
- **THEN** 系统创建租户范围内唯一的草稿对象并返回稳定 id

### Requirement: Published version is immutable

系统 SHALL 将员工系统指令、Skill/Tool 精确版本和能力策略固化为不可变数字员工版本；ExecutionProfile、ModelRoute、Workspace/Sandbox 和运行审批配置由接收租户的 Deployment 单独管理。

#### Scenario: Publish valid version
- **WHEN** 有效员工草稿的 Skill、Tool 和资源中心依赖通过校验，且不包含发布方 ExecutionProfile、ModelRoute 或 Sandbox
- **THEN** 系统创建 PUBLISHED 员工版本，且运行环境由接收租户 Deployment 单独管理

#### Scenario: Edit published version
- **WHEN** 用户尝试直接修改已发布员工版本的指令或能力绑定
- **THEN** 系统拒绝修改并要求创建新的草稿版本

### Requirement: Deployment pins a published version

系统 SHALL 通过 Deployment 固定一个已发布员工版本和当前租户运行配置修订；配置修订必须引用接收方当前平台/租户可用的运行资源。

#### Scenario: Deploy employee
- **WHEN** 管理员把已发布版本部署到租户
- **THEN** 系统创建指向该精确员工版本的 `PENDING_CONFIGURATION` Deployment；运行环境配置前不能创建 Session/Run

#### Scenario: Roll back deployment
- **WHEN** 管理员选择较早的已发布版本回滚
- **THEN** 后续新 Session 使用回滚版本和该 Deployment 当前租户运行配置；已开始 Run 保留其原有员工与运行快照

### Requirement: Publish validates dependencies

系统 SHALL 在发布前校验员工版本引用的 Skill、Tool 和资源中心依赖有效且允许在目标范围使用；发布不要求发布方配置接收租户专属的 ExecutionProfile、ModelRoute、Workspace 或 Sandbox。

#### Scenario: Missing dependency
- **WHEN** 版本引用的 Tool 或 Skill 不存在、未发布或已被撤销
- **THEN** 发布失败并返回依赖校验结果

### Requirement: Digital employee composes executable skills
系统 SHALL 允许数字员工版本组合多个已发布 SkillVersion，并为每个版本固化 Skill 顺序、触发规则、输入映射和输出处理策略。

#### Scenario: Run selects a skill
- **WHEN** 用户消息匹配数字员工版本中的 Skill 触发规则
- **THEN** 系统创建 SkillExecution，并将该 Skill 的指令、Schema 和允许工具加入本次 Run 配置

#### Scenario: No skill matches
- **WHEN** 用户消息未匹配任何显式 Skill 规则且 Agent 没有默认 Skill
- **THEN** 系统不调用 Skill 工具流程，并返回可解释的未匹配结果或交给 Agent 默认处理策略

### Requirement: Run records the assembled agent snapshot

系统 SHALL 在 Run 开始前固定员工版本、能力依赖和 Session 使用的 Deployment 运行配置修订及精确资源版本。

#### Scenario: Agent version changes during run
- **WHEN** 管理员在 Run 执行期间发布新的数字员工版本
- **THEN** 当前 Run 继续使用原员工与运行配置快照，后续新 Session 才解析新的员工版本

### Requirement: Deployment pins a recipient runtime configuration revision

系统 SHALL 将 Deployment 的运行模式（继承默认或员工专属覆盖）作为不可变修订保存，并在 Session 创建时解析和固定最终 EngineProfile、ModelRoute 与解析来源。

#### Scenario: Save first employee-specific override
- **WHEN** 接收租户管理员为 `PENDING_CONFIGURATION` Deployment 选择 `OVERRIDE` 并提交有效的本地 ExecutionProfile
- **THEN** 系统创建修订 1 并激活 Deployment；该员工配置优先于用户、租户及平台默认值

#### Scenario: Inherit the creator's personal default
- **WHEN** Deployment 处于 `INHERIT` 模式且创建 Session 的用户有 ACTIVE 个人 ExecutionProfile 默认绑定
- **THEN** 系统使用该用户默认 Profile 中的引擎策略；ModelRoute 按独立默认链解析，并分别记录来源 `USER_DEFAULT`

#### Scenario: Fall back to the tenant default
- **WHEN** Deployment 处于 `INHERIT` 模式且 Session 创建者没有可用个人默认，但当前租户有 ACTIVE 默认 Profile
- **THEN** 系统使用租户默认引擎策略，ModelRoute 仍独立解析并记录各自的实际来源

#### Scenario: User default does not override employee-specific profile
- **WHEN** Session 创建者有个人默认 Profile，但目标 Deployment 已固定另一个 `OVERRIDE` Profile
- **THEN** Session 使用 Deployment Profile，不因创建者个人偏好改变员工专属引擎或模型

#### Scenario: No default profile resolves
- **WHEN** Deployment 处于 `INHERIT` 且用户、租户、平台/系统范围均没有可用 Profile
- **THEN** 系统返回 `RUNTIME_CONFIGURATION_REQUIRED`（409），不创建 Session 或 Run

#### Scenario: Update global default
- **WHEN** 管理员更新用户或租户默认 Profile
- **THEN** 之后创建的 INHERIT Session 使用新默认；已有 Session 和 Run 保留原解析快照

#### Scenario: Inherit engine and model from different scopes
- **WHEN** Deployment 为 `INHERIT`，创建者有个人 EngineProfile 默认但没有个人 ModelRoute 默认，租户有兼容的 ModelRoute 默认
- **THEN** Session 使用个人 EngineProfile 与租户 ModelRoute，并分别记录 `USER_DEFAULT` 和 `TENANT_DEFAULT` 来源

#### Scenario: Model remains independently selectable after engine selection
- **WHEN** 管理员在默认运行环境或 Deployment 表单中选择一个 ExecutionProfile 后再选择已授权 ModelRoute
- **THEN** 系统保留管理员选择的 ModelRoute，不因引擎选择而自动填入、锁定或切换模型

#### Scenario: Override only the employee ModelRoute
- **WHEN** Deployment 处于 `OVERRIDE` 且只设置了 ModelRoute 覆盖，没有设置 ExecutionProfile 覆盖
- **THEN** Session 使用作用域默认解析的 EngineProfile 与 Deployment 固定的 ModelRoute

#### Scenario: Inherited ModelRoute is incompatible with resolved engine
- **WHEN** 用户/租户 ModelRoute 默认与 Deployment 或作用域解析出的 EngineProvider 不兼容
- **THEN** 系统返回 `ENGINE_MODEL_INCOMPATIBLE`，不静默更换模型，也不创建 Session
