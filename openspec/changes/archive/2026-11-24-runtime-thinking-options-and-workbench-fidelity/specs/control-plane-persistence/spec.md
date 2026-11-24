# Spec Delta

## ADDED Requirements

### Requirement: Runtime strategy snapshots persist immutably
系统 SHALL 将 Session 和 Run 使用的回答策略及配置来源持久化为不可变快照，并确保历史 Run 可在服务重启后恢复。

#### Scenario: Restart after creating a strategy snapshot
- **WHEN** Session/Run 已保存回答策略后 API 或 Worker 重启
- **THEN** 系统恢复相同策略、来源、配置版本和 Engine/Model 引用

#### Scenario: Deployment strategy changes after a Run is queued
- **WHEN** 管理员在 Run 入队后保存新的回答策略
- **THEN** 已入队 Run 继续使用原策略，新策略只影响之后创建的 Session/Run

#### Scenario: Strategy references unsupported capability
- **WHEN** 持久化前校验发现策略超出 EngineProvider/ModelRoute capability
- **THEN** 事务拒绝保存新的可执行 Session/Run，不留下伪成功快照

### Requirement: Strategy snapshots remain tenant scoped and idempotent
系统 SHALL 按 platform、tenant、Session、Run 和幂等键隔离回答策略快照，并在重复提交时返回原快照而不重复执行。

#### Scenario: Retry the same strategy-bearing turn
- **WHEN** 同租户使用相同幂等键、请求摘要和回答策略重试
- **THEN** 系统返回原 Message/Run/Snapshot，不创建第二次 Runtime 调用

#### Scenario: Reuse a key with a changed strategy
- **WHEN** 同幂等键的回答策略或请求摘要发生变化
- **THEN** 系统返回幂等冲突，原快照保持不变

#### Scenario: Cross-tenant strategy lookup
- **WHEN** 用户查询其他租户的 Session/Run 策略快照
- **THEN** Repository 拒绝读取且不泄露策略详情
