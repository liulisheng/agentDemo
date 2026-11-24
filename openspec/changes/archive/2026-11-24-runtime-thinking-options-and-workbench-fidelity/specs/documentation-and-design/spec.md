# Spec Delta

## ADDED Requirements

### Requirement: Prototypes are the user-visible fidelity baseline
系统变更 SHALL 以 `docs/原型/` 中对应 HTML 的布局、视觉令牌、交互文案、状态和示例数据为验收基线；实现不得另起一套未记录的视觉或状态语义。

#### Scenario: Implement a workbench change
- **WHEN** 工作台增加回答策略、思考阶段或审批状态
- **THEN** 对应原型、中文需求、流程图和实现测试同时描述同一用户路径

#### Scenario: Prototype and implementation differ
- **WHEN** 当前 React 页面或 API 与原型的字段、布局、状态或文案不一致
- **THEN** 任务不得标记完成，必须先更新提案/原型/设计或按已批准的差异记录原因

### Requirement: Design documents distinguish implemented and planned behavior
系统 SHALL 在中文设计文档和原型中明确区分已实现、已验证、方案设计、待接入和未验证，不把静态演示按钮或供应商推断写成已实现能力。

#### Scenario: Runtime capability is not verified
- **WHEN** DeepSeek、Codex 或其他引擎的思考参数尚未完成契约验证
- **THEN** 文档和原型标记为方案/未接入，记录验证命令和限制

#### Scenario: Complete the change
- **WHEN** 实现任务准备标记完成
- **THEN** 原型、流程/状态图、API/数据说明、测试结果和未验证项均已同步
