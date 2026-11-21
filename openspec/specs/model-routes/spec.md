# model-routes Specification

## Purpose

为平台提供统一的模型路由和连接配置，使当前的直接模型接入能够平滑切换到 new-api 或 Dadi Model Gateway，并让 Run 记录实际使用的模型来源和用量。

## Requirements

### Requirement: Model route is provider-neutral
系统 SHALL 以模型路由、模型别名、协议入口和认证引用描述模型接入，不要求平台为每家供应商维护独立兼容性矩阵。

#### Scenario: Configure direct model route
- **WHEN** 管理员配置有效的直接模型入口和 Secret 引用
- **THEN** 系统创建可绑定到 ExecutionProfile 的模型路由

### Requirement: Model gateway adapters are replaceable
系统 SHALL 允许通过适配器使用直接模型、new-api 或 Dadi Model Gateway，业务领域对象不因网关替换而改变。

#### Scenario: Switch gateway adapter
- **WHEN** 租户将模型路由从直接入口切换到 new-api
- **THEN** 新 Run 使用新适配器，已开始的 Run 保留原模型路由快照

### Requirement: Model call returns normalized usage
系统 SHALL 在模型供应商或网关返回用量时记录输入 Token、输出 Token、总 Token、请求引用和费用信息；无法获得精确值时必须标记为估算。

#### Scenario: Gateway returns usage
- **WHEN** new-api 返回 provider request id 和 usage
- **THEN** 平台保存标准化 ModelCallUsage 并关联 Run、Trace 和模型路由

#### Scenario: Gateway omits usage
- **WHEN** 模型响应没有精确 usage
- **THEN** 平台可以保存估算值，但必须标记 usage_status 为 ESTIMATED

### Requirement: Secret is not part of model configuration payload
系统 SHALL 只在模型路由中保存 Secret 引用，不将 API Key 写入数据库、日志、版本快照或用户可见响应。

#### Scenario: Secret reference resolution
- **WHEN** Worker 启动模型调用
- **THEN** Worker 通过受控 SecretProvider 解析凭据并只在进程作用域内使用

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
