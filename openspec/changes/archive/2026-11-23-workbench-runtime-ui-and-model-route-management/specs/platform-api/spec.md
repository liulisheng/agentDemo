# Spec Delta

## ADDED Requirements

### Requirement: ModelRoute lifecycle API enforces version and reference boundaries

系统 SHALL 提供经认证租户上下文校验的 ModelRoute 创建、更新和逻辑删除接口；更新需比较期望版本，删除需原子检查可运行引用并审计，响应不得包含凭据明文。

#### Scenario: Update a route at the current version
- **WHEN** 当前租户管理员提交带匹配 `expected_route_version` 的有效路由更新
- **THEN** API 保存路由并返回递增后的 `route_version`，已固定的 RunSnapshot 不变

#### Scenario: Update with a stale route version
- **WHEN** 请求基于过期 `expected_route_version` 更新路由
- **THEN** API 返回 `MODEL_ROUTE_VERSION_CONFLICT`（409），且不覆盖最新路由

#### Scenario: Delete an unreferenced route
- **WHEN** 当前租户管理员删除未被可运行配置引用的路由
- **THEN** API 原子将路由状态改为 `REVOKED`、写入一次审计并返回保留的路由摘要，不执行物理删除

#### Scenario: Delete a referenced route
- **WHEN** 路由仍被作用域默认、当前 Deployment 修订或可供新 Session 使用的已发布 Profile 历史字段引用
- **THEN** API 返回 `MODEL_ROUTE_IN_USE`（409）及同租户内可安全展示的依赖摘要，不改变路由或引用

#### Scenario: Delete an already revoked route
- **WHEN** 管理员重复删除已撤销的路由
- **THEN** API 返回当前 `REVOKED` 状态，不重复递增版本或写入删除审计

#### Scenario: Member or cross-tenant deletion
- **WHEN** 普通成员删除路由，或请求引用另一个租户的 route ID
- **THEN** API 分别返回 403 或统一 404，不泄露跨租户资源与依赖信息

#### Scenario: Revoke races with a new runtime binding
- **WHEN** 路由删除与默认值或 Deployment 绑定并发提交
- **THEN** 路由行锁及同一事务使操作串行化；只能一个操作成功，另一个得到 `MODEL_ROUTE_IN_USE` 或稳定的配置冲突
