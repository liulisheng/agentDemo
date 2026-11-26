# Spec Delta

## Purpose

为平台提供可审计的 Agent 团队协作能力，让一个用户请求能够由多个固定成员、多个执行引擎和独立 Worker Pool 共同完成，并让用户持续看到计划、任务、成员活动和产出物交付状态。

## ADDED Requirements

### Requirement: Team version fixes members and policies

系统 SHALL 通过不可变 TeamVersion 固定团队成员、路由 Agent、汇总 Agent、编排模式、并发上限、审批策略和产出物交付策略；Conversation 只能绑定已发布版本。

#### Scenario: Create a team draft
- **WHEN** 具备团队管理权限的用户创建团队草稿并选择活动数字员工、路由 Agent 和汇总 Agent
- **THEN** 系统保存草稿，校验成员属于当前租户且可用，并允许继续编辑但不允许 Conversation 绑定未发布草稿

#### Scenario: Publish a team version
- **WHEN** 管理员发布通过能力、ModelRoute、并发和权限校验的团队草稿
- **THEN** 系统生成不可变 TeamVersion，后续 Conversation 绑定该版本的成员和策略快照

#### Scenario: Member cannot publish a team
- **WHEN** 没有团队管理权限的成员尝试保存或发布团队配置
- **THEN** 系统返回 403，不修改草稿、版本或成员绑定

### Requirement: Router Agent produces a validated task plan

系统 SHALL 将 Router Agent 的结构化输出视为计划提案，并在执行前校验任务数量、DAG 无环性、成员范围、能力、预算、租户和工具策略。

#### Scenario: Valid parallel plan
- **WHEN** 用户提交团队消息且固定 Router Session 的 QUEUED Run 由 Run Worker 成功生成两个无依赖任务和一个依赖两者的汇总任务
- **THEN** 初次 HTTP 响应保持 `PLANNING`，后续 Activity 查询保存计划版本，标记可并行分支，并进入计划确认或自动批准状态

#### Scenario: Invalid member reference
- **WHEN** Run Worker 返回的计划将任务分配给不属于当前 TeamVersion 的成员
- **THEN** Orchestration 进入 `FAILED` 并记录成员校验错误，不创建 Task 或子 Run

#### Scenario: Cyclic plan
- **WHEN** Run Worker 返回的任务依赖关系形成环
- **THEN** Orchestration 进入 `FAILED` 并记录 `TASK_GRAPH_CYCLE`，不派发任何任务

### Requirement: Task graph exposes observable lifecycle

系统 SHALL 为每个任务提供 `PROPOSED`、`READY`、`ASSIGNED`、`CLAIMED`、`RUNNING`、`BLOCKED`、`WAITING_APPROVAL`、`SUCCEEDED`、`FAILED`、`CANCELLED` 和 `SKIPPED` 状态，并保存状态变化事件。

#### Scenario: Dependency releases a task
- **WHEN** 任务的全部必需依赖进入 `SUCCEEDED`
- **THEN** 下游任务进入 `READY`，Scheduler 可以为其创建子 Run

#### Scenario: Dependency fails
- **WHEN** 必需依赖进入不可恢复的 `FAILED`
- **THEN** 下游任务进入 `BLOCKED` 或按 TeamVersion 继续策略进入 `SKIPPED`，并记录原因

#### Scenario: User cancels a task
- **WHEN** 有权限的用户取消尚未完成的任务
- **THEN** 任务进入 `CANCELLED`，系统停止派发该任务的新 Attempt，并处理下游依赖

### Requirement: Scheduler assigns tasks by capability and load

系统 SHALL 根据用户锁定的成员、Router Agent 建议、任务能力要求、成员并发额度、Worker Pool 健康和租户预算选择任务成员，并记录分配来源。

#### Scenario: Capability match
- **WHEN** 任务要求 Workspace 和 Tool 能力且只有 Codex 成员满足
- **THEN** Scheduler 将任务分配给该成员并记录 `assignment_source`、`engine_name` 和能力匹配结果

#### Scenario: No eligible member
- **WHEN** 团队中没有成员满足任务的能力或并发条件
- **THEN** 任务进入 `BLOCKED`，错误类别为 `ENGINE_CAPABILITY_UNSUPPORTED` 或 `ENGINE_POOL_UNAVAILABLE`

#### Scenario: User assignment takes precedence
- **WHEN** 用户锁定任务负责人且该成员仍为 ACTIVE 并满足能力要求
- **THEN** Scheduler 不覆盖用户指定成员，Router Agent 只能提出新的建议

### Requirement: Child Runs retain orchestration provenance

系统 SHALL 为每个可执行任务创建带有 `orchestration_id`、`task_id`、`attempt_no`、成员 Session、EngineProvider、ModelRoute 和 Trace 关联的子 Run。

#### Scenario: Create a child Run
- **WHEN** Task 进入 `READY` 且依赖、成员和引擎能力校验通过
- **THEN** 系统创建一个幂等的子 Run，并将其投递到该 `engine_name` 对应的 Worker Pool 队列

#### Scenario: Duplicate task submission
- **WHEN** Scheduler 因重试提交相同 TaskAttempt 的请求
- **THEN** 系统返回原子 Run 结果，不创建第二个有效子 Run

### Requirement: Orchestration aggregates and controls child Runs

系统 SHALL 聚合任务状态为 `PLANNING`、`AWAITING_PLAN_APPROVAL`、`READY`、`RUNNING`、`PAUSED`、`CANCELLING`、`SUCCEEDED`、`PARTIAL_FAILED`、`FAILED`、`CANCELLED` 或 `UNKNOWN`，并支持暂停、恢复和取消。

#### Scenario: All required tasks succeed
- **WHEN** 所有必需任务进入 `SUCCEEDED` 且汇总任务完成
- **THEN** OrchestrationRun 进入 `SUCCEEDED` 并生成最终答复引用

#### Scenario: Cancel running orchestration
- **WHEN** 用户取消正在运行的 OrchestrationRun
- **THEN** 系统停止派发新任务，向运行中的子 Run 发出取消请求，并在所有任务终止后进入 `CANCELLED`

#### Scenario: Partial failure remains deliverable
- **WHEN** 非必需任务失败但其他任务已成功并产生可见产出物
- **THEN** OrchestrationRun 进入 `PARTIAL_FAILED`，保留已完成产出物并展示失败任务原因

### Requirement: Member activity is queryable

系统 SHALL 为每个团队成员提供当前认领任务、执行阶段、EngineProvider、Worker Pool、认领时间、已运行时长和最近事件的可查询快照。

#### Scenario: View current activity
- **WHEN** 用户打开团队工作台的成员当前活动区域
- **THEN** 系统返回每个成员当前任务、状态、阶段摘要、Worker Pool、Run/Trace 标识和最近事件

#### Scenario: View task detail dialog
- **WHEN** 用户点击成员活动中的“查看任务”
- **THEN** 工作台展示任务状态、负责人、依赖、Attempt、Trace、当前阶段和产出物摘要

#### Scenario: View recent events dialog
- **WHEN** 用户点击成员活动中的“查看最近事件”
- **THEN** 工作台按服务端 sequence 展示 Worker 认领、Runtime、工具、Artifact 和错误事件

### Requirement: Team activity and task queries enforce tenant permissions

系统 SHALL 按当前 platform、tenant、Conversation 参与关系和成员角色授权团队配置、任务、活动、事件和产出物查询。

#### Scenario: Viewer reads activity
- **WHEN** 只读成员查询团队活动
- **THEN** 系统返回脱敏的任务、引擎、阶段和产出物状态，但不返回可执行的编辑、取消、重试或交付操作

#### Scenario: Cross-tenant activity request
- **WHEN** 用户使用其他租户的 orchestration_id、task_id 或 member_id 查询活动
- **THEN** 系统按资源不存在处理，不泄露跨租户任务或引擎信息

### Requirement: Team mode does not change pinned single-agent conversations

系统 SHALL 将团队编排限制在 `TEAM` Conversation；已有 `SINGLE_AGENT` Conversation 继续使用原固定 Session/Run，团队协作入口创建新的 Conversation。

#### Scenario: Continue a single-agent conversation as a team
- **WHEN** 用户在单员工工作台点击“以团队协作继续”并选择已发布 TeamVersion
- **THEN** 系统创建新的 TEAM Conversation，复制必要的用户任务摘要，原 Conversation、Session、Message 和 Run 保持不变

#### Scenario: Team configuration changes after binding
- **WHEN** 管理员发布新的 TeamVersion
- **THEN** 已绑定 Conversation 的成员、Session、任务和 Run 保持旧版本，新 Conversation 才使用新版本
