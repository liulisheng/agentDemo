# execution-observability Specification

## Purpose

为每次 Agent 执行提供可检索的端到端链路，使平台能够还原 Session、Run、上下文、记忆、模型调用、工具调用、审批、沙箱和消息投递过程。

## Requirements

### Requirement: Every run has a correlated trace
系统 SHALL 为 Session 和 Run 生成稳定 trace_id，并为上下游操作生成可关联的 span_id、parent_span_id、turn_id 和时间戳。

#### Scenario: Session to model trace
- **WHEN** 用户消息触发一次 Run 并完成模型调用
- **THEN** 查询 Run 时间线可以按父子关系看到 Session、Context、Memory 和 Model Span

### Requirement: Model calls are observable
系统 SHALL 记录每次模型调用的引擎、模型路由、供应商请求引用、开始结束时间、状态、Token Usage 和内容引用。

#### Scenario: Multiple model calls in one run
- **WHEN** Agent 因工具调用进行两次模型调用
- **THEN** 同一 Run 下存在两个独立 Model Span，并能分别统计耗时和用量

### Requirement: Tool calls are observable
系统 SHALL 记录工具名称、协议类型、版本、输入输出引用、审批引用、状态、耗时、错误类别和重试次数。

#### Scenario: Tool failure
- **WHEN** MCP 工具调用超时
- **THEN** Trace 显示 Tool Span 为 failed，包含超时类别，并关联后续 Run 状态

### Requirement: Raw payload retention is policy controlled
系统 SHALL 通过租户或项目数据策略控制是否保存完整模型和工具入参出参；完整内容必须使用加密对象引用，普通索引不保存正文。

#### Scenario: Debug payload enabled
- **WHEN** 项目启用原始 payload 保存
- **THEN** 系统保存加密对象并在 Trace 中记录内容引用、哈希、大小和保留期限

#### Scenario: Debug payload disabled
- **WHEN** 项目未启用原始 payload 保存
- **THEN** Trace 只保存元数据和摘要，不保存完整 prompt、工具参数或工具输出

### Requirement: Timeline access is tenant scoped
系统 SHALL 按 platform、tenant、project 和授权用户范围限制 Trace、审计和原始 payload 查询。

#### Scenario: Cross-tenant trace query
- **WHEN** 用户查询其他租户的 trace_id
- **THEN** 系统按资源不存在处理并不返回链路内容

### Requirement: Runtime events are normalized without full history ownership
系统 SHALL 通过 Runtime Adapter 接收可用的 Runtime 事件、Usage 和 provider references，并将其转换为平台 Trace；平台不要求供应商暴露或迁移全部私有上下文历史。

#### Scenario: Runtime provides only final usage
- **WHEN** Runtime 只返回最终结果和 Token Usage
- **THEN** 平台记录可获得的 Model Span 和 Usage，并标记缺失的内部事件

### Requirement: Runtime references remain auditable
系统 SHALL 在 Session、Run、Workspace、Model 和 Runtime Thread 等资源上保存 opaque references、引擎名称和创建时间，以支持后续排障和回溯。

#### Scenario: Resume with runtime reference
- **WHEN** 用户恢复一个已存在的 Runtime Session
- **THEN** 平台先校验租户和引擎归属，再把保存的 opaque reference 交给对应 Runtime Adapter

### Requirement: Observability records strategy and phase summaries
系统 SHALL 为 Run 记录实际回答策略、阶段状态、阶段时间、总耗时和可用 reasoning token 用量，并关联 EngineProvider、ModelRoute、Session 和 Trace。

#### Scenario: Strategy and phases are available
- **WHEN** Runtime 返回思考、工具或答复事件
- **THEN** Trace 查询返回关联 Run 的阶段、状态、秒级时间和非敏感策略摘要

#### Scenario: Provider does not report reasoning usage
- **WHEN** 引擎没有返回 reasoning token
- **THEN** Usage 标记为 unavailable 或 estimated，不伪造精确用量

### Requirement: Observability excludes raw reasoning and secrets
系统 SHALL 禁止将原始思考链、隐藏 Prompt、凭据、完整工具参数和完整工具返回写入普通 Trace、Usage 或审计 payload。

#### Scenario: Runtime emits private reasoning content
- **WHEN** 适配器收到供应商内部推理正文
- **THEN** 平台只保留阶段和稳定摘要，原始正文不进入 MySQL、对象索引、日志或工作台消息

#### Scenario: Tenant reads another tenant trace
- **WHEN** 用户请求其他租户的 Run/Trace/Usage
- **THEN** API 按租户授权拒绝或返回资源不存在，不泄露策略或用量内容

### Requirement: Real model runs expose non-sensitive execution evidence
系统 SHALL 为真实模型 Run 记录 execution mode、EngineProvider、ModelRoute、模型 ID、脱敏 provider request reference、策略摘要、阶段、秒级时间、耗时和 Usage 状态，并与 Session/Run/Trace 关联。

#### Scenario: Trace real model success
- **WHEN**真实 OpenAI-compatible ModelRoute 返回成功答复和 Usage
- **THEN** Trace 和 Usage 标记 NATIVE 执行并展示可获得的模型、请求引用、阶段和 token 状态

#### Scenario: Provider omits reasoning usage
- **WHEN**真实模型未返回 reasoning token
- **THEN** Usage 标记 `UNAVAILABLE` 或 `ESTIMATED`，不伪造精确值

#### Scenario: Secret and raw reasoning are excluded
- **WHEN**真实模型响应包含凭据、隐藏 Prompt 或内部推理正文
- **THEN** 消息、Trace、Usage、日志和工作台只保留安全摘要，不保存这些正文
