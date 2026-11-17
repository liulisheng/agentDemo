# Spec Delta

## Purpose

为管理端和未来前端提供稳定的 HTTP 控制面，使平台资源能够按平台、租户、用户范围进行配置、发布、部署和查询。

## ADDED Requirements

### Requirement: Management API exposes platform resources
系统 SHALL 通过版本化 HTTP API 管理数字员工、技能、工具、执行配置、模型路由、部署、Conversation、Session、Run 和审计资源。

#### Scenario: List tenant resources
- **WHEN** 已认证用户查询当前租户的数字员工
- **THEN** API 只返回该用户有权访问的当前 platform_id 和 tenant_id 范围内资源

#### Scenario: Unknown resource
- **WHEN** 用户请求不存在或不属于当前租户的资源
- **THEN** API 返回统一的 not found 结果，不泄露资源是否存在于其他租户

### Requirement: API uses explicit authorization context
系统 SHALL 从已验证身份或 IdentityProvider 生成 platform、tenant、user 和授权上下文，不信任请求体中的身份字段作为授权依据。

#### Scenario: Body attempts to switch tenant
- **WHEN** 请求体携带与认证上下文不同的 tenant_id
- **THEN** API 拒绝请求并记录脱敏授权事件

#### Scenario: Fake identity context
- **WHEN** 当前环境启用 Fake IdentityProvider
- **THEN** API 使用 Fake 数据生成与生产相同结构的授权上下文

### Requirement: API errors are stable and actionable
系统 SHALL 对参数错误、未认证、无权限、资源冲突、依赖未发布和执行能力不可用返回稳定错误类别及可关联的 request_id。

#### Scenario: Publish dependency failure
- **WHEN** 发布请求引用不存在或未发布的 Skill 或 Tool
- **THEN** API 返回 dependency validation error，并列出可安全展示的缺失引用

### Requirement: Mutating operations are idempotent where applicable
系统 SHALL 对创建 Run、接收外部消息、发布版本和渠道投递等可能重试的操作支持显式幂等键。

#### Scenario: Retry with same payload
- **WHEN** 客户端使用相同幂等键和相同请求摘要重试
- **THEN** API 返回原操作结果且不创建重复资源或重复执行

#### Scenario: Reuse key with changed payload
- **WHEN** 相同幂等键对应不同请求摘要
- **THEN** API 返回 idempotency conflict 并拒绝第二次操作
