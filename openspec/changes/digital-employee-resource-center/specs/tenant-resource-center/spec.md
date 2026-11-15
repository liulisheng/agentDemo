# Spec Delta

## Purpose

让租户发布不可变的数字员工、Skill 和 Tool 资源版本，并由其他授权租户选择复制或只读引用，同时保留来源、版本和审计记录。

## ADDED Requirements

### Requirement: 发布到资源中心的版本保持不可变

系统 SHALL 以发布方租户和精确版本创建资源中心资源快照，并记录其依赖清单和内容摘要；已发布快照不得原地修改。

#### Scenario: 发布租户资源版本
- **WHEN** 发布方管理员选择已发布的员工、Skill 或 Tool 版本并提交资源中心信息
- **THEN** 系统生成带依赖清单和摘要的不可变资源中心版本

#### Scenario: 修改已发布资源中心版本
- **WHEN** 发布方尝试改写已发布的资源内容或依赖
- **THEN** 系统拒绝修改并要求创建新的资源中心版本

### Requirement: 资源中心发现和导入受发布策略控制

系统 SHALL 让平台内租户发现已发布资源的脱敏目录元数据；发现不等于读取资源包或导入，COPY/REFERENCE 必须具有对应接收租户和导入模式的有效授权。

#### Scenario: 无 Grant 租户发现平台资源
- **WHEN** 租户浏览资源中心且没有目标资源 Grant
- **THEN** 系统展示已发布资源的名称、类型、发布方和版本摘要，但不返回包内容或可导入内容地址

#### Scenario: 无授权租户尝试导入
- **WHEN** 租户对目标资源没有 COPY 或 REFERENCE Grant，并提交导入请求
- **THEN** 系统拒绝导入且不暴露发布方租户的内部记录

#### Scenario: 按授权模式导入
- **WHEN** 接收租户选择其被授权的 COPY 或 REFERENCE 模式
- **THEN** 系统创建租户范围的导入记录并返回来源版本及摘要

### Requirement: COPY 创建租户独立资源

系统 SHALL 将 COPY 导入物化为接收租户自己的资源版本和对象；导入后来源资源更新不得自动修改该租户副本。

#### Scenario: 导入资源副本
- **WHEN** 接收租户获准 COPY 并导入一个固定资源版本
- **THEN** 系统创建新的租户资源标识和版本，保留来源 Hub ID、版本和摘要作为溯源信息

#### Scenario: 来源发布新版
- **WHEN** 发布方发布了新的资源中心版本
- **THEN** 已存在的租户副本保持不变，只有显式拉取操作才创建新的租户版本

### Requirement: REFERENCE 固定版本并在 Run 中只读

系统 SHALL 为 REFERENCE 创建租户侧代理，固定 Hub 资源 ID、版本和摘要；Run 只能读取该版本，不能改写共享资源。

#### Scenario: Run 使用 Hub 引用
- **WHEN** 员工草稿绑定有效的 Hub REFERENCE 并启动 Run
- **THEN** 系统校验授权和摘要，将 Hub 版本写入 RunSnapshot，并向 Runtime 提供只读内容

#### Scenario: 编辑 Hub 引用内容
- **WHEN** 用户在资源中心或员工编辑页修改 REFERENCE 资源文件
- **THEN** 系统拒绝原地修改，并提供创建租户副本的路径

### Requirement: Hub 版本升级和授权撤销显式生效

系统 SHALL 通过显式升级创建新的导入版本；授权撤销阻止之后的导入和新 Run 使用，不得改写既有 RunSnapshot。

#### Scenario: REFERENCE 升级
- **WHEN** 接收租户选择新的已授权 Hub 版本
- **THEN** 系统创建新的租户代理版本，已有员工版本和 RunSnapshot 保持原版本

#### Scenario: 撤销授权
- **WHEN** 授权方撤销接收租户的资源权限
- **THEN** 系统拒绝新的导入或 Run 解析该 Hub 资源，已创建 Run 的快照不变

### Requirement: 共享 Tool 不传递发布方凭据

系统 SHALL 在资源包中排除发布方 Secret 明文和租户专属 SecretRef，REFERENCE Tool 的执行凭据由接收租户绑定。

#### Scenario: 导入含 SecretRef 的 Tool
- **WHEN** 接收租户 COPY 或 REFERENCE 一个使用凭据的 Tool
- **THEN** 系统不返回发布方凭据，且要求接收租户配置自己的 SecretRef 后才允许发布员工版本
