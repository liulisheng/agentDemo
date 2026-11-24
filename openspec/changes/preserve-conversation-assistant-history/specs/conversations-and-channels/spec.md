# Spec Delta

## ADDED Requirements

### Requirement: Conversation messages preserve all completed turns
系统 SHALL 在同一 Conversation/Session 中按服务端序号持久化并恢复每轮 USER 消息和确定性 ASSISTANT 答复；加载后续 turn 不得删除或替换先前答复。

#### Scenario: Restore two completed turns
- **WHEN** 用户在同一 Session 完成第一轮和第二轮问答后重新读取 Conversation 消息
- **THEN** API 按顺序返回两条 USER 和两条 ASSISTANT 消息，前端同时展示两轮答复

#### Scenario: Second run is still active
- **WHEN** 第二轮尚未完成
- **THEN** 第一轮已完成答复继续展示，第二轮只展示已持久化的用户消息和当前 Run 状态

#### Scenario: Run result is uncertain
- **WHEN** Runtime 返回 UNKNOWN、取消或不可证明的失败
- **THEN** 系统不生成成功 ASSISTANT 答复，不覆盖历史答复，并保留 Run 的错误/对账状态

#### Scenario: Unauthorized tenant reads history
- **WHEN** 其他租户或非 Conversation participant 请求消息
- **THEN** 系统按既有资源不存在/拒绝语义返回，不泄露任何历史正文
