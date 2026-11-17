# digital-employees Specification

## Purpose

为平台提供可配置、可发布、可部署的数字员工模板，并让每次运行绑定明确的不可变版本以支持审计、回滚和多业务线复用。

## Requirements

### Requirement: Digital employee has a stable identity
系统 SHALL 为数字员工维护稳定标识、名称、描述、归属租户、负责人、状态和可管理标签。

#### Scenario: Create employee draft
- **WHEN** 有权限的用户创建数字员工
- **THEN** 系统创建租户范围内唯一的草稿对象并返回稳定 id

### Requirement: Published version is immutable
系统 SHALL 将系统提示、Skill 引用、Tool 引用、ExecutionProfile、策略和运行参数固化为不可变的数字员工版本。

#### Scenario: Publish valid version
- **WHEN** 草稿的全部依赖和策略校验通过
- **THEN** 系统创建递增版本号并将版本状态设置为 PUBLISHED

#### Scenario: Edit published version
- **WHEN** 用户尝试直接修改已发布版本
- **THEN** 系统拒绝修改并要求创建新的草稿版本

### Requirement: Deployment pins a published version
系统 SHALL 通过部署对象将一个已发布版本绑定到平台、租户、项目或渠道，并在运行时使用该绑定版本。

#### Scenario: Deploy employee
- **WHEN** 管理员把已发布版本部署到租户
- **THEN** 系统创建 ACTIVE deployment 并记录部署策略和生效时间

#### Scenario: Roll back deployment
- **WHEN** 管理员选择较早的已发布版本回滚
- **THEN** 后续新 Run 使用回滚版本，已开始的 Run 保留原版本快照

### Requirement: Publish validates dependencies
系统 SHALL 在发布前校验引用的 Skill、Tool、ExecutionProfile、模型路由、工作区策略和审批策略均存在且允许在目标范围使用。

#### Scenario: Missing dependency
- **WHEN** 版本引用的 Tool 不存在或已被撤销
- **THEN** 发布失败并返回依赖校验结果

### Requirement: Digital employee composes executable skills
系统 SHALL 允许数字员工版本组合多个已发布 SkillVersion，并为每个版本固化 Skill 顺序、触发规则、输入映射和输出处理策略。

#### Scenario: Run selects a skill
- **WHEN** 用户消息匹配数字员工版本中的 Skill 触发规则
- **THEN** 系统创建 SkillExecution，并将该 Skill 的指令、Schema 和允许工具加入本次 Run 配置

#### Scenario: No skill matches
- **WHEN** 用户消息未匹配任何显式 Skill 规则且 Agent 没有默认 Skill
- **THEN** 系统不调用 Skill 工具流程，并返回可解释的未匹配结果或交给 Agent 默认处理策略

### Requirement: Run records the assembled agent snapshot
系统 SHALL 在 Run 开始前保存数字员工版本、SkillVersion、ToolVersion、ExecutionProfile、ContextPolicy 和 MemoryPolicy 的解析快照。

#### Scenario: Agent version changes during run
- **WHEN** 管理员在 Run 执行期间发布新的数字员工版本
- **THEN** 当前 Run 继续使用原快照，后续新 Run 才使用新版本
