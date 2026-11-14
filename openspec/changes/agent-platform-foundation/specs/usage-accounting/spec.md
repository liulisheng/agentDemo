# Spec Delta

## Purpose

为平台提供模型和工具调用的可核对用量台账，使管理员能够按平台、租户、用户、数字员工、Session 和 Run 查询 Token、费用、耗时和预算消耗。

## ADDED Requirements

### Requirement: Usage is recorded per model call
系统 SHALL 为每次模型调用记录输入 Token、输出 Token、总 Token、缓存或推理 Token（若供应商提供）、请求引用、状态和计量来源。

#### Scenario: Exact provider usage
- **WHEN** 模型网关返回精确 usage
- **THEN** 台账保存 exact usage_status 并关联 provider_request_id

#### Scenario: Estimated usage
- **WHEN** 模型响应不包含 usage
- **THEN** 台账可以保存估算值，但必须标记 ESTIMATED 并保留估算方法

### Requirement: Cost uses a pricing snapshot
系统 SHALL 使用调用发生时的定价快照计算费用，后续价格变化不能改变已经完成 Run 的费用记录。

#### Scenario: Price changes after run
- **WHEN** 管理员更新模型价格配置
- **THEN** 历史 Run 仍使用原 pricing_version 和 cost_amount

### Requirement: Usage aggregates by organizational scope
系统 SHALL 支持按 platform、tenant、user、digital employee、project、channel、Session 和 Run 汇总 Token、费用、调用次数和耗时。

#### Scenario: Tenant monthly report
- **WHEN** 租户管理员查询月份用量
- **THEN** 返回租户范围内的模型 Token、工具调用、费用和失败统计

### Requirement: Budget policy can stop new work
系统 SHALL 在平台、租户、用户或项目达到配置的 Token、费用、并发或执行时长预算时阻止新的 Run 或工具调用。

#### Scenario: Budget exceeded
- **WHEN** 租户费用预算已耗尽且没有管理员豁免
- **THEN** 新 Run 被拒绝并记录 budget exceeded 事件

### Requirement: Usage records are immutable
系统 SHALL 将已完成模型调用的计量记录视为不可变，只允许通过更正记录关联新的调整事件。

#### Scenario: Correct a provider report
- **WHEN** 网关对历史调用返回修正账单
- **THEN** 系统新增 adjustment record 并保留原始计量记录
