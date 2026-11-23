# Spec Delta

## ADDED Requirements

### Requirement: MySQL runtime defaults consistently to DeepSeek Harness
系统 SHALL 在 MySQL/Compose 模式默认装配 DeepSeek Harness，并将同一运行引擎配置用于 API 的 EngineProvider 校验和 Run Worker 执行；Fake 仅在显式配置时作为运行引擎。

#### Scenario: Default MySQL engine
- **WHEN** 运维未设置 `AGENT_PLATFORM_RUNTIME_ENGINE` 并以 MySQL/Compose 模式启动服务
- **THEN** API 与 Run Worker 均使用 DeepSeek Harness，EngineProvider 清单只公布规范 ID `deepseek_harness`

#### Scenario: Explicit offline Fake override
- **WHEN** 运维显式设置 `AGENT_PLATFORM_RUNTIME_ENGINE=fake` 并重启 API 与 Run Worker
- **THEN** 两个服务均使用 Fake Runtime，Profile 清单不把 DeepSeek 标成可运行的原生引擎

### Requirement: DeepSeek Harness uses a canonical provider identity
系统 SHALL 以 `deepseek_harness` 作为新建 ExecutionProfile 的规范 EngineProvider ID，并兼容解析已保存的 `deepseek` 引擎别名。

#### Scenario: Create a canonical DeepSeek profile
- **WHEN** 管理员在 DeepSeek Harness 已注册的租户环境中创建并发布 `engine_name=deepseek_harness` 的 ExecutionProfile
- **THEN** Profile 固定版本可用于后续 Session，且 Provider 选择器只显示一个 DeepSeek Harness 选项

#### Scenario: Execute a legacy profile alias
- **WHEN** 已有运行快照引用 `engine_name=deepseek`
- **THEN** Worker 将该别名解析到同一个已注册 DeepSeek Harness Adapter，不创建第二个 Provider 项

#### Scenario: Selected route credential is unavailable
- **WHEN** DeepSeek Run 的租户 ModelRoute 引用 SecretRef，但 Worker 无法解析对应凭据
- **THEN** Run 以稳定的配置错误结束，不记录密钥明文，也不回退到 Fake Runtime
