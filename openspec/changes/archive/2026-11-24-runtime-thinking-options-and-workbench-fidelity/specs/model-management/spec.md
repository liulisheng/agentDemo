# Spec Delta

## ADDED Requirements

### Requirement: Runtime strategy is configured independently from engine and model
系统 SHALL 在默认运行环境和 Deployment 运行配置中独立呈现回答策略，不把思考模式、推理强度、答复详细程度或预算字段混入 EngineProfile 或 ModelRoute。

#### Scenario: Administrator configures answer strategy
- **WHEN** 管理员打开默认运行环境或员工 Deployment 配置
- **THEN** 页面显示思考模式、推理强度、答复详细程度和推理预算，并明确配置只影响新 Session

#### Scenario: Member views strategy configuration
- **WHEN** 普通成员打开运行配置
- **THEN** 成员可以查看有效回答策略和来源，但不能修改租户默认或 Deployment 覆盖

#### Scenario: Strategy fields are not yet supported by the API
- **WHEN** 后端尚未注册对应 EngineProvider capability
- **THEN** 页面将策略字段标记为方案或不可用状态，不伪装保存成功

### Requirement: Strategy compatibility is visible before Session creation
系统 SHALL 在保存或创建 Session 前校验回答策略与 EngineProvider/ModelRoute capability，并向用户显示可定位的兼容性结果。

#### Scenario: Deep mode is compatible
- **WHEN** 所选引擎和模型均支持深度思考及所需预算
- **THEN** 页面显示可用并允许保存，Session 创建时固定该策略

#### Scenario: Deep mode is incompatible
- **WHEN** 所选引擎或模型不支持深度思考
- **THEN** 页面显示不兼容原因并阻止创建 Session，不自动换模型或降级策略
