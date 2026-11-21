# Spec Delta

## Purpose

为租户管理员提供模型连接、连接内多模型目录、模型路由和可用性测试能力，使数字员工可以绑定一个经过验证且当前租户可用的模型路由。

## ADDED Requirements

### Requirement: Model connections are tenant-scoped
系统 SHALL 将模型连接归属到一个明确租户，并只允许该租户的授权管理员查看和管理连接。连接配置包含协议、Base URL、凭据引用和状态，不包含凭据明文。

#### Scenario: Tenant administrator creates a connection
- **WHEN** 当前租户管理员创建有效的模型连接
- **THEN** 系统保存连接并将其绑定到当前平台和租户

#### Scenario: Another tenant lists connections
- **WHEN** 其他租户用户查询模型连接
- **THEN** 系统不返回当前租户的连接、模型目录或凭据引用

### Requirement: A connection can expose multiple model catalog entries
系统 SHALL 允许同一租户模型连接关联多个外部模型 ID。目录项来源可以是已认证的远端模型列表或管理员手工录入。

#### Scenario: Synchronize models from a connection
- **WHEN** 管理员同步支持模型目录查询的连接
- **THEN** 系统为每个返回的模型 ID 创建或更新该连接下的目录项，不复制连接凭据

#### Scenario: Add a model when remote discovery is unavailable
- **WHEN** 连接不支持模型目录查询但管理员提供有效模型 ID
- **THEN** 系统允许管理员手工添加该连接下的模型目录项

### Requirement: Model routes bind one tenant model to an employee
系统 SHALL 允许租户管理员为连接下的一个模型创建稳定的 ModelRoute 别名。员工版本只能绑定同租户且处于启用状态的路由。

#### Scenario: Create a route from a catalog item
- **WHEN** 管理员为当前租户连接下的模型创建 ModelRoute
- **THEN** 路由记录连接引用、外部模型 ID、别名和默认参数，并可供同租户员工版本选择

#### Scenario: Bind a model from another tenant
- **WHEN** 管理员尝试将其他租户的模型或连接绑定到当前租户路由
- **THEN** 系统拒绝该绑定并返回租户范围错误

### Requirement: Connection test does not generate model output
系统 SHALL 提供显式连接测试，用连接的 SecretRef 验证认证和目录可达性；连接测试不得通过生成补全来冒充连通性检查。

#### Scenario: Connection authentication succeeds
- **WHEN** 管理员显式测试一个有效连接
- **THEN** 系统返回连接状态、目录发现状态和脱敏错误类别，不返回或记录凭据明文

#### Scenario: Connection requires a credential
- **WHEN** 连接端点拒绝请求或 SecretRef 无法解析
- **THEN** 系统显示认证或凭据解析失败，不将连接标记为可用

### Requirement: Model test performs a bounded real inference
系统 SHALL 提供显式模型测试，通过 Worker 向所选连接的具体模型发送无工具、长度受限的合成请求，并记录结果状态、延迟、请求引用和可用用量。

#### Scenario: Administrator confirms a model test
- **WHEN** 管理员查看测试说明并显式确认模型测试
- **THEN** 系统将一次受限测试交给 Worker 执行，不在 API 请求进程中启动 Runtime

#### Scenario: Model test succeeds
- **WHEN** Worker 收到所选模型的有效响应
- **THEN** 系统显示成功状态、模型 ID、延迟及精确或估算用量，并标明该结果来自测试调用

#### Scenario: Model test fails
- **WHEN** 凭据无效、模型不存在、端点超时或响应格式错误
- **THEN** 系统记录稳定的脱敏错误类别并显示测试失败，不自动重试不确定的请求

### Requirement: Platform authorization filters tenant model routes
系统 SHALL 在模型路由列表和员工版本选择器中仅返回当前租户已启用且当前平台授权策略允许使用的路由。

#### Scenario: User selects an authorized route
- **WHEN** 用户有权配置员工且模型路由对当前租户可用
- **THEN** 员工版本选择器显示该路由的别名和模型 ID

#### Scenario: Route access is revoked before execution
- **WHEN** Worker 执行 Run 前发现该路由已禁用或当前用户不再具有平台侧使用权限
- **THEN** Worker 在模型请求发出前拒绝执行并记录授权错误
