# execution-engines Specification

## Purpose

为平台提供可替换的 Agent 执行引擎边界，使 DeepSeek Harness、Codex app-server、Codex exec、其他 CLI 和 Fake 引擎能够由平台配置选择并共享 Session、Run、事件、取消和错误语义。

## Requirements

### Requirement: Engine exposes a common lifecycle
系统 SHALL 为每个执行引擎提供启动、创建 Session、恢复 Session、执行 Run、取消和关闭的统一语义。

#### Scenario: Start and run engine
- **WHEN** Worker 根据已解析的 ExecutionProfile 启动引擎并提交 Run
- **THEN** 平台创建可关联的 Session 和 Run，并接收统一结果或事件

### Requirement: Engine selection follows profile policy
系统 SHALL 根据平台、租户、用户范围的 ExecutionProfile 选择引擎，优先级为用户、租户、平台、系统默认。

#### Scenario: User override
- **WHEN** 用户级配置存在且处于 ACTIVE 状态
- **THEN** 新 Run 使用用户级引擎配置并记录解析来源

### Requirement: Unsupported capability is explicit
系统 SHALL 在引擎不支持恢复、取消、流式、审批、MCP 或工作区等被请求能力时返回明确能力错误，不静默降级。

#### Scenario: CLI lacks resume
- **WHEN** 用户请求恢复一个 Generic CLI 引擎无法恢复的 Session
- **THEN** 系统返回 unsupported capability，并保留原 Session 状态

### Requirement: Engine process is isolated from platform API
系统 SHALL 通过受控 Worker 和 Sandbox 启动引擎进程，平台 API 不直接执行未经策略授权的宿主命令。

#### Scenario: Malformed CLI arguments
- **WHEN** EngineProfile 包含非法命令参数或未授权工作目录
- **THEN** Worker 拒绝启动且不执行宿主进程

### Requirement: Run result is engine-neutral
系统 SHALL 将不同引擎的结果转换为统一的 RunResult、EngineEvent、ToolCall 和 Usage 语义，并保留原始供应商请求引用。

#### Scenario: Codex event conversion
- **WHEN** Codex app-server 发送流式 Agent message 或 tool event
- **THEN** 平台将其转换为统一事件并关联 session_id、run_id、trace_id

### Requirement: Runtime-owned state remains opaque
系统 SHALL 将 Runtime 的 Session、Thread、内部上下文、私有记忆、工作区和结果状态作为 opaque 引用管理，不要求不同引擎共享或迁移其内部格式。

#### Scenario: Switch engine for a new run
- **WHEN** 用户把部署从 DeepSeek Harness 切换到 Codex
- **THEN** 新 Run 创建新的 Runtime Session，原引擎引用仍保留在历史审计中

### Requirement: Adapter does not reimplement the runtime loop
系统 SHALL 通过 Runtime Adapter 调用供应商的 Agent Loop、上下文压缩、内部工具循环和环境操作，并只在平台边界处理业务策略、权限、事件和用量。

#### Scenario: Runtime compacts context
- **WHEN** Runtime 在内部执行上下文压缩
- **THEN** 平台接收标准化事件或 Usage，不重新执行另一套压缩流程

### Requirement: Deployment override precedes scoped engine defaults

系统 SHALL 先解析 Deployment 的员工专属 Profile 覆盖；仅在继承模式下依次尝试 Session 创建者的用户、租户、平台和系统默认 Profile。

#### Scenario: Deployment has an explicit employee profile
- **WHEN** Deployment 处于 `OVERRIDE` 且 Session 创建者存在不同的用户默认 Profile
- **THEN** Session 使用 Deployment Profile，并记录来源为 `DEPLOYMENT_OVERRIDE`

#### Scenario: User default takes precedence over tenant default
- **WHEN** Deployment 处于 `INHERIT` 且用户和租户都有 ACTIVE 默认 Profile
- **THEN** Session 使用用户默认 Profile，并记录来源为 `USER_DEFAULT`

#### Scenario: Tenant and platform defaults are fallbacks
- **WHEN** Deployment 处于 `INHERIT` 且用户默认不存在
- **THEN** 系统依次使用租户默认、平台默认或系统默认，并记录实际解析来源

#### Scenario: No scoped profile resolves
- **WHEN** Deployment 处于 `INHERIT` 且所有作用域都没有 ACTIVE Profile
- **THEN** 系统返回 `RUNTIME_CONFIGURATION_REQUIRED`，不启动 EngineProvider 或 Fake Engine

### Requirement: EngineProfile and ModelRoute resolve independently

系统 SHALL 对 EngineProfile 与 ModelRoute 分别按 Deployment override、用户、租户、平台/系统的优先级解析，且在执行前验证最终组合兼容。

#### Scenario: Deployment overrides only engine
- **WHEN** Deployment 固定 ExecutionProfile，但没有固定 ModelRoute，且 Session 创建者有个人 ModelRoute 默认
- **THEN** Session 使用 Deployment EngineProfile 和个人 ModelRoute，并分别记录来源

#### Scenario: Deployment overrides only model
- **WHEN** Deployment 固定 ModelRoute，但未固定 ExecutionProfile，且用户没有个人 EngineProfile 默认
- **THEN** Session 使用作用域解析出的 EngineProfile 和 Deployment 显式 ModelRoute，并分别记录来源

#### Scenario: Changing the engine does not alter the selected model
- **WHEN** 用户在默认配置或 Deployment 配置中更换 EngineProfile
- **THEN** 已选择的 ModelRoute 保持不变；Session 创建前独立校验新组合，失败时提示错误且不替换模型

#### Scenario: Engine and model are incompatible
- **WHEN** 分别解析出的 EngineProvider 不支持所选 ModelRoute
- **THEN** Session 创建失败且不改变任一默认绑定，不回退到另一模型或引擎

### Requirement: EngineProvider registry validates engine and model compatibility

系统 SHALL 只允许选择已注册且获租户授权的 EngineProvider，并在创建 Session 前校验 Engine、ModelRoute 和请求能力兼容。

#### Scenario: Select a registered engine and supported model
- **WHEN** Deployment 选择已启用的 EngineProvider 和该 Provider 支持的 ModelRoute
- **THEN** Worker 按固定 EngineProfile/ModelRoute 启动对应 adapter 并将精确引用写入 Session/Run 快照

#### Scenario: Runtime model comes from the selected ModelRoute
- **WHEN** 新 Session 解析出独立 ModelRoute，且 ExecutionProfile 的历史 `model_alias` 为空或不同
- **THEN** Adapter 使用 ModelRoute 的模型名称；不得因 Profile alias 覆盖用户选择

#### Scenario: Model route is incompatible with engine
- **WHEN** 用户选择的 ModelRoute 协议或 Provider 不被 EngineProvider 支持
- **THEN** 系统返回 `ENGINE_MODEL_INCOMPATIBLE` 且不创建 Session/Run

#### Scenario: Requested feature is not supported by the engine
- **WHEN** ExecutionProfile 要求的恢复、取消、MCP、Workspace 或 Sandbox 能力不在 EngineProvider capabilities 中
- **THEN** 系统返回明确能力错误，不静默切换引擎或去掉配置

#### Scenario: Future adapter has not passed registration checks
- **WHEN** 用户尝试选择未注册或未通过隔离/凭据/生命周期验证的 Claude Code 或其他 EngineProvider
- **THEN** API 不向用户展示该选项，或在提交时拒绝且保持原绑定不变

### Requirement: Worker executes with the recipient Deployment snapshot

系统 SHALL 将接收方 Deployment 的 EngineProfile、ModelRoute 和来源解析为 Session/Run 的不可变运行快照，并要求 Worker 只使用该快照选择 EngineProvider 与模型路由。尚未接入 Worker 的 Deployment 级 Workspace/Sandbox/附加审批覆盖必须被显式拒绝，不得静默忽略。

#### Scenario: Execute Run after Deployment configuration update
- **WHEN** Worker 领取一个配置更新前已提交的 Run
- **THEN** Worker 使用 Run 固定的配置修订与资源版本，不读取 Deployment 当前最新配置替换快照

#### Scenario: Execute Run without required local model route
- **WHEN** 新 Session/Run 需要模型路由但接收方 Deployment 未配置有效的本租户 ModelRoute
- **THEN** 控制面在创建 Session/Run 前返回可定位配置错误，Worker 不启动模型请求

#### Scenario: Recipient credential is revoked
- **WHEN** Worker 运行时无法解析快照引用的接收方 SecretRef
- **THEN** Run 以确定性配置/凭据错误结束，不使用发布方凭据、其他租户凭据或 Fake Runtime

#### Scenario: Configuration changes while Run is executing
- **WHEN** 管理员在 Run 运行期间保存新的 Deployment 配置修订
- **THEN** 当前 Run 继续使用原快照，新修订只供之后创建的 Session 使用

#### Scenario: Snapshot excludes secret values
- **WHEN** 运行配置被保存到 RunSnapshot、Trace 或审计记录
- **THEN** 系统仅保存受控 SecretRef 和必要的非敏感配置摘要，不保存 API Key、密码或环境变量明文

#### Scenario: Unsupported Deployment environment override is not ignored
- **WHEN** Deployment 修订含有与精确 ExecutionProfile 不一致的 Workspace/Sandbox 覆盖，或含有未接入工具审批流程的附加审批策略
- **THEN** 新 Session 在 EngineProvider 出站前返回 `ENGINE_CAPABILITY_UNSUPPORTED`，不生成一个看似成功但没有应用该策略的 Run
