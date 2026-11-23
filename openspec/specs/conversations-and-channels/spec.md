# conversations-and-channels Specification

## Purpose

为 Web、单聊、群聊和未来 IM 接入提供统一的 Conversation、Message、Participant、AgentBinding 和 Channel Adapter 行为，并控制哪些消息可以启动 Run。

## Requirements

### Requirement: Conversation is independent from Session
系统 SHALL 将用户可见 Conversation 与数字员工的 Session 分开建模，一个 Conversation 可以绑定多个数字员工，每个数字员工拥有独立 Session。

#### Scenario: Multi-agent group
- **WHEN** 群聊绑定两个 ACTIVE 数字员工
- **THEN** 系统维护共享可见消息和分别关联的 Agent Session

### Requirement: Message ingress is normalized
系统 SHALL 将 Web、IM Webhook 和内部事件转换为包含来源、外部消息 id、会话、参与者和回复关系的统一 Message。

#### Scenario: Duplicate webhook
- **WHEN** 同一个 Channel 重复发送相同 external_message_id
- **THEN** 系统只创建一条内部 Message 且返回原处理结果

### Requirement: Group messages require an explicit trigger
系统 SHALL 只在消息包含有效 @提及、命令或匹配已启用路由规则时启动 Agent Run。

#### Scenario: Ordinary group message
- **WHEN** 群聊消息没有 @提及、命令或规则命中
- **THEN** 系统保存消息但不创建 Run

#### Scenario: Mentioned agent
- **WHEN** 群聊消息 @提及某个 ACTIVE 数字员工
- **THEN** 系统只为被提及的 AgentBinding 创建 Run

### Requirement: Channel delivery is retryable
系统 SHALL 为出站消息记录目标渠道、幂等键、投递状态、外部消息 id 和重试信息。

#### Scenario: Delivery retry
- **WHEN** IM 平台暂时不可用
- **THEN** 系统保留 Outbound Delivery 并按策略重试，不重复创建业务 Run

### Requirement: Agent messages cannot create uncontrolled loops
系统 SHALL 标识消息来源和路由深度，并阻止 Agent 消息在没有明确规则时再次触发 Agent。

#### Scenario: Agent-to-agent loop
- **WHEN** 一个 Agent 的回复被另一个 Agent 接收但未命中显式 A2A 或路由规则
- **THEN** 系统不启动新的 Run 并记录 loop prevention 事件

### Requirement: Conversation sessions inherit the Deployment runtime configuration

系统 SHALL 在 Conversation 创建 Session 时分别解析 Deployment/User/Tenant EngineProfile 与 ModelRoute 配置，并将解析结果和来源固定到该 Session 的后续 Run。

#### Scenario: Start a conversation with configured Deployment
- **WHEN** 用户为 `ACTIVE` 且已配置的 Deployment 新建 Conversation/Session
- **THEN** Session 固定该 Deployment 当前运行配置修订，工作台显示其 Profile、ModelRoute 和配置修订摘要

#### Scenario: Inherit the Session creator's user default
- **WHEN** 用户为 `INHERIT` Deployment 创建 Conversation/Session 且用户与租户都有默认 Profile
- **THEN** Session 使用创建者的用户默认 Profile，并显示 `USER_DEFAULT` 来源

#### Scenario: Resolve engine and model from separate scopes
- **WHEN** Session 创建者的个人 EngineProfile 默认存在，但个人 ModelRoute 默认未设置且租户配置了 ModelRoute 默认
- **THEN** Session 使用用户 EngineProfile 与租户 ModelRoute，分别显示两个解析来源

#### Scenario: Shared conversation does not switch to another participant's default
- **WHEN** 另一位参与者向已创建的 Conversation 提交新 Run，且其个人默认引擎与 Session Profile 不同
- **THEN** 新 Run 继续使用该 Session 固定的引擎和模型，不按提交人的个人默认重新解析

#### Scenario: Deployment configuration changes after Session creation
- **WHEN** 管理员为 Deployment 保存新的运行配置修订
- **THEN** 已有 Conversation/Session 和 Run 保持旧修订，新建 Conversation/Session 使用新修订

#### Scenario: Global default changes after Session creation
- **WHEN** 用户或管理员更换用户/租户 EngineProfile 默认或 ModelRoute 默认
- **THEN** 后续新 Session 分别按新作用域解析，已有 Session/Run 继续使用创建时固定的 Profile、ModelRoute 和两个来源

#### Scenario: User expects new configuration in an existing conversation
- **WHEN** 用户在配置更新后继续提交到旧 Session 的 Conversation
- **THEN** 工作台明确提示该对话仍使用旧修订，并提供创建新 Conversation 的入口；系统不得静默改变该 Session 的运行环境

#### Scenario: Deployment still needs configuration
- **WHEN** 用户尝试为 `PENDING_CONFIGURATION` Deployment 创建 Conversation/Session
- **THEN** 工作台展示“请先配置运行环境”并链接到有权限的配置入口，不保存空白会话或用户消息

#### Scenario: Member cannot edit Deployment configuration
- **WHEN** 普通运行成员查看对话运行信息
- **THEN** 系统允许其查看该 Session 使用的配置修订摘要，但不展示 SecretRef 明文或配置保存操作

#### Scenario: No profile can be resolved for an inherited Deployment
- **WHEN** 用户为 INHERIT Deployment 创建 Session 但所有可用作用域都没有 ACTIVE ExecutionProfile
- **THEN** 系统返回 `RUNTIME_CONFIGURATION_REQUIRED` 且不创建空 Session 或 Conversation
