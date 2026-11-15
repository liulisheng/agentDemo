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

系统 SHALL 允许租户管理员创建和编辑员工 DRAFT 版本；已发布版本不得原地修改，后续修改必须生成新的版本号。

#### Scenario: 基于已发布版本创建新草稿
- **WHEN** 管理员选择“创建新版本”并保存草稿
- **THEN** 系统分配新版本号，既有已发布版本及其绑定保持不变

#### Scenario: 尝试修改已发布版本
- **WHEN** 管理员提交对 PUBLISHED 员工版本的修改
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
- **WHEN** 租户成员写入绑定、跨租户资源被引用或目标员工版本为 PUBLISHED
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

### Requirement: Deployment 只能指向已发布版本

系统 SHALL 允许管理员部署已发布员工版本并回滚到已发布版本；部署或回滚不得改写既有 Run 的版本快照。

#### Scenario: 回滚 Deployment
- **WHEN** 管理员把 Deployment 回滚到员工旧的 PUBLISHED 版本
- **THEN** 系统更新 Deployment 指针并记录审计事件，已有 Run 继续保留创建时的版本快照
