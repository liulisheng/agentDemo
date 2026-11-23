# Tasks

## 1. ModelRoute lifecycle API

- [x] 1.1 先为 ModelRoute 编辑与删除补服务层单测：期望版本匹配递增、过期版本冲突、REVOKED 幂等、默认/活动 Deployment/可用旧 Profile 引用时 409、成员 403；先运行测试确认缺失行为导致失败。
- [x] 1.2 实现 ModelRoute REVOKED 状态流、引用检查、租户审计和原子仓储操作；验证服务层单测转绿，REVOKED 不可重新启用。
- [x] 1.3 为 PUT 增加 `expected_route_version`，新增 DELETE 逻辑撤销 API；验证过期版本、跨租户 404、成员 403、引用冲突和重复 DELETE 响应。
- [x] 1.4 为默认 ModelRoute 和 Deployment 配置写入补同事务路由锁/状态复查；验证并发绑定与撤销只能一个成功，且撤销失败不改变路由版本或审计。
- [x] 1.5 更新旧 Profile 路由 fallback、REVOKED selectable 过滤和 Deployment 重新上线校验；验证被撤销路由不能用于新 Session，历史 RunSnapshot 仍可读。

## 2. ModelRoute 页面操作

- [x] 2.1 先为 ModelRoute 页添加创建入口、创建/编辑表单、逻辑删除确认、REVOKED 状态和成员只读组件测试；确认缺失操作的用例失败。
- [x] 2.2 扩展前端 API client：路由更新发送期望版本，新增逻辑删除调用；为 `MODEL_ROUTE_IN_USE`、版本冲突和权限错误提供可读反馈，并通过 client 测试。
- [x] 2.3 在 ModelRoute 页实现创建、编辑、删除操作；创建表单从本租户连接/目录选择模型，编辑保留当前引用且显示新版本，REVOKED 行只读；运行 ModelManagementPage 测试。
- [x] 2.4 保留独立“测试模型”“停用/启用”流程；确认删除需要单独确认，停用可恢复，REVOKED 不可恢复，模型测试仍需显式确认。

## 3. 原型对齐与运行配置页面

- [x] 3.1 先补默认运行环境页的布局/角色/空状态组件测试和工作台配置弹窗的模式/独立选择测试；确认当前组件结构与原型验收点不符。
- [x] 3.2 将默认运行环境页调整为原型的默认值表单与 Profile 目录结构；验证用户/租户引擎和模型默认独立保存、成员只能保存个人值、窄屏按序排列。
- [x] 3.3 将工作台弹窗调整为原型的继承/覆盖切换及有效值来源摘要；验证只覆盖引擎、只覆盖模型、二者均覆盖、成员只读、旧 Session 快照不变。
- [x] 3.4 更新 `docs/原型/员工Deployment运行配置.html` 的 ModelRoute 创建/编辑/逻辑删除交互和依赖冲突、错误、空、加载状态；验证静态演示、键盘操作和窄屏可读。
- [x] 3.5 更新 `docs/工作台运行配置方案.md` 和 `docs/验证记录.md`，写明 REVOKED 生命周期、引用边界、无 schema migration、已验证项和未验证项。

## 4. 集成验收

- [x] 4.1 运行后端单测、MySQL ModelRoute 生命周期/并发集成测试、前端相关 Vitest、Ruff 和 Vite build；记录命令与结果。
- [x] 4.2 在本地 `/enterprise/models?tab=routes` 和 `/enterprise/models?tab=runtime-defaults` 验证管理员/成员视图、创建/编辑/删除冲突、默认配置布局及空/失败反馈；在 `/enterprise/workbench` 验证继承与独立覆盖弹窗。
- [x] 4.3 运行 `openspec validate --changes --strict`、`openspec validate --specs --strict` 和 `git diff --check`；更新任务状态与中文验证文档。

## Workflow follow-up

- 实现与验收完成后同步主规格并归档本 OpenSpec Change。
