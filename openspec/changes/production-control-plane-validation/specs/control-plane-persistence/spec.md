# Spec Delta

## Purpose

确保控制面资源在本地 MySQL 中可靠保存，并在服务重启、请求重试和 Worker 租约变化后维持租户隔离与可审计状态。

## ADDED Requirements

### Requirement: Control-plane resources persist across process restarts
系统 SHALL 将已创建的数字员工、不可变版本、Skill、Tool、ExecutionProfile、ModelRoute、Deployment、Conversation、Session、Run、Trace 和 Usage 记录持久化到 MySQL。

#### Scenario: Read resources after API restart
- **WHEN** API 在资源写入成功后重启并收到同一租户的查询
- **THEN** 系统返回重启前已提交的资源和状态

### Requirement: Capability bindings preserve immutable resource versions
系统 SHALL 持久化数字员工版本、Skill 版本、Tool 声明和 ExecutionProfile 之间的租户范围绑定及其顺序。

#### Scenario: Resolve a published employee composition after restart
- **WHEN** API 在员工版本和 Skill/Tool 绑定提交后重启
- **THEN** 系统按原顺序恢复同一组已发布资源版本，不将绑定解析到较新的草稿版本

### Requirement: Persistent queries are tenant scoped
系统 SHALL 使用认证上下文中的 platform_id 和 tenant_id 限定每次控制面资源读取和写入。

#### Scenario: Cross-tenant resource lookup
- **WHEN** 用户使用自己的身份查询其他租户的资源 ID
- **THEN** 系统按资源不存在处理且不泄露资源是否存在

### Requirement: Schema migrations are explicit and ordered
系统 SHALL 通过显式部署迁移按版本顺序更新 MySQL schema，应用启动不得自动应用迁移。

#### Scenario: Start against an unmigrated database
- **WHEN** 应用连接的数据库缺少所需 schema version
- **THEN** 应用报告明确的 schema 不匹配错误且不创建或修改数据库对象

### Requirement: New schema objects have Chinese descriptions
本 Change 新增或变更的 MySQL 表和字段 SHALL 在数据库元数据中提供准确的中文 COMMENT 描述。

#### Scenario: Inspect migrated schema comments
- **WHEN** migration 在 MySQL 8.4 成功执行
- **THEN** information_schema 中本 Change 新增或变更的每张表和每个字段都能查询到非空中文 COMMENT

### Requirement: Run submission is idempotent
系统 SHALL 在租户范围内按幂等键和请求摘要识别 Run 重试。

#### Scenario: Retry a Run submission
- **WHEN** 相同租户使用相同幂等键和相同请求内容重试
- **THEN** 系统返回原 Run 且不创建第二个 Run 或重复执行

#### Scenario: Reuse a key with changed content
- **WHEN** 相同租户复用幂等键但请求摘要发生变化
- **THEN** 系统返回稳定的幂等冲突且保留原 Run

### Requirement: Stale workers cannot change persistent run state
系统 SHALL 使用单调递增的 fencing token 限制 Worker 对 Session 和 Run 持久状态的更新。

#### Scenario: Worker writes with an expired lease
- **WHEN** 较旧 Worker 使用已失效的 fencing token 更新心跳、事件或最终结果
- **THEN** 系统拒绝该写入且不覆盖较新 Worker 已保存的状态

### Requirement: Related run state commits atomically
系统 SHALL 将 Run 状态、版本快照、Trace/Usage 索引和结果引用按声明的事务边界提交，避免部分成功状态对查询可见。

#### Scenario: Persistence transaction rolls back
- **WHEN** 保存 Run 完成状态时任一必须写入的数据库操作失败
- **THEN** 系统回滚该事务且查询不到伪造的成功状态

### Requirement: Unknown runtime outcome remains explicit
系统 SHALL 在无法确认 Runtime 是否完成时将 Run 保留为 UNKNOWN 或等价不确定状态，不自动重新提交可能有副作用的请求。

#### Scenario: Worker disappears during execution
- **WHEN** Worker lease 过期且没有可验证的 Runtime 完成结果
- **THEN** 系统记录 UNKNOWN 并允许恢复流程处理，但不得自动创建重复 Runtime 执行
