# Spec Delta

## ADDED Requirements

### Requirement: Deployment runtime dialog follows the approved workbench prototype

系统 SHALL 在现有 `/enterprise/workbench` 当前员工旁提供与独立 HTML 原型一致的运行配置弹窗，清楚展示继承解析结果或员工独立覆盖状态。

#### Scenario: View inherited runtime configuration
- **WHEN** 管理员在工作台打开处于 `INHERIT` 的 Deployment 运行配置
- **THEN** 弹窗显示当前有效 EngineProfile、ModelRoute、各自来源和解析优先级，并提示保存默认只影响新 Session

#### Scenario: Override engine and model independently
- **WHEN** 管理员切换到员工覆盖并选择 EngineProfile 或 ModelRoute
- **THEN** 两个字段分别可独立设置或继承；更换 EngineProfile 不自动选择、清除或替换 ModelRoute

#### Scenario: No effective default or selectable route exists
- **WHEN** 管理员打开配置且有效默认或可选资源为空、读取中或读取失败
- **THEN** 弹窗提供与状态匹配的加载、空状态或可重试错误说明，保存无效配置不可用

#### Scenario: Member views runtime configuration
- **WHEN** 有运行权限的普通成员打开 Deployment 运行配置
- **THEN** 弹窗展示只读配置和来源，不提供可编辑控件或保存操作

#### Scenario: Save a new Deployment revision
- **WHEN** 管理员保存有效的新运行配置修订
- **THEN** 该配置只供新 Session 解析；当前 Conversation Session 和已创建 Run 继续使用原有快照
