# model-management Specification

## Purpose

为租户管理员提供模型连接、连接内多模型目录、模型路由和可用性测试能力，使数字员工可以绑定一个经过验证且当前租户可用的模型路由。

## Requirements

### Requirement: Model connections are tenant-scoped
系统 SHALL 将模型连接归属到一个明确租户，并只允许该租户的授权管理员查看和管理连接。连接配置包含协议、Base URL、凭据引用和状态，不包含凭据明文。

#### Scenario: Tenant administrator creates a connection
- **WHEN** 当前租户管理员创建有效的模型连接
- **THEN** 系统保存连接并将其绑定到当前平台和租户

#### Scenario: Another tenant lists connections
- **WHEN** 其他租户用户查询模型连接
- **THEN** 系统不返回当前租户的连接、模型目录或凭据引用

### Requirement: A connection can expose multiple model catalog entries
系统 SHALL 允许同一租户模型连接关联多个外部模型 ID。目录项来源可以是已认证的远端模型列表或管理员手工录入。

#### Scenario: Synchronize models from a connection
- **WHEN** 管理员同步支持模型目录查询的连接
- **THEN** 系统为每个返回的模型 ID 创建或更新该连接下的目录项，不复制连接凭据

#### Scenario: Add a model when remote discovery is unavailable
- **WHEN** 连接不支持模型目录查询但管理员提供有效模型 ID
- **THEN** 系统允许管理员手工添加该连接下的模型目录项

### Requirement: Model routes bind one tenant model to an employee
系统 SHALL 允许租户管理员为连接下的一个模型创建稳定的 ModelRoute 别名。员工版本只能绑定同租户且处于启用状态的路由。

#### Scenario: Create a route from a catalog item
- **WHEN** 管理员为当前租户连接下的模型创建 ModelRoute
- **THEN** 路由记录连接引用、外部模型 ID、别名和默认参数，并可供同租户员工版本选择

#### Scenario: Bind a model from another tenant
- **WHEN** 管理员尝试将其他租户的模型或连接绑定到当前租户路由
- **THEN** 系统拒绝该绑定并返回租户范围错误

### Requirement: Connection test does not generate model output
系统 SHALL 提供显式连接测试，用连接的 SecretRef 验证认证和目录可达性；连接测试不得通过生成补全来冒充连通性检查。

#### Scenario: Connection authentication succeeds
- **WHEN** 管理员显式测试一个有效连接
- **THEN** 系统返回连接状态、目录发现状态和脱敏错误类别，不返回或记录凭据明文

#### Scenario: Connection requires a credential
- **WHEN** 连接端点拒绝请求或 SecretRef 无法解析
- **THEN** 系统显示认证或凭据解析失败，不将连接标记为可用

### Requirement: Model test performs a bounded real inference
系统 SHALL 提供显式模型测试，通过 Worker 向所选连接的具体模型发送无工具、长度受限的合成请求，并记录结果状态、延迟、请求引用和可用用量。

#### Scenario: Administrator confirms a model test
- **WHEN** 管理员查看测试说明并显式确认模型测试
- **THEN** 系统将一次受限测试交给 Worker 执行，不在 API 请求进程中启动 Runtime

#### Scenario: Model test succeeds
- **WHEN** Worker 收到所选模型的有效响应
- **THEN** 系统显示成功状态、模型 ID、延迟及精确或估算用量，并标明该结果来自测试调用

#### Scenario: Model test fails
- **WHEN** 凭据无效、模型不存在、端点超时或响应格式错误
- **THEN** 系统记录稳定的脱敏错误类别并显示测试失败，不自动重试不确定的请求

### Requirement: Platform authorization filters tenant model routes
系统 SHALL 在模型路由列表和员工版本选择器中仅返回当前租户已启用且当前平台授权策略允许使用的路由。

#### Scenario: User selects an authorized route
- **WHEN** 用户有权配置员工且模型路由对当前租户可用
- **THEN** 员工版本选择器显示该路由的别名和模型 ID

#### Scenario: Route access is revoked before execution
- **WHEN** Worker 执行 Run 前发现该路由已禁用或当前用户不再具有平台侧使用权限
- **THEN** Worker 在模型请求发出前拒绝执行并记录授权错误

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

### Requirement: 默认运行环境页按已确认原型呈现独立设置

系统 SHALL 在模型管理页的“默认运行环境”页签并列呈现作用域默认表单和 ExecutionProfile 版本目录，并分别展示引擎与模型的有效值、来源和保存状态；窄屏下保持该顺序并纵向排列。

#### Scenario: 管理员打开默认运行环境
- **WHEN** 租户管理员打开模型管理的“默认运行环境”页签
- **THEN** 页面显示个人/租户默认、EngineProfile 与 ModelRoute 独立选择、当前解析来源和 Profile 版本目录，不把 ModelRoute 合并进 Profile

#### Scenario: 成员查看全局默认
- **WHEN** 普通成员打开“默认运行环境”页签
- **THEN** 成员可保存自己的引擎和模型默认；租户默认可查看但不可编辑，也不显示租户保存操作

#### Scenario: 目录为空
- **WHEN** 当前租户没有可用 ExecutionProfile 或 ModelRoute
- **THEN** 页面分别显示空状态和对应管理入口，不显示不可提交的伪选项

#### Scenario: 默认目录加载失败
- **WHEN** EngineProvider、Profile、ModelRoute 或默认绑定读取失败
- **THEN** 页面显示可区分的读取错误与重试入口，且不覆盖用户当前未保存的选择

### Requirement: ModelRoute 页提供租户路由管理操作

系统 SHALL 在“模型路由”页签提供可发现的创建入口，并为授权管理员提供创建、编辑和逻辑删除 ModelRoute 的操作；成员只读。

#### Scenario: 管理员从路由页创建路由
- **WHEN** 租户管理员在 ModelRoute 页点击“创建路由”并选择当前租户已启用连接及目录中的可用模型
- **THEN** 系统创建带别名和非敏感参数的路由，并在当前路由表中显示其版本和状态

#### Scenario: 创建时没有可用连接或模型
- **WHEN** 租户没有已启用连接或所选连接没有 ACTIVE 目录模型
- **THEN** 创建表单说明缺少的依赖并提供前往连接/模型目录的入口，不允许提交无效路由

#### Scenario: 管理员编辑路由
- **WHEN** 租户管理员在路由行选择“编辑”并保存有效的别名、目录模型或非敏感参数
- **THEN** 系统保存新路由版本，刷新路由表并继续固定已经创建的 RunSnapshot

#### Scenario: 管理员删除路由
- **WHEN** 租户管理员在路由行确认“删除”
- **THEN** 系统按逻辑删除处理并显示已删除状态；删除失败时保留原路由并显示依赖冲突

#### Scenario: 成员打开路由页
- **WHEN** 普通租户成员打开 ModelRoute 页
- **THEN** 成员可以查看连接和路由信息，但不能创建、编辑、停用、启用或删除路由

### Requirement: Runtime strategy is configured independently from engine and model
系统 SHALL 在默认运行环境和 Deployment 运行配置中独立呈现回答策略，不把思考模式、推理强度、答复详细程度或预算字段混入 EngineProfile 或 ModelRoute。

#### Scenario: Administrator configures answer strategy
- **WHEN** 管理员打开默认运行环境或员工 Deployment 配置
- **THEN** 页面显示思考模式、推理强度、答复详细程度和推理预算，并明确配置只影响新 Session

#### Scenario: Member views strategy configuration
- **WHEN** 普通成员打开运行配置
- **THEN** 成员可以查看有效回答策略和来源，但不能修改租户默认或 Deployment 覆盖

#### Scenario: Strategy fields are not yet supported by the API
- **WHEN** 后端尚未注册对应 EngineProvider capability
- **THEN** 页面将策略字段标记为方案或不可用状态，不伪装保存成功

### Requirement: Strategy compatibility is visible before Session creation
系统 SHALL 在保存或创建 Session 前校验回答策略与 EngineProvider/ModelRoute capability，并向用户显示可定位的兼容性结果。

#### Scenario: Deep mode is compatible
- **WHEN** 所选引擎和模型均支持深度思考及所需预算
- **THEN** 页面显示可用并允许保存，Session 创建时固定该策略

#### Scenario: Deep mode is incompatible
- **WHEN** 所选引擎或模型不支持深度思考
- **THEN** 页面显示不兼容原因并阻止创建 Session，不自动换模型或降级策略

### Requirement: Independent runtime strategy panel shows effective values and overrides
系统 SHALL 提供可复用的独立运行策略面板，分别展示当前生效策略、来源、本次覆盖差异、EngineProvider capability 和仅影响新 Session 的说明；面板不得把回答策略字段写入 EngineProfile 或 ModelRoute。

#### Scenario: Administrator opens Deployment strategy panel
- **WHEN** 租户管理员从员工 Deployment 打开运行配置
- **THEN** 系统以双栏管理台布局展示思考模式、推理强度、答复详细程度、推理预算、当前生效值、来源和覆盖差异

#### Scenario: Member opens strategy panel
- **WHEN** 普通成员查看同一运行配置
- **THEN** 系统展示有效策略和来源但隐藏保存及新建配置操作，不返回 Secret 值

#### Scenario: Unsupported capability is selected
- **WHEN** 用户选择当前 EngineProvider 或 ModelRoute 不支持的深度思考或推理预算
- **THEN** 面板标记不支持原因并阻止保存，不静默降级或切换模型

#### Scenario: Strategy is saved with a new revision
- **WHEN** 管理员保存有效策略并确认只影响新 Session
- **THEN** 系统创建下一配置修订并返回解析后的策略、来源和可新建 Session 的上下文，旧 Session 保持原快照
