# Spec Delta

## MODIFIED Requirements

### Requirement: 员工版本编辑保持不可变性

系统 SHALL 允许租户管理员为未归档员工创建和编辑 DRAFT 版本。员工版本只编辑系统指令和 Skill/Tool 能力绑定；运行 Profile、ModelRoute、Workspace/Sandbox 与运行审批策略属于 Deployment 配置，不进入员工版本。

#### Scenario: 编辑现有草稿
- **WHEN** 管理员打开并保存未归档员工的 DRAFT 版本
- **THEN** 系统只更新指令或能力绑定并保留精确 Skill/Tool 依赖；请求和界面不提供运行 Profile、ModelRoute、Workspace/Sandbox 或运行审批字段

#### Scenario: 基于已发布或已下线版本创建新草稿
- **WHEN** 管理员显式从 PUBLISHED 或 DEPRECATED 员工版本创建草稿
- **THEN** 系统在事务内分配新版本号并复制系统指令、精确 Skill/Tool 绑定及顺序；不复制任何租户运行配置

#### Scenario: 尝试修改只读版本
- **WHEN** 管理员提交对 PUBLISHED 或 DEPRECATED 员工版本的修改
- **THEN** 系统拒绝修改并返回 `IMMUTABLE_VERSION`

#### Scenario: 基于已发布版本定向添加资源
- **WHEN** 管理员从已发布版本的 Skills 或 Tools Tab 发起添加操作
- **THEN** 系统创建继承指令和全部现有 Skill/Tool 绑定的新 DRAFT，再执行对应绑定；源版本及其 Deployment 配置保持不变

### Requirement: 发布前验证员工版本依赖

系统 SHALL 在发布员工版本前验证 Skill、Tool 和 Hub 引用的发布状态、租户授权及版本摘要；发布员工内容不要求绑定运行 Profile、ModelRoute、Workspace 或 Sandbox。

#### Scenario: 所有依赖有效
- **WHEN** 草稿中的 Skill、Tool 和 Hub 依赖均已发布、授权有效且摘要匹配
- **THEN** 系统允许发布员工版本，即使当前租户尚未创建运行 Deployment 或配置模型/沙箱

#### Scenario: 依赖缺失或 Hub 授权失效
- **WHEN** 草稿引用未发布依赖、无权 Hub 资源或不匹配摘要
- **THEN** 系统阻止发布并返回逐项可定位的依赖错误

### Requirement: 新建 Deployment 和回滚只能选择已发布版本

系统 SHALL 只允许管理员在新建 Deployment 或回滚时选择 PUBLISHED 员工版本；新 Deployment 在接收方运行配置保存前处于 `PENDING_CONFIGURATION`，已配置的 Deployment 在切换员工版本时保留本地运行配置修订。

#### Scenario: 回滚 Deployment
- **WHEN** 管理员把 Deployment 回滚到员工旧的 PUBLISHED 版本
- **THEN** 系统更新员工版本指针、保留接收方当前运行配置并记录审计；已有 Run 继续保留创建时的版本快照

#### Scenario: 回滚 Deployment 到非 PUBLISHED 版本
- **WHEN** 管理员选择 DRAFT 或 DEPRECATED 的员工版本作为回滚目标
- **THEN** 系统返回 `DEPENDENCY_VALIDATION_ERROR`（409），部署保留当前员工版本和配置

### Requirement: 员工工作台按资源类型独立分页

系统 SHALL 在员工详情提供概览、每个已启用资源类型的独立 Tab、版本与发布 Tab；Deployment 运行配置在工作台独立管理，不与员工版本内容表单混合。

#### Scenario: 切换 Skill/Tool 资源 Tab
- **WHEN** 员工版本同时绑定多个 Skill 和 Tool
- **THEN** 员工详情分别显示 Skills、Tools Tab，各自只显示该类型资源并保持精确版本和对应操作

#### Scenario: 编辑员工版本
- **WHEN** 管理员打开 DRAFT 员工版本编辑器
- **THEN** 系统指令、Skills 和 Tools 位于独立配置区，不显示 Profile、ModelRoute、Workspace/Sandbox 或运行审批配置

#### Scenario: 打开 Deployment 运行配置
- **WHEN** 管理员在员工工作台打开一个 Deployment
- **THEN** 页面独立显示该租户的运行配置状态、当前修订、可用配置入口和错误反馈

### Requirement: 新 Deployment 必须先配置接收方运行环境

系统 SHALL 在接收方 Deployment 选择 `INHERIT` 或 `OVERRIDE` 运行模式前阻止其创建新 Session 或 Run。

#### Scenario: 创建新的 Deployment
- **WHEN** 管理员为已发布员工版本新建 Deployment
- **THEN** 系统创建 `PENDING_CONFIGURATION` Deployment，运行工作台显示配置入口并拒绝新建 Session/Run

### Requirement: Deployment 工作台配置接收方运行环境

系统 SHALL 在现有 `/enterprise/workbench` 当前员工旁提供轻量配置入口，让接收租户管理员为 Deployment 继承或分别覆盖 EngineProfile、ModelRoute。

#### Scenario: Deployment inherits scope defaults
- **WHEN** 管理员将 Deployment 设为 `INHERIT`
- **THEN** 工作台显示当前 Session 创建者将解析到的用户/租户/平台默认来源，并保存新的不可变配置修订

#### Scenario: Administrator sets an employee-specific profile
- **WHEN** 管理员将 Deployment 设为 `OVERRIDE` 并选择兼容的本地 ExecutionProfile/ModelRoute
- **THEN** 系统保存新的不可变修订；该员工引擎选择优先于用户和租户默认值

#### Scenario: Override only the engine
- **WHEN** 管理员为 Deployment 固定 EngineProfile 且将 ModelRoute 设置为“继承默认”
- **THEN** 新 Session 使用员工 EngineProfile 和创建者作用域解析的 ModelRoute，并分别记录来源

#### Scenario: Override only the ModelRoute
- **WHEN** 管理员为 Deployment 固定 ModelRoute 且将 EngineProfile 设置为“继承默认”
- **THEN** 新 Session 使用创建者作用域解析的 EngineProfile 和员工 ModelRoute override

#### Scenario: Changing the Deployment engine leaves its model selection alone
- **WHEN** 管理员在 Deployment 表单中更换 ExecutionProfile
- **THEN** 当前 ModelRoute 选择保持不变；系统不自动改写模型，后续 Session 创建前单独校验组合兼容性

#### Scenario: Different employees use different engines
- **WHEN** 租户管理员分别给员工 A、B 选择不同的已注册 EngineProvider/Profile
- **THEN** 两个 Deployment 各自显示其 Engine/Model 配置，且一个 Deployment 的配置变化不影响另一个

#### Scenario: 普通成员查看但不能编辑
- **WHEN** 有运行权限的普通成员打开 Deployment 运行配置
- **THEN** 系统展示当前配置摘要但不提供保存入口；提交配置变更返回 403

#### Scenario: 配置资源属于其他租户
- **WHEN** 管理员提交不属于当前 platform/tenant 且未获平台授权的 Profile 或 ModelRoute
- **THEN** 系统返回统一资源不存在/未授权错误且不保存配置修订

#### Scenario: 编辑现有 Deployment 配置
- **WHEN** 管理员基于当前修订提交有效的新配置
- **THEN** 系统创建下一修订并审计变更；已有 Session/Run 保留旧修订，新 Session 使用新修订

#### Scenario: Unsupported engine is not selectable
- **WHEN** 当前平台尚未注册或验证某个 EngineProvider adapter
- **THEN** 工作台不把该引擎显示为可用选项，已有员工 Deployment 继续使用原配置

### Requirement: Global runtime defaults are managed under Model Management

系统 SHALL 在“模型管理 > 默认运行环境”提供用户和租户 EngineProfile 与 ModelRoute 默认设置；用户默认与租户默认独立保存，不与员工 EmployeeVersion 内容混合。

#### Scenario: User sets a personal default
- **WHEN** 用户在默认运行环境页选择自己有权使用的 ACTIVE ExecutionProfile 和/或 ModelRoute
- **THEN** 系统分别保存用户作用域默认值，并提示其仅影响之后创建且继承默认的 Session

#### Scenario: Tenant administrator sets the tenant default
- **WHEN** 租户管理员在默认运行环境页选择本租户可用的 ACTIVE ExecutionProfile 和/或 ModelRoute
- **THEN** 系统分别保存租户默认值；没有更高优先级用户默认的后续 INHERIT Session 使用此值

#### Scenario: Member cannot edit the tenant default
- **WHEN** 普通租户成员打开默认运行环境页
- **THEN** 系统允许其查看租户默认并管理个人默认，但不提供租户默认保存入口

#### Scenario: Default profile changes
- **WHEN** 用户或租户管理员更新作用域默认 Profile 或 ModelRoute
- **THEN** 页面显示新的 Engine/Model 和来源；已有 Session 不变，新的 INHERIT Session 使用新默认
