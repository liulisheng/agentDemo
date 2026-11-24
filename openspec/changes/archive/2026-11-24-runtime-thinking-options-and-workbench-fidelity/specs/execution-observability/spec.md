# Spec Delta

## ADDED Requirements

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
