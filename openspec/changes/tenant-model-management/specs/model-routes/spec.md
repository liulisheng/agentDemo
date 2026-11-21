# Spec Delta

## ADDED Requirements

### Requirement: Model route binds to one model in a tenant connection
系统 SHALL 以连接引用和外部模型 ID 标识 ModelRoute；连接、目录模型和路由必须属于同一平台与租户。

#### Scenario: One connection provides multiple models
- **WHEN** 管理员从同一连接目录创建多个 ModelRoute
- **THEN** 每个路由引用各自的模型 ID，并可使用同一连接的协议、endpoint 和 SecretRef

#### Scenario: Route references another tenant's connection
- **WHEN** 管理员尝试把其他平台或租户的连接模型绑定到当前路由
- **THEN** 系统拒绝创建或更新该路由

### Requirement: Run snapshot pins the complete model route configuration
系统 SHALL 在 Run 创建时固定路由版本、连接版本、模型 ID、provider、protocol、endpoint、参数和 SecretRef；快照不得包含凭据明文。

#### Scenario: Worker executes a queued Run after route configuration changes
- **WHEN** Worker 执行 Run 时，连接或 ModelRoute 已发布了新版本
- **THEN** Worker 仍使用该 Run 快照中的模型配置和凭据引用

#### Scenario: Administrator rotates a model credential
- **WHEN** 管理员更新连接凭据引用并创建新的连接版本
- **THEN** 后续 Run 使用新版本，已创建 Run 保留原 SecretRef 版本且不暴露 Secret 明文
