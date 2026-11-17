# Spec Delta

## Purpose

为多轮 Agent 运行提供可控制的上下文组装和分层记忆，使系统能够在 Token 预算内恢复必要信息，同时区分 Session、Conversation、User、Project、Tenant 和 Agent 记忆。

## ADDED Requirements

### Requirement: Context is assembled from declared sources
系统 SHALL 根据数字员工版本、Skill、最近消息、摘要、记忆和工具结果组装模型上下文，并记录每次模型调用使用的来源引用。

#### Scenario: Build context for a run
- **WHEN** Run 进入模型调用阶段
- **THEN** 系统生成 ContextSnapshot，记录纳入、排除和估算 Token 信息

### Requirement: Context obeys a budget policy
系统 SHALL 按 ExecutionProfile 或租户策略限制上下文大小，并在超限时按声明策略压缩、摘要或裁剪内容。

#### Scenario: Context exceeds budget
- **WHEN** 最近消息和工具结果超过模型上下文预算
- **THEN** 系统执行配置的 compaction 策略并记录被裁剪内容

### Requirement: Memory has explicit scopes
系统 SHALL 为记忆标识作用域、归属租户、来源 Run、可见性、保留策略和过期时间，检索时不得跨越授权范围。

#### Scenario: Tenant memory isolation
- **WHEN** Agent 查询当前租户的长期记忆
- **THEN** 检索结果不包含其他租户或未授权项目的记忆

### Requirement: Memory writes are controlled
系统 SHALL 通过显式 MemoryWriter 或配置的记忆提取策略写入长期记忆，不自动将全部原始对话持久化为长期记忆。

#### Scenario: Extract durable memory
- **WHEN** Run 完成且记忆策略判断某项偏好可长期保存
- **THEN** 系统创建带来源和作用域的 MemoryItem，并记录写入 Span

### Requirement: Memory retrieval is observable
系统 SHALL 记录记忆检索的查询引用、命中数量、耗时和使用的记忆引用，不在普通日志中泄露敏感正文。

#### Scenario: Memory search trace
- **WHEN** BusinessContextAssembler 检索 User 或 Project memory
- **THEN** Trace 中出现 Memory Search Span 并关联 ContextSnapshot

### Requirement: Platform manages business context, not runtime internals
系统 SHALL 负责租户、用户、项目、业务对象、Skill 和工具策略等业务上下文，并将 Runtime 私有上下文压缩、线程状态和内部记忆交给目标引擎。

#### Scenario: Runtime-owned compaction
- **WHEN** DeepSeek 或 Codex Runtime 对内部历史执行压缩
- **THEN** 平台不重复压缩同一历史，只记录可获得的 Runtime 事件、引用和 Usage

### Requirement: Enterprise memory is separate from runtime memory
系统 SHALL 将平台管理的 User、Project、Tenant 和 Agent 企业记忆与 Runtime 私有记忆分开，并允许只保存 Runtime memory reference。

#### Scenario: Runtime memory reference
- **WHEN** Runtime 返回可恢复的私有记忆引用
- **THEN** 平台保存 opaque reference，不解析或改写供应商私有记忆格式

### Requirement: Context snapshot records platform inputs
系统 SHALL 记录平台注入的业务上下文、Skill、工具结果、企业记忆引用和 Token 估算，但不要求复制 Runtime 的完整内部消息历史。

#### Scenario: Business context snapshot
- **WHEN** 平台为一次 Run 组装业务上下文
- **THEN** ContextSnapshot 记录输入来源、哈希、纳入/排除项和预算信息
