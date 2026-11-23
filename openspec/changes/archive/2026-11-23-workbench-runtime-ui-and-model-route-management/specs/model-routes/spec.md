# Spec Delta

## ADDED Requirements

### Requirement: ModelRoute updates pin a new route version without changing existing snapshots

系统 SHALL 为租户管理员提供 ModelRoute 编辑能力；有效编辑递增当前路由版本，新 Session 使用新版本，已创建 Session/RunSnapshot 保持原路由快照。

#### Scenario: Edit an active route
- **WHEN** 当前租户管理员基于最新路由版本保存别名、允许的连接目录模型或非敏感参数变更
- **THEN** 系统递增 `route_version`，后续 Session 解析新路由；已有 RunSnapshot 的模型、连接和凭据引用不变

#### Scenario: Edit using a stale route version
- **WHEN** 管理员基于已被其他管理员更新的路由版本提交编辑
- **THEN** API 返回稳定的版本冲突，不覆盖较新的路由配置

#### Scenario: Member edits a route
- **WHEN** 当前租户普通成员尝试编辑路由
- **THEN** API 返回 403，路由和版本保持不变

### Requirement: ModelRoute logical deletion preserves references and historical execution

系统 SHALL 将管理员删除操作持久化为 `REVOKED` 终态，不物理删除路由行或历史快照；REVOKED 路由不得用于新的默认绑定、Deployment 覆盖、Session 或 Run。

#### Scenario: Revoke an unreferenced route
- **WHEN** 租户管理员删除未被可运行配置引用的路由
- **THEN** 路由状态变为 `REVOKED`，路由行保留以满足审计和历史外键，且该路由从后续可选目录中移除

#### Scenario: Revoke a route referenced by a default or deployment
- **WHEN** 路由仍被作用域默认、当前 Deployment 修订，或新 Session 可消费的已发布 Profile 历史路由字段引用
- **THEN** API 返回 `MODEL_ROUTE_IN_USE`（409）和当前租户内的引用说明，不改变路由状态

#### Scenario: Revoke a route with historical RunSnapshots
- **WHEN** 路由已被历史 Session/RunSnapshot 固定，但没有当前可运行配置引用
- **THEN** 系统允许路由进入 `REVOKED`；历史 Run/Trace 仍可读取快照中的路由与模型信息

#### Scenario: Repeat deletion of a revoked route
- **WHEN** 管理员再次删除已为 `REVOKED` 的同一路由
- **THEN** API 返回当前 REVOKED 状态且不重复产生状态变更审计

#### Scenario: Member or another tenant revokes a route
- **WHEN** 普通成员删除路由，或其他租户访问该 route ID
- **THEN** 前者收到 403，后者按资源不存在返回 404；均不改变路由状态或泄露引用信息
