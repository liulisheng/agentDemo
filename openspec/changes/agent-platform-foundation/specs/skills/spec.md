# Spec Delta

## Purpose

为数字员工提供可复用的业务行为和流程定义，使提示指令、输入输出契约、工具权限和审批要求能够独立管理并被多个数字员工版本引用。

## ADDED Requirements

### Requirement: Skill defines a reusable behavior
系统 SHALL 为 Skill 保存名称、说明、版本、输入 Schema、输出 Schema、指令内容、允许工具和执行策略。

#### Scenario: Create skill draft
- **WHEN** 用户创建 Skill 草稿并提交有效输入输出 Schema
- **THEN** 系统保存草稿并允许后续数字员工版本引用

### Requirement: Skill version is immutable after publish
系统 SHALL 为 Skill 提供草稿、发布和废弃状态，已发布版本的行为定义不可直接修改。

#### Scenario: Publish skill
- **WHEN** Skill 的 Schema、工具引用和策略校验通过
- **THEN** 系统生成不可变版本并允许数字员工版本引用

#### Scenario: Deprecated skill
- **WHEN** 管理员废弃已发布 Skill 版本
- **THEN** 新的数字员工版本不能引用该版本，已有部署继续使用其已固化版本直到被替换

### Requirement: Skill execution respects declared tools
系统 SHALL 只允许 Skill 使用其版本声明且经过目标部署授权的工具。

#### Scenario: Undeclared tool request
- **WHEN** Skill 执行过程中请求未声明的工具
- **THEN** 系统拒绝工具调用并记录 policy denial

### Requirement: Skill result follows its contract
系统 SHALL 校验 Skill 的输入和输出是否符合其版本 Schema，并在校验失败时返回可追踪的 Skill execution error。

#### Scenario: Invalid input
- **WHEN** 调用方传入不符合 Skill 输入 Schema 的数据
- **THEN** 系统在执行前拒绝请求且不调用模型或工具

### Requirement: Skill supports explicit execution types
系统 SHALL 支持 instruction、tool-bundle 和 workflow 三类 Skill，并为每类 Skill 保存相应的指令、工具集合或步骤定义。

#### Scenario: Instruction skill
- **WHEN** Agent 选择 instruction Skill
- **THEN** 系统将 Skill 指令加入 Context，并由 ExecutionEngine 生成结果

#### Scenario: Tool-bundle skill
- **WHEN** Agent 选择 tool-bundle Skill
- **THEN** 系统只向本次 Run 暴露该 Skill 声明且策略允许的工具

#### Scenario: Workflow skill
- **WHEN** Agent 选择 workflow Skill
- **THEN** 系统按照已发布步骤和条件执行，并为每一步生成可追踪的 SkillExecution 事件

### Requirement: Skill execution is observable
系统 SHALL 为每次 SkillExecution 记录 SkillVersion、输入引用、输出引用、触发来源、工具调用、模型调用、状态、耗时和错误类别。

#### Scenario: Skill execution failure
- **WHEN** Skill 的某一步工具调用失败
- **THEN** SkillExecution 标记失败并关联原始 ToolCall 和父 Run，且不伪造成功输出

### Requirement: Skill dependencies are policy intersections
系统 SHALL 将 Skill 声明的工具和权限与数字员工版本、租户策略和当前用户授权求交集后再执行。

#### Scenario: Skill requests forbidden tool
- **WHEN** Skill 声明的工具未被租户或 Agent Deployment 允许
- **THEN** Skill 发布或执行校验失败，工具不会进入模型可见工具集合
