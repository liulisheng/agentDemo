# Spec Delta

## ADDED Requirements

### Requirement: Runtime accepts a canonical answer strategy
系统 SHALL 使用统一的回答策略描述思考模式、推理强度、推理预算、答复详细程度、输出上限、工具步骤上限和单轮超时，不要求 UI 直接填写供应商私有字段。

#### Scenario: Deep thinking is requested for a compatible engine
- **WHEN** Session 的 EngineProvider 和 ModelRoute 都声明支持深度思考，且 RunSnapshot 请求 `reasoning_mode=DEEP`
- **THEN** RuntimeAdapter 将统一策略映射为该引擎的私有请求，并保留平台 Run 与引擎请求的关联

#### Scenario: Engine does not support a requested strategy
- **WHEN** Run 请求的思考模式、推理强度或推理预算超出 EngineProvider/ModelRoute 能力
- **THEN** 平台在 Runtime 出站前返回明确能力错误，不静默降级、不切换模型、不调用 Fake Runtime

### Requirement: Engine adapters preserve common lifecycle and normalize phases
系统 SHALL 让 DeepSeek Harness、Codex App Server 和 Fake Runtime 通过同一 RuntimeRequest/RuntimeResult 生命周期交互，并将思考、工具、答复和错误阶段转换为统一事件。

#### Scenario: Adapter maps provider-specific input
- **WHEN** Worker 传入包含 EmployeeContext、ModelRoute、ToolDefinition、MCP 和回答策略的 RuntimeRequest
- **THEN** 对应 Adapter 生成供应商私有请求；平台不把供应商私有字段反向暴露给其他引擎

#### Scenario: Runtime reports thinking and tool phases
- **WHEN** Runtime 返回思考、工具调用、工具结果或最终答复事件
- **THEN** 平台生成关联 `session_id`、`run_id`、`trace_id` 的统一 RuntimeEvent，并可供工作台按顺序展示

#### Scenario: Runtime does not expose raw reasoning
- **WHEN** 引擎返回内部推理或隐藏上下文
- **THEN** 平台只保存阶段、状态、耗时和可用用量摘要，不保存或展示原始思考链
