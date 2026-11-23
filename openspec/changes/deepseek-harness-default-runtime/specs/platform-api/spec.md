# Spec Delta

## ADDED Requirements

### Requirement: EngineProvider API reflects the runtime available to workers
系统 SHALL 通过 `GET /api/v1/engine-providers` 返回当前执行配置中已启用的 Provider 摘要；清单不得暴露运行命令、SecretRef 或凭据明文。

#### Scenario: DeepSeek is the default in MySQL mode
- **WHEN** MySQL/Compose 服务未设置运行引擎变量并完成启动
- **THEN** API 返回一个 `deepseek_harness` Provider，执行模式为原生模式，且新建 Profile 表单只展示该规范选项

#### Scenario: Operators explicitly select Fake
- **WHEN** MySQL/Compose API 与 Worker 均显式配置 Fake Runtime
- **THEN** API 只公布 Fake Provider，并将其标记为模拟模式

#### Scenario: Create profile for an enabled provider
- **WHEN** 当前租户管理员提交一个当前 API 清单中已注册的 EngineProvider
- **THEN** API 创建相应 ExecutionProfile；租户成员或未注册 Provider 的提交分别收到权限拒绝或稳定的 Provider 不可用错误

#### Scenario: Provider list never exposes credentials
- **WHEN** 任意租户身份读取 Provider 清单
- **THEN** 响应只包含名称、显示名、支持协议、能力和执行模式，不包含命令行、SecretRef 或秘密值
