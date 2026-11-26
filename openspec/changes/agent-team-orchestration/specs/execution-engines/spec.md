# Spec Delta

## ADDED Requirements

### Requirement: Each EngineProvider has an isolated Worker Pool

系统 SHALL 为每个启用的 EngineProvider 提供独立队列、Worker Pool、并发额度、健康状态和心跳；Worker 只能领取与自身 `engine_name` 匹配的 Run。

MySQL Worker 通过 `AGENT_PLATFORM_WORKER_ENGINE_NAMES` 限定其可领取的 EngineProvider。未配置时，部署入口必须退回当前 Runtime Engine；运行时 Worker 将其可服务的引擎写入 V014 心跳。

#### Scenario: Codex worker claims Codex Run
- **WHEN** Codex Worker 请求领取队列任务
- **THEN** 系统只返回 `engine_name=codex_cli` 的 QUEUED Run，不返回 DeepSeek 或 Fake Run

#### Scenario: Engine pool is unavailable
- **WHEN** 任务需要的 EngineProvider Worker Pool 无健康 Worker
- **THEN** 任务保持可恢复的 `BLOCKED` 或等待状态并记录 `ENGINE_POOL_UNAVAILABLE`，不静默切换其他引擎

#### Scenario: Pool health is visible
- **WHEN** 用户查看团队配置或成员活动
- **THEN** 工作台展示 Worker Pool 队列长度、Worker 数量、健康状态和最近心跳，不展示引擎凭据

#### Scenario: Stale worker heartbeat
- **WHEN** 指定 EngineProvider 的 Worker 在 30 秒内没有更新 V014 心跳
- **THEN** 工作台将该 Worker Pool 显示为不可用，并保留 QUEUED Run 供恢复后继续领取

### Requirement: Task capability requirements are checked before execution

系统 SHALL 在创建子 Run 和领取任务前校验任务所需的 Workspace、Sandbox、Tool、MCP、Reasoning、Approval、Context 或输出能力。

#### Scenario: Compatible member engine
- **WHEN** 任务要求 Workspace 且被分配的成员 EngineProvider 支持 Workspace
- **THEN** 系统允许创建子 Run，并将能力校验结果写入任务 Attempt 和 RunSnapshot 摘要

#### Scenario: Unsupported capability
- **WHEN** 任务要求 MCP 但候选成员的 EngineProvider 不支持 MCP
- **THEN** 系统返回 `ENGINE_CAPABILITY_UNSUPPORTED`，不启动 Worker 进程，不移除任务要求

### Requirement: Child Run pins the selected engine and model

系统 SHALL 将团队成员的 EngineProvider、ExecutionProfile、ModelRoute 和运行策略写入子 Run 的不可变快照，Worker 不得读取当前默认配置替换快照。

#### Scenario: Execute a mixed-engine team plan
- **WHEN** 同一 OrchestrationRun 将一个任务分配给 Codex 成员、另一个任务分配给 DeepSeek 成员
- **THEN** 两个子 Run 分别进入对应队列并使用各自固定的 EngineProvider、ModelRoute 和 Session

#### Scenario: Runtime config changes during execution
- **WHEN** 管理员在子 Run 执行期间发布新的 ExecutionProfile 或 ModelRoute
- **THEN** 当前子 Run 继续使用旧快照，新配置只影响之后创建的 Session/Run

#### Scenario: Engine switch is requested for a running Session
- **WHEN** 用户尝试在同一 Session 中途切换 EngineProvider
- **THEN** 系统拒绝切换并要求创建新的 Session/Run，原引擎引用保留在审计中

### Requirement: Cross-engine context uses normalized task outputs

系统 SHALL 通过平台标准化 TaskOutput、结构化结果和 Artifact 引用向不同引擎传递上下文，不共享供应商私有 Session、Thread、隐藏 Prompt 或凭据。

#### Scenario: DeepSeek result is consumed by Codex
- **WHEN** DeepSeek 成员完成上游任务且 Codex 成员依赖其结果
- **THEN** Codex Run 接收任务摘要、结构化结果和租户授权的 Artifact 引用，不接收 DeepSeek 私有 Session 引用

#### Scenario: Sensitive runtime state is excluded
- **WHEN** Scheduler 生成下游任务上下文
- **THEN** 上下文不包含 SecretRef 明文、原始思考链或不必要的完整工具响应

### Requirement: Engine errors remain explicit in task state

系统 SHALL 将 Worker Pool 不可用、引擎能力不支持、ModelRoute 协议不兼容、凭据失败、超时和取消转换为稳定错误，并保留子 Run 与 TaskAttempt 关联。

#### Scenario: Engine timeout
- **WHEN** Worker 在任务截止时间内未得到确定 RuntimeResult
- **THEN** 子 Run 使用现有 FAILED/UNKNOWN 语义结束，任务根据重试策略进入 READY、BLOCKED 或 FAILED，不自动重放可能已到达供应商的请求

#### Scenario: Credential failure
- **WHEN** Worker 无法解析固定 ModelRoute 的 SecretRef
- **THEN** Run 以稳定凭据错误结束，不使用其他租户凭据、发布方凭据或 Fake Runtime

### Requirement: Runtime credentials remain in Worker processes

MySQL 模式下，API SHALL 只保存有租户范围的 RunSnapshot、ObjectStore 输入引用和队列状态，不调用需要 SecretRef 的 Runtime；只有持有当前 MySQL lease/fencing token 的 Run Worker 可以执行成员或 Router Runtime。

#### Scenario: Router and member work use the credentialed Worker
- **WHEN** 团队消息触发 Router 计划或成员 Task
- **THEN** API 返回持久化的 PLANNING/QUEUED 状态，受信 Worker 执行 Runtime，后续查询将 Run 终态恢复为 Orchestration/Task/Attempt/Artifact 状态

#### Scenario: Worker receives a stale or mismatched Run
- **WHEN** Worker 的 engine filter 与 RunSnapshot.engine_name 不匹配，或 Run lease/fencing token 已失效
- **THEN** Worker 不调用 Runtime，且不能覆盖当前 Task/Run 终态
