# Spec Delta

## ADDED Requirements

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
