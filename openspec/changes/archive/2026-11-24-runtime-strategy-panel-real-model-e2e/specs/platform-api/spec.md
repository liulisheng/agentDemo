# Spec Delta

## ADDED Requirements

### Requirement: Strategy panel context is returned as one auditable view
系统 SHALL 提供策略面板所需的组合视图，包含 Deployment 修订、有效 RuntimeOptions、每个字段来源、EngineProvider capability、ModelRoute 摘要和新 Session 可用性；读取和保存均按认证 platform、tenant、user 上下文执行。

#### Scenario: Read panel context
- **WHEN** 已授权用户打开当前租户的运行策略面板
- **THEN** API 返回当前修订、有效策略、字段来源、能力摘要和不兼容原因，不返回 Secret 明文

#### Scenario: Save with stale revision
- **WHEN** 管理员使用过期 `expected_config_version` 保存策略
- **THEN** API 返回 `RUNTIME_CONFIG_REVISION_CONFLICT`，不覆盖其他管理员的新修订

#### Scenario: Cross-tenant panel request
- **WHEN** 用户使用其他租户的 Deployment 或 ModelRoute 标识读取/保存面板
- **THEN** API 按资源不存在或授权拒绝处理，不泄露资源存在性

### Requirement: Real model session launch uses the saved strategy snapshot
系统 SHALL 允许策略面板在保存成功后请求新 Conversation/Session，并只使用服务端解析的 EngineProfile、ModelRoute、RuntimeOptions 和配置修订创建 Run。

#### Scenario: Launch a new session from panel
- **WHEN** 管理员点击“保存并新建对话”且策略和 ModelRoute 均兼容
- **THEN** API 创建带固定策略快照的 Session/Conversation，并返回可提交 turn 的上下文

#### Scenario: Existing conversation remains pinned
- **WHEN** 用户在策略修订后继续发送旧 Conversation 消息
- **THEN** API 继续使用旧 Session/RunSnapshot，不将新策略静默注入旧对话
