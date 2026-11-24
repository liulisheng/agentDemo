# Spec Delta

## ADDED Requirements

### Requirement: Workbench displays answer strategy and runtime phases
系统 SHALL 在工作台 Run 快照和时间线中显示实际回答策略，并把思考、工具、答复和审批阶段作为可理解的用户可见事件展示。

#### Scenario: Deep thinking run is displayed
- **WHEN** Run 使用深度思考、高推理强度和标准答复策略
- **THEN** 工作台显示这些策略摘要，并按顺序显示“思考中”“调用工具”“生成答复”等阶段

#### Scenario: Raw reasoning is not displayed
- **WHEN** Runtime 产生内部思考内容
- **THEN** 工作台只显示阶段、状态和安全摘要，不显示原始思考链或隐藏 Prompt

#### Scenario: Strategy changes after Session creation
- **WHEN** 管理员修改 Deployment 默认回答策略后打开旧 Conversation
- **THEN** 工作台继续显示旧 Session/Run 的固定策略，并提示新配置只对新 Session 生效

### Requirement: Workbench event times have second precision
系统 SHALL 为 Conversation 列表、消息、思考阶段、工具事件、审批和 Run 状态显示本地时间到秒，并以服务端顺序字段处理同秒事件。

#### Scenario: Display a message timestamp
- **WHEN** 工作台加载包含 `created_at` 的用户或助手消息
- **THEN** UI 显示包含日期、小时、分钟和秒的本地时间

#### Scenario: Multiple events share a second
- **WHEN** 思考、工具和结果事件的本地时间相同到秒
- **THEN** UI 按消息 `sequence_no` 或 Trace `sequence` 保持确定顺序

#### Scenario: Loading, empty, error and read-only states
- **WHEN** 对话、消息或 Run 查询处于加载、空、失败或成员只读状态
- **THEN** UI 使用与现有原型一致的浅色管理台样式展示对应状态，不显示可执行的管理员操作
