# Spec Delta

## Purpose

让租户管理员能够在员工详情中维护数字员工资料、Skill/Tool 版本绑定、发布和部署，并在服务重启后继续查看相同的版本与部署状态。

## ADDED Requirements

### Requirement: 管理台默认入口使用当前管理页面

系统 SHALL 将根路径打开到数字员工管理台，并将旧开放 Gallery 路径迁移到资源中心；用户不应落入旧 Gallery 的占位菜单。

#### Scenario: 打开应用根路径
- **WHEN** 用户访问管理台根路径
- **THEN** 系统导航到数字员工管理页

#### Scenario: 访问旧 Gallery 地址
- **WHEN** 用户打开 `/workspace/gallery` 或 `/enterprise/platform`
- **THEN** 系统重定向到“资源中心”页面，不显示“开放广场平台”旧页面

### Requirement: 员工目录按当前租户管理

系统 SHALL 按认证得到的平台和租户范围创建、查询、编辑和归档数字员工；普通租户成员只能读取有权资源，不能执行管理写操作。

#### Scenario: 管理员创建员工
- **WHEN** 当前租户管理员提交有效的员工名称、岗位和用途描述
- **THEN** 系统创建当前租户的员工草稿并返回员工标识

#### Scenario: 跨租户读取员工
- **WHEN** 请求中的员工不属于当前平台和租户
- **THEN** 系统按资源不存在处理，不返回其他租户的员工信息

### Requirement: 员工版本编辑保持不可变性

系统 SHALL 允许租户管理员为未归档员工创建和编辑 DRAFT 版本。PUBLISHED 和 DEPRECATED 的配置只读；后续修改必须分叉为新版本。

#### Scenario: 编辑现有草稿
- **WHEN** 管理员打开并保存未归档员工的 DRAFT 版本
- **THEN** 系统更新同一版本号，保留未修改的精确 ExecutionProfile ID/版本、ModelRoute、Workspace/Sandbox Profile ID/版本和审批策略；目录只有较新 Profile 时，不自动升级或使用新版本策略预览

#### Scenario: 基于已发布或已下线版本创建新草稿
- **WHEN** 管理员显式从 PUBLISHED 或 DEPRECATED 员工版本创建草稿
- **THEN** 系统在事务内分配新版本号，并完整复制系统指令、精确 Skill/Tool 绑定及顺序、运行和审批配置；源版本及其历史引用保持不变

#### Scenario: 尝试修改只读版本
- **WHEN** 管理员提交对 PUBLISHED 或 DEPRECATED 员工版本的修改
- **THEN** 系统拒绝修改并返回 `IMMUTABLE_VERSION`

#### Scenario: 基于已发布版本定向添加资源
- **WHEN** 管理员从已发布版本的 Skills 或 Tools Tab 发起添加操作
- **THEN** 系统创建完整继承原系统指令、Skills、Tools、运行配置和审批配置的新 DRAFT，再执行对应类型的绑定操作；原版本保持不变

### Requirement: 员工资源绑定按类型独立更新

系统 SHALL 为 Skill 与 Tool 绑定提供类型专属接口；每个接口只能更改 DRAFT 员工版本对应的绑定表和 capability slot，不能覆盖系统指令、另一类型绑定或运行配置。

#### Scenario: 定向更新 Skill 绑定
- **WHEN** 管理员提交一个 DRAFT 的 Skill 精确版本清单
- **THEN** 系统只替换该版本的 Skill 绑定和 Skill capability bindings，并保留其 Tool 绑定、系统指令、运行配置及状态

#### Scenario: 定向更新 Tool 绑定
- **WHEN** 管理员提交一个 DRAFT 的 Tool 精确版本清单
- **THEN** 系统只替换该版本的 Tool 绑定和 Tool capability bindings，并保留其 Skill 绑定、系统指令、运行配置及状态

#### Scenario: 定向更新角色与版本限制
- **WHEN** 租户成员写入绑定、跨租户资源被引用或目标员工版本为 PUBLISHED/DEPRECATED
- **THEN** 系统分别拒绝为 403、404 或 `IMMUTABLE_VERSION`，且不产生部分绑定

### Requirement: 员工版本固定 Skill 和 Tool 版本

系统 SHALL 在员工草稿中保存 Skill、Tool 的租户资源标识、精确版本、顺序和来源模式；运行时使用员工允许的 Tool 与 Skill 声明 Tool 的交集。

#### Scenario: 保存草稿绑定
- **WHEN** 管理员在草稿中添加 Skill/Tool 并保存
- **THEN** 系统记录精确版本和顺序，且详情返回可复现的固定引用

#### Scenario: 展示有效 Tool 范围
- **WHEN** 员工版本包含 Skill 声明范围和员工 Tool allowlist
- **THEN** 管理台展示两者交集作为该版本的有效 Tool 范围

### Requirement: 员工上下文可创建和管理 Skill/Tool

系统 SHALL 允许租户管理员在员工配置上下文创建 Skill 和 Tool、维护 DRAFT Tool 定义并发布固定版本；租户成员可以查看，但不能创建、编辑或发布。

#### Scenario: 创建并发布 Tool
- **WHEN** 管理员在员工详情的 Tool 管理页创建名称、描述和 JSON Schema 有效的 Tool
- **THEN** 系统创建 DRAFT Tool，允许管理员更新 DRAFT 字段并在确认后发布为不可变版本

#### Scenario: 从员工 Skills Tab 创建 Skill 草稿
- **WHEN** 管理员从员工 Skills Tab 创建 Skill 并提交有效的初始信息
- **THEN** 系统创建独立 Skill DRAFT 并打开该 Skill 文件工作区；发布后管理员再从员工 Skills Tab 绑定精确版本，成员不能执行写入

### Requirement: 员工工作台按资源类型独立分页

系统 SHALL 在员工详情提供概览、每个已启用资源类型的独立 Tab、版本与发布 Tab；Skills、Tools 和未来资源类型分别呈现数量、状态和操作，不把不同类型混入同一个绑定列表或表单。

#### Scenario: 切换 Skill/Tool 资源 Tab
- **WHEN** 员工版本同时绑定多个 Skill 和 Tool
- **THEN** 员工详情分别显示 Skills、Tools Tab，各自只显示该类型资源并保持精确版本和对应操作

#### Scenario: 编辑员工版本
- **WHEN** 管理员打开 DRAFT 员工版本编辑器
- **THEN** 系统指令、Skills、Tools 和运行配置位于独立、留白充足的配置区；可选高级环境参数默认收起

### Requirement: 发布前验证员工版本依赖

系统 SHALL 在发布员工版本前验证 Skill、Tool、ExecutionProfile 和被引用 Hub 资源的发布状态、租户授权及版本摘要。

#### Scenario: 所有依赖有效
- **WHEN** 草稿中的所有依赖均已发布、授权有效且版本摘要匹配
- **THEN** 系统将员工版本发布为不可变版本

#### Scenario: 依赖缺失或 Hub 授权失效
- **WHEN** 草稿引用未发布依赖、无权 Hub 资源或不匹配摘要
- **THEN** 系统阻止发布并返回逐项可定位的依赖错误

### Requirement: 新建 Deployment 和回滚只能选择已发布版本

系统 SHALL 只允许管理员在新建 Deployment 或回滚时选择 PUBLISHED 员工版本；历史 Deployment 可以继续固定并运行 DEPRECATED 版本，部署或回滚不得改写既有 Run 的版本快照。

#### Scenario: 回滚 Deployment
- **WHEN** 管理员把 Deployment 回滚到员工旧的 PUBLISHED 版本
- **THEN** 系统更新 Deployment 指针并记录审计事件，已有 Run 继续保留创建时的版本快照

#### Scenario: 回滚 Deployment 到非 PUBLISHED 版本
- **WHEN** 管理员选择 DRAFT 或 DEPRECATED 的员工版本作为回滚目标
- **THEN** 系统返回 `DEPENDENCY_VALIDATION_ERROR`（409），部署保留当前版本

### Requirement: 员工版本下线不改变已有部署

系统 SHALL 允许租户管理员把未归档员工的 PUBLISHED 版本下线为终态 DEPRECATED，并在同一持久化事务记录 `EMPLOYEE_VERSION_DEPRECATED`。该版本不得重新发布，也不得用于新建部署、回滚或重新上线；已有 Deployment 保持当前状态。

#### Scenario: 下线有 ACTIVE Deployment 的版本
- **WHEN** 管理员确认下线仍有 ACTIVE Deployment 的员工版本
- **THEN** 确认界面列出使用该精确版本的 ACTIVE Deployment 数量和标识，并引导管理员前往部署记录单独停用；服务端仅改变版本状态，历史 Deployment 继续可读，ACTIVE 入口仍可接收会话和运行

#### Scenario: 重试下线同一版本
- **WHEN** 管理员再次请求下线未归档员工的当前 DEPRECATED 版本
- **THEN** 返回当前版本且不重复写入审计；DRAFT 的下线请求返回 `INVALID_STATE`

### Requirement: Deployment 的独立下线与重新上线

系统 SHALL 允许租户管理员将 ACTIVE Deployment 下线为 DISABLED；仅当 Deployment 当前为 DISABLED、员工未归档且其固定版本仍为 PUBLISHED 时允许恢复为 ACTIVE。状态转换不得改变固定版本、会话或 RunSnapshot，并分别记录 `DEPLOYMENT_DISABLED` 和 `DEPLOYMENT_ENABLED`。

#### Scenario: 确认重新上线
- **WHEN** 管理员从已下线行打开重新上线确认
- **THEN** 页面重新读取当前部署、员工和精确版本；读取失败或任何状态不满足时禁用确认并说明原因，提交时服务端再次校验当前状态

#### Scenario: 部署下线后的准入
- **WHEN** 为 DISABLED Deployment 新建 Session，或为其已有 Session 发起全新的 Run
- **THEN** 返回 `DEPLOYMENT_DISABLED`（409），已通过准入的运行可以完成；匹配已提交 Run 的幂等重放仍返回原 Run

#### Scenario: 租户和角色校验
- **WHEN** 普通租户成员请求编辑、分叉、下线员工版本、启停部署或查询管理审计，或管理员操作其他租户的资源
- **THEN** 成员请求返回 403，跨租户操作按资源不存在返回 404；成员仍可查看本租户允许读取的版本和部署
