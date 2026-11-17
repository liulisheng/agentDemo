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
