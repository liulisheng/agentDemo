# Spec Delta

## ADDED Requirements

### Requirement: Independent runtime strategy panel shows effective values and overrides
系统 SHALL 提供可复用的独立运行策略面板，分别展示当前生效策略、来源、本次覆盖差异、EngineProvider capability 和仅影响新 Session 的说明；面板不得把回答策略字段写入 EngineProfile 或 ModelRoute。

#### Scenario: Administrator opens Deployment strategy panel
- **WHEN** 租户管理员从员工 Deployment 打开运行配置
- **THEN** 系统以双栏管理台布局展示思考模式、推理强度、答复详细程度、推理预算、当前生效值、来源和覆盖差异

#### Scenario: Member opens strategy panel
- **WHEN** 普通成员查看同一运行配置
- **THEN** 系统展示有效策略和来源但隐藏保存及新建配置操作，不返回 Secret 值

#### Scenario: Unsupported capability is selected
- **WHEN** 用户选择当前 EngineProvider 或 ModelRoute 不支持的深度思考或推理预算
- **THEN** 面板标记不支持原因并阻止保存，不静默降级或切换模型

#### Scenario: Strategy is saved with a new revision
- **WHEN** 管理员保存有效策略并确认只影响新 Session
- **THEN** 系统创建下一配置修订并返回解析后的策略、来源和可新建 Session 的上下文，旧 Session 保持原快照
