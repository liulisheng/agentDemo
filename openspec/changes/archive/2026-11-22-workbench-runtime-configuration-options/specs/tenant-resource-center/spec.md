# Spec Delta

## MODIFIED Requirements

### Requirement: 共享员工包不绑定发布方运行环境

系统 SHALL 在资源中心员工包中共享员工指令、精确 Skill/Tool 依赖和必要能力要求，但排除发布方的 ExecutionProfile、ModelRoute、Workspace/Sandbox 资源引用、租户策略和凭据。

#### Scenario: 发布可移植员工包
- **WHEN** 发布方管理员将已发布员工版本发布到资源中心
- **THEN** 资源包不包含发布方 Profile/Route/Workspace/Sandbox 标识、版本或 SecretRef；可选能力要求不包含租户资源标识

#### Scenario: 接收方没有发布方模型或沙箱
- **WHEN** 获授权接收租户 COPY 或 REFERENCE 一个员工包，但没有与发布方相同的模型路由或沙箱
- **THEN** 系统仍完成员工资源导入，且不创建指向发布方租户运行资源的引用

#### Scenario: 新导入员工待配置
- **WHEN** 接收租户尚未为导入的员工 Deployment 配置本地运行环境
- **THEN** Deployment 保持 `PENDING_CONFIGURATION`，工作台提示配置本租户资源，且系统不创建 Session 或 Run

#### Scenario: 读取旧格式资源包
- **WHEN** 接收租户导入包含旧版 `execution_profile` 字段的已发布 Hub 员工包
- **THEN** 系统忽略其中发布方专属运行绑定，只导入员工内容与能力，并要求接收租户为新 Deployment 配置本地运行环境

#### Scenario: Import response prompts recipient configuration
- **WHEN** 接收租户成功导入 DIGITAL_EMPLOYEE 的 COPY 或 REFERENCE 包
- **THEN** 导入响应包含 `runtime_configuration_required=true` 和 `next_action=CONFIGURE_DEPLOYMENT_RUNTIME`；其他资源类型不要求此操作

#### Scenario: REFERENCE 保持只读且配置独立
- **WHEN** 接收租户通过 REFERENCE 使用共享员工版本并保存自己的 Deployment 配置
- **THEN** 系统只读共享员工内容，配置修订归接收租户 Deployment 所有，发布方更新或撤销不得改写接收方运行配置和既有 RunSnapshot
