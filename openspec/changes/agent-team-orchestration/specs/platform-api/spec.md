# Spec Delta

## ADDED Requirements

### Requirement: Conversation creation declares its mode

系统 SHALL 在 Conversation 创建 API 中要求或推导 `conversation_mode`，并对 `SINGLE_AGENT` 与 `TEAM` 分别校验数字员工绑定或已发布 TeamVersion。

#### Scenario: Create a single-agent conversation
- **WHEN** 用户提交 `SINGLE_AGENT` 和一个租户内 ACTIVE 数字员工
- **THEN** API 创建单员工 Conversation/Session，并返回固定运行配置摘要

#### Scenario: Create a team conversation
- **WHEN** 用户提交 `TEAM` 和有权访问的已发布 TeamVersion
- **THEN** API 创建团队 Conversation、成员 AgentBinding 和团队运行上下文

#### Scenario: Invalid team version
- **WHEN** 请求引用草稿、已撤销或其他租户的 TeamVersion
- **THEN** API 返回统一的 not found 或 dependency error，不创建 Conversation、Session 或消息

### Requirement: Team and orchestration APIs are tenant-scoped

系统 SHALL 提供 Team、TeamVersion、Worker Pool、OrchestrationRun、Task、Activity、Event、Artifact 和 Delivery 查询/控制 API，并始终使用认证 platform、tenant、user 和参与关系进行授权。

#### Scenario: List team activity
- **WHEN** 用户查询自己有权访问的 Conversation 团队活动
- **THEN** API 返回成员当前任务、状态、EngineProvider、Worker Pool、已运行时长和最近事件

#### Scenario: Cross-tenant orchestration request
- **WHEN** 用户使用其他租户的 orchestration_id 查询任务或事件
- **THEN** API 返回统一的 not found，不泄露其他租户资源存在性

### Requirement: Orchestration mutations are idempotent

系统 SHALL 对创建 OrchestrationRun、确认计划、创建 TaskAttempt、重试任务、取消编排和 ArtifactDelivery 支持显式幂等键或稳定资源冲突语义。

#### Scenario: Retry orchestration creation
- **WHEN** 客户端使用相同幂等键和相同请求摘要重复提交团队任务
- **THEN** API 返回原 OrchestrationRun、用户消息和计划结果，不创建重复父运行

#### Scenario: Reuse idempotency key with changed input
- **WHEN** 相同幂等键提交不同的用户需求或团队版本
- **THEN** API 返回 `IDEMPOTENCY_CONFLICT`，不修改已有编排

### Requirement: API exposes mode-compatible workbench context

系统 SHALL 提供工作台上下文查询，单员工模式返回当前数字员工和运行配置，团队模式返回当前团队、成员、配置、Worker Pool、任务和产出物摘要。

#### Scenario: Read single-agent context
- **WHEN** 用户打开 `SINGLE_AGENT` Conversation 工作台
- **THEN** API 返回单 Session/Run、EngineProvider、ModelRoute、策略快照和审批摘要，不返回团队任务面板

#### Scenario: Read team context
- **WHEN** 用户打开 `TEAM` Conversation 工作台
- **THEN** API 返回 TeamVersion、成员能力、Worker Pool、Orchestration 状态、任务队列、成员活动和产出物摘要

### Requirement: Continue-as-team preserves source conversation

系统 SHALL 提供 `POST /api/v1/conversations/{conversation_id}/continue-as-team`，只允许从 `SINGLE_AGENT` Conversation 创建新的 TEAM Conversation。

#### Scenario: Create team continuation
- **WHEN** 用户选择已发布 TeamVersion 并提交 continue-as-team 请求
- **THEN** API 返回新 `conversation_id`、`source_conversation_id` 和复制的任务摘要，不修改源 Conversation 的 Session/Run

#### Scenario: Continue-as-team from team conversation
- **WHEN** 用户从已有 TEAM Conversation 再次调用 continue-as-team
- **THEN** API 返回稳定的 invalid state 错误，不创建嵌套 Conversation

### Requirement: Existing single-agent APIs remain compatible

系统 SHALL 继续支持现有单员工 `POST /conversations/{id}/turns`、消息、Run、Trace、取消和策略快照接口；对 `TEAM` Conversation 的单员工 turn 请求返回可定位的模式错误。

#### Scenario: Existing single-agent turn
- **WHEN** 客户端向 `SINGLE_AGENT` Conversation 提交 turn
- **THEN** API 按既有幂等语义创建固定 Session 的单个 Run，并返回兼容字段

#### Scenario: Single-agent endpoint used for team
- **WHEN** 客户端向 `TEAM` Conversation 调用单员工 turn 接口
- **THEN** API 返回 `CONVERSATION_MODE_MISMATCH`，提示使用团队编排提交接口，不创建孤立 Run

### Requirement: Team messages are planned by the bound Router Agent

系统 SHALL 通过 `POST /api/v1/conversations/{conversation_id}/team-turns` 将用户消息交给该 Conversation 固定 TeamVersion 的 Router Session，并只接受满足成员范围、能力、DAG 和租户校验的任务计划。MySQL 模式必须先返回 PLANNING 状态并将 Router Run 排入受信 Run Worker 队列；计划终态由 Activity 查询恢复。

#### Scenario: Router creates a valid plan
- **WHEN** 用户向已绑定的 TEAM Conversation 提交文本和幂等键
- **THEN** API 持久化用户原文，并原子保存 Router QUEUED Run 与隐藏输入对象，立即返回 `PLANNING`
- **AND** Run Worker 使用自身 SecretRef 执行；后续 Activity 查询验证 Worker 的结果后持久化 Router 摘要、Task DAG，并按计划审批策略返回 `AWAITING_PLAN_APPROVAL` 或 `READY`

#### Scenario: Router returns invalid or unsupported plan
- **WHEN** Router 输出非 JSON 任务计划、循环依赖、未知成员或不支持的能力
- **THEN** API 返回稳定的 `PLAN_INVALID`/`TASK_GRAPH_CYCLE`/`ENGINE_CAPABILITY_UNSUPPORTED` 错误，不派发子 Run

#### Scenario: Replay a team turn after process restart
- **WHEN** 同一租户和 Conversation 以相同幂等键重放相同消息
- **THEN** API 返回已有 Orchestration 和计划，不重复运行 Router 或创建第二组任务

#### Scenario: Runtime credentials are isolated to Worker
- **WHEN** API 接收团队 Router 或成员 Task 请求
- **THEN** API 仅存储 QUEUED Run 与对象引用，不调用需要 SecretRef 的 Runtime
- **AND** 只有持有有效租户 Worker lease 的 Run Worker 执行 Runtime，API 轮询后按 Run 终态更新 Task

### Requirement: Mutating APIs expose stable permission and runtime errors

系统 SHALL 对团队草稿保存、版本发布、计划确认、任务分配、暂停/恢复/取消、Artifact 交付和运行配置变更返回稳定错误类别，并不泄露凭据明文。

#### Scenario: Member saves team configuration
- **WHEN** 普通成员尝试保存团队草稿或发布 TeamVersion
- **THEN** API 返回 403，不修改团队配置或成员绑定

#### Scenario: Runtime capability failure
- **WHEN** 计划或任务引用不支持的 EngineProvider 能力
- **THEN** API 返回 `ENGINE_CAPABILITY_UNSUPPORTED`，保留可读的任务错误和 request_id，不启动引擎

#### Scenario: Configuration revision conflict
- **WHEN** 管理员用过期 TeamVersion/运行配置修订保存更新
- **THEN** API 返回稳定版本冲突，不覆盖已发布或当前生效配置
