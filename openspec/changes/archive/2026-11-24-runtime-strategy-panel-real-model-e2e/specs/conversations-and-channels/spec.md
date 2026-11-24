# Spec Delta

## ADDED Requirements

### Requirement: Strategy panel can start a pinned Conversation
系统 SHALL 从运行策略面板提供“保存并新建对话”操作；新 Conversation/Session 必须使用保存后解析出的策略、引擎、模型和配置修订。

#### Scenario: Start conversation with real model strategy
- **WHEN** 管理员保存有效策略并点击新建对话，然后提交一条用户消息
- **THEN** 工作台显示真实 ModelRoute 的 Run、固定策略快照、阶段事件和助手答复

#### Scenario: Start is blocked by incompatible configuration
- **WHEN** 策略、EngineProvider 和 ModelRoute 不兼容
- **THEN** 工作台显示可定位错误，不创建空 Conversation、不发送用户消息、不切换 Fake

#### Scenario: Refresh preserves the real response
- **WHEN** 真实模型 Run 成功后用户刷新工作台
- **THEN** USER/ASSISTANT 消息、策略快照、阶段时间和 Run 状态仍可从服务端恢复
