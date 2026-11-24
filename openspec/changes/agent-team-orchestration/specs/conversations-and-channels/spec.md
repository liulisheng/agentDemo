# Spec Delta

## MODIFIED Requirements

### Requirement: Conversation is independent from Session

系统 SHALL 将用户可见 Conversation 与数字员工的 Session 分开建模，一个 Conversation 可以绑定多个数字员工，每个数字员工拥有独立 Session；Conversation 创建时必须固定 `SINGLE_AGENT` 或 `TEAM` 模式。

#### Scenario: Single-agent conversation
- **WHEN** 用户创建 `SINGLE_AGENT` Conversation 并绑定一个 ACTIVE 数字员工
- **THEN** 系统维护共享可见消息、一个 AgentBinding 和一个固定 Session，用户消息继续创建单个 Run

#### Scenario: Multi-agent group
- **WHEN** 群聊绑定两个 ACTIVE 数字员工并固定 `TEAM` 模式
- **THEN** 系统维护共享可见消息、多个独立 Agent Session 和一个 ConversationTeamBinding

#### Scenario: Mode cannot be changed in place
- **WHEN** 用户尝试把已有 `SINGLE_AGENT` Conversation 原地改为 `TEAM`
- **THEN** 系统拒绝原地转换，并要求创建新的 TEAM Conversation

### Requirement: Group messages require an explicit trigger

系统 SHALL 根据 Conversation 模式和触发规则启动执行：`SINGLE_AGENT` 消息可以直接创建固定 Session 的 Run；`TEAM` 消息必须命中团队路由规则、用户确认的团队计划或显式团队入口，普通未触发消息只保存而不创建 OrchestrationRun。

#### Scenario: Ordinary single-agent message
- **WHEN** `SINGLE_AGENT` Conversation 收到一条有效用户消息
- **THEN** 系统为该 Conversation 的固定 Session 创建一个幂等 Run

#### Scenario: Ordinary team message
- **WHEN** `TEAM` Conversation 消息没有 @提及、命令、启用路由规则或团队提交入口
- **THEN** 系统保存消息但不创建 OrchestrationRun 或子 Run

#### Scenario: Mentioned team route
- **WHEN** `TEAM` Conversation 消息命中有效 @提及、命令或团队路由规则
- **THEN** 系统创建一个 OrchestrationRun，由 Router Agent 生成任务计划并按确认策略继续

#### Scenario: Agent message does not create a new team loop
- **WHEN** 团队成员 Agent 的消息被其他成员接收但未命中显式 A2A 规则
- **THEN** 系统不创建新的 OrchestrationRun，并记录 loop prevention 事件

## ADDED Requirements

### Requirement: Conversation mode is exposed consistently in the workbench

系统 SHALL 在同一执行工作台提供“数字员工对话”和“团队协作”两个互斥模式，并让右上角只显示当前模式对应的上下文选择器。

#### Scenario: Team workbench context
- **WHEN** 用户切换到团队协作模式
- **THEN** 右上角显示当前团队和团队配置入口，左侧只显示团队 Conversation，不显示当前数字员工选择器

#### Scenario: Single-agent workbench context
- **WHEN** 用户切换到数字员工对话模式
- **THEN** 右上角显示当前数字员工和运行配置入口，左侧只显示单员工 Conversation，不显示当前团队选择器

### Requirement: Continue as team creates a new Conversation

系统 SHALL 提供从单员工 Conversation 继续为团队协作的入口，并返回新 Conversation 与来源 Conversation 的关联。

#### Scenario: Continue with a published team
- **WHEN** 用户选择已发布 TeamVersion 并点击“以团队协作继续”
- **THEN** 系统创建新的 TEAM Conversation，复制用户可见任务摘要和允许复用的 Artifact 引用，并返回 `source_conversation_id`

#### Scenario: Continue without team permission
- **WHEN** 没有团队访问权限的用户点击团队协作入口
- **THEN** 系统返回 403，不创建新 Conversation，不改变原单员工 Conversation

### Requirement: Conversation history remains mode-compatible

系统 SHALL 让单员工和团队 Conversation 共享 Message、Trace 和时间精度语义，同时按模式展示对应时间线内容。

#### Scenario: Single-agent history restore
- **WHEN** 用户刷新单员工工作台
- **THEN** 系统恢复 USER/ASSISTANT 消息、单个 Run 阶段、策略快照和审批状态，不展示团队任务面板

#### Scenario: Team history restore
- **WHEN** 用户刷新团队工作台
- **THEN** 系统恢复团队消息、OrchestrationRun、任务状态、成员活动、Worker Pool 摘要和产出物交付状态

#### Scenario: Loading, empty, error and read-only states
- **WHEN** 任一模式的 Conversation、任务、活动或 Run 查询处于加载、空、失败或只读状态
- **THEN** 工作台使用当前原型的浅色管理台样式展示对应状态，并隐藏无权限操作
