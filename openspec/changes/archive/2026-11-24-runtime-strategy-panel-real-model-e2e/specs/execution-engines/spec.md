# Spec Delta

## ADDED Requirements

### Requirement: OpenAI-compatible real model execution is explicitly selected
系统 SHALL 提供一个不经过 Fake Runtime 的真实 OpenAI-compatible ModelRoute 验收路径；Worker 通过受控 SecretRef 向配置的 Endpoint 发送受限请求，并返回标准 RuntimeResult。

#### Scenario: Real model request succeeds
- **WHEN** Worker 使用已授权 ModelRoute、可解析 SecretRef 和有效 RuntimeOptions 发送短请求
- **THEN** Run 返回真实模型答复、模型 ID、脱敏 provider request reference、Usage 和标准阶段事件，并标记 execution mode 为 NATIVE

#### Scenario: Real model credential is unavailable
- **WHEN** Worker 无法解析 ModelRoute 的 SecretRef
- **THEN** Run 以 `MODEL_SECRET_UNAVAILABLE` 或稳定凭据错误结束，不切换 Fake、不发送未授权请求

#### Scenario: Real model endpoint times out
- **WHEN**真实 Endpoint 在请求截止时间内没有确定响应
- **THEN** Run 进入现有 FAILED/UNKNOWN 语义，不自动重放可能已到达供应商的请求

### Requirement: Provider-specific strategy mapping remains capability gated
系统 SHALL 将平台 RuntimeOptions 通过 Adapter 映射为 OpenAI-compatible、DeepSeek Harness、Codex App Server 或 Fake 的私有输入；未经协议验证的字段必须在出站前被拒绝或标记未验证。

#### Scenario: OpenAI-compatible strategy is mapped
- **WHEN**真实 ModelRoute 支持平台要求的推理和输出字段
- **THEN** Adapter 只发送允许的供应商参数，并保留平台策略与请求的关联

#### Scenario: Unsupported budget is requested
- **WHEN** Adapter 未声明推理预算能力但面板提交了预算
- **THEN** 平台返回 `ENGINE_CAPABILITY_UNSUPPORTED`，不调用真实模型
