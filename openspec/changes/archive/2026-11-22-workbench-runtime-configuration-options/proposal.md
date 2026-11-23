# Proposal

## Why

数字员工版本会进入资源中心供其他租户 COPY 或 REFERENCE，但运行配置中的 ModelRoute、ExecutionProfile、Sandbox 和 Workspace 属于接收方租户环境。接收方可能没有发布方的模型或沙箱；同时租户/用户可能设置全局默认引擎与模型，而不同员工又需要覆盖为不同引擎。运行配置因此需要同时支持作用域默认值和员工 Deployment 专属选择。

## What Changes

- 将运行配置从数字员工版本移至接收方自己的 Deployment；同一员工发布版本可在不同租户 Deployment 使用不同本地配置。
- 在现有“模型管理”页新增“默认运行环境”页签，分别设置用户/租户默认 EngineProfile 和 ModelRoute。选择引擎不会锁定模型；用户可以独立选择该 EngineProvider 支持的任一本租户授权 ModelRoute。
- 新建 ExecutionProfile 只绑定已注册引擎及当前 Worker 支持的策略，不要求绑定模型；既有 Profile 的 ModelRoute/`model_alias` 作为迁移兼容数据，独立 ModelRoute 优先。默认环境和 Deployment 都提供独立模型选择，换引擎不自动改模型。
- Deployment 可独立继承或覆盖 EngineProfile 与 ModelRoute；解析优先级分别为 Deployment 字段覆盖 > 用户默认 > 租户默认 > 平台/系统默认。
- ExecutionProfile 使用稳定 Profile ID 和版本：DRAFT 可编辑，PUBLISHED 不原地修改；从已发布方案创建同一 Profile 的下一 DRAFT 版本，修改并发布后显式更新默认/Deployment 绑定，避免每次修改新增 Profile ID。
- 在现有员工运行工作台增加轻量 Deployment 配置入口；管理员可分别选择本租户授权的引擎 Profile 与 ModelRoute，普通成员只读。Deployment 级 Workspace/Sandbox 与附加审批仍需 Worker 执行边界接通后再开放；本期 API 明确拒绝非空的未接入字段，避免保存后被忽略。
- 本期不执行 Deployment 级 Workspace/Sandbox 和附加审批策略；V009 保留历史兼容字段，但 API 拒绝新非空覆盖，且 Session 不允许历史覆盖值与精确 Profile 不一致。后续需接通 Workspace/Sandbox 生命周期和工具审批 Worker 才能开放这类覆盖。
- Deployment 在显式选择“继承默认”或“员工独立配置”后可运行；当当前用户无法解析出有效引擎/模型组合时，拒绝创建 Session 并提示配置来源。
- 员工版本发布和资源中心包不再要求、复制或引用发布方运行 Profile、模型路由、工作区、沙箱或凭据。COPY/REFERENCE 只导入员工定义和能力；接收租户配置自己的 Deployment。
- 用户/租户引擎默认与模型路由默认分别绑定；Deployment 覆盖配置按 Deployment 产生不可变修订。Session 创建时分别解析 EngineProfile 和 ModelRoute 并固定两个来源；已有 Session/Run 继续使用原快照。
- 通过 EngineProvider 注册表启用异构引擎；A 可选择 Codex CLI，B 可选择 DeepSeek Harness，C 可选择未来已验证的 Claude Code adapter。模型路由必须与引擎能力兼容，未注册或不兼容的组合拒绝执行。
- 为已存在 Deployment 迁移当前有效配置，保持升级前运行行为；新增 MySQL migration，按项目约定添加中文 COMMENT、租户复合约束和 `information_schema` 注释验证。
- 更新员工编辑器、Deployment/运行工作台、API、RunSnapshot、资源中心导入/发布流程、中文文档和独立 HTML 原型。

## Capabilities

### New Capabilities

无。运行配置属于既有数字员工 Deployment 与控制面能力。

### Modified Capabilities

- `digital-employees`：员工版本不再固定租户运行配置；Deployment 固定接收方运行配置修订。
- `digital-employee-management`：从员工版本编辑移除运行环境配置，在 Deployment 工作台维护。
- `tenant-resource-center`：资源包不携带发布方运行配置；接收租户可在没有相同 Profile/ModelRoute/Sandbox 时完成导入。
- `platform-api`：提供 Deployment 运行配置读取、保存和配置状态；未配置 Deployment 拒绝新 Session/Run。
- `control-plane-persistence`：持久化租户隔离的运行配置修订与迁移兼容。
- `conversations-and-channels`：新 Session 读取 Deployment 当前配置，已绑定 Session 保持原配置。
- `execution-engines`：Worker 从 RunSnapshot 使用接收方精确 EngineProvider/ModelRoute；当前不支持的 Workspace/Sandbox/附加审批配置会在保存或 Session 解析时明确拒绝，不从发布方员工包回退解析。
- `model-routes`：ModelRoute 与所选 EngineProvider 必须兼容；Run 快照保留独立解析结果。
- `model-management`：现有连接与路由页面增加“默认运行环境”页签，分别维护用户/租户 EngineProfile 与 ModelRoute 默认。

## Impact

- 后端：员工版本领域/仓储、资源中心包格式与导入、Deployment 生命周期、Run/Session Resolver、API、审计和 Worker。
- 数据库：新增 `deployment_runtime_config_versions` 和作用域 `model_route_bindings`，扩展 V003 `execution_profile_bindings` 的持久化 API；变更 `deployments` 状态/当前配置修订引用，允许新员工版本不引用执行 Profile，并将 `execution_profiles.model_alias` 改为可空。V009 回填历史 Deployment 和独立路由默认。SQL 统一放 `docs/migrations/`，新增/变更对象均写中文 COMMENT。
- 前端：员工版本编辑器移除 Profile/ModelRoute/Workspace/Sandbox 运行字段；现有模型管理页增加“默认运行环境”第三页签；工作台保留三栏布局，仅提供紧凑 Deployment 继承/覆盖对话框。
- 默认配置：引擎默认复用 `execution_profile_bindings`；ModelRoute 默认独立存入 `model_route_bindings`。新建 Profile 不绑定 ModelRoute；迁移时将历史 Profile 中的 ModelRoute 值回填到独立绑定。仅在没有独立模型默认时，旧 Profile 的 alias/route 可作为兼容回退。
- 资源中心：新包只描述可复用员工内容/Skill/Tool 依赖。旧包按兼容规则忽略发布方环境绑定，并提示接收方配置。
- 外部模型网关：本期不接入 Dadi/new-api。模型目录、授权、凭据解析和实际调用继续由现有 ModelRoute/Provider/Worker 边界负责。

## 分阶段交付与验收目标

1. **独立作用域默认与 Deployment 覆盖**：分别持久化用户/租户 EngineProfile 和 ModelRoute 默认；Deployment 可以单独覆盖其中一项或两项，验证各自优先级和来源。
2. **资源中心解耦**：新包不含发布方环境；接收租户没有同款模型/沙箱也可导入，并从全局默认或本地 Deployment 配置启动。
3. **异构引擎与工作台入口**：注册可用 EngineProvider，工作台轻量对话框支持继承默认或单员工覆盖；Session/Run 固定引擎、模型和解析来源。
4. **完整验证**：OpenSpec strict validation、后端/前端/MySQL migration 测试通过；原型覆盖作用域优先级、权限、空/加载/错误/运行结果和窄屏键盘操作。
