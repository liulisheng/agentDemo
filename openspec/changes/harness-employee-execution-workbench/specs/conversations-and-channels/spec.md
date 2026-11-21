# Spec Delta

## ADDED Requirements

### Requirement: Employee workbench lists conversations within tenant scope
系统 SHALL 提供按当前认证租户和员工绑定查询 Conversation 的稳定游标分页结果，并为每项返回可授权展示的最后消息摘要、更新时间、最新 Run 状态和待审批数量。

#### Scenario: List conversations for one employee
- **WHEN** 用户为指定员工打开工作台并请求下一页
- **THEN** 系统只返回该用户有权访问且绑定该员工的 Conversation，并按更新时间和 conversation_id 稳定排序

#### Scenario: Conversation contains a pending approval
- **WHEN** Conversation 最新 Run 正在等待写操作审批
- **THEN** 列表项显示 AWAITING_APPROVAL 和待处理数量，且不把该摘要当作审批授权

### Requirement: User turns create recoverable asynchronous runs
系统 SHALL 将工作台中的用户消息、幂等键和对应的数字员工 Session 关联，并为有效 turn 创建可查询的持久化 Run。

#### Scenario: Submit a new employee task
- **WHEN** 用户向当前 Conversation 提交有效消息和幂等键，并可选指定员工
- **THEN** 系统保存消息并创建 QUEUED Run，立即返回 Run 标识而不等待 Harness 完成

#### Scenario: Conversation has multiple active employee bindings
- **WHEN** 用户提交 turn 时未指定员工，且 Conversation 有多个 ACTIVE AgentBinding
- **THEN** 系统拒绝请求并且不创建 Message 或 Run；用户指定一个有权访问的 employee_id 后才提交

#### Scenario: Retry the same turn
- **WHEN** 用户使用相同幂等键和相同消息摘要重试
- **THEN** 系统返回同一个消息/Run 结果且不重复启动员工

#### Scenario: Retry a turn with a changed request digest
- **WHEN** 用户复用幂等键但消息、Conversation、Session 或固定 RunSnapshot 摘要不同
- **THEN** 系统返回 IDEMPOTENCY_CONFLICT，且不新增 Message 或 Run

#### Scenario: Restore a conversation with an approval
- **WHEN** 用户重新打开含待审批 Run 的 Conversation
- **THEN** 系统恢复消息顺序、Run 状态和该审批的预览入口

### Requirement: Workbench distinguishes internal execution events
系统 SHALL 在工作台主时间线优先展示用户消息、实际工具调用与结果、审批状态和员工答复；Session、上下文和 Runtime/Model 内部事件 SHALL 默认收起，并在展开时提供中文说明。

#### Scenario: Hide implementation details by default
- **WHEN** 一个 Run 含有多个 Harness Runtime/Model 内部事件
- **THEN** 主时间线显示用户可理解的消息、工具活动和最终答复，并提供包含内部事件数量的折叠入口

#### Scenario: Inspect internal event details
- **WHEN** 用户展开 Run 的执行详情
- **THEN** 系统按时间顺序显示内部事件的中文含义和状态，同时保留必要的原始事件类型用于诊断
