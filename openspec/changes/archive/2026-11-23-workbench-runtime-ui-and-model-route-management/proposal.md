# Proposal

## Why

“默认运行环境”和工作台“运行配置”虽已实现独立引擎/模型绑定，但当前布局与已确认的 HTML 原型不一致，影响管理员按原型验收和理解继承关系。ModelRoute 页也缺少创建、编辑和删除入口，路由日常维护需要绕回模型目录或无法完成。

## What Changes

- 将“默认运行环境”页按原型整理为默认值表单与 ExecutionProfile 版本目录的清晰布局，保留 EngineProfile/ModelRoute 独立保存、作用域来源、管理员权限及加载/空/错误反馈。
- 将工作台运行配置弹窗按原型展示“全部继承/设置员工覆盖”、有效默认来源和员工配置；EngineProfile 与 ModelRoute 可分别覆盖，切换引擎不自动替换模型，已固定 Session 不变。
- 在 ModelRoute 页增加创建入口和连接/目录模型选择；支持编辑路由别名、模型及非敏感参数，编辑后递增路由版本。
- 增加 ModelRoute 逻辑删除：状态转为 `REVOKED`，路由不再进入新的默认/Deployment 选择，历史 RunSnapshot 与审计继续可读。若仍被有效默认值、活动 Deployment 或会影响新 Session 的已发布旧 Profile 引用，拒绝删除并说明先解绑的位置。停用仍可恢复。
- 更新静态 HTML 原型、中文设计/验证文档和相关自动化验收。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `model-management`：默认运行环境 UI 对齐原型；ModelRoute 页支持创建、编辑、逻辑删除及权限反馈。
- `model-routes`：路由编辑版本与 REVOKED 状态不得改变既有 RunSnapshot；被运行配置引用时拒绝删除。
- `digital-employee-management`：工作台运行配置弹窗按原型呈现继承摘要及独立覆盖项。
- `platform-api`：新增/补齐 ModelRoute 逻辑删除接口、引用冲突和租户授权错误语义。

## Impact

- 前端：`ModelManagementPage`、`DefaultRuntimeEnvironmentTab`、`DeploymentRuntimeConfigurationDialog`、`ConversationWorkbench` 及组件测试。
- 后端：ModelRoute 管理 API/应用服务和仓储引用检查；现有 `REVOKED` schema 状态可复用，无需新增 migration。
- 文档：独立 HTML 原型、工作台运行配置方案、Docker/验证说明及 OpenSpec 主规格。
- 非目标：真实模型调用、新 EngineProvider/Claude 适配器、Workspace/Sandbox/审批策略接线、物理删除路由历史记录。

## 验收目标

- 两个运行配置页面的布局、入口和关键状态符合现有原型，窄屏可读且键盘可操作。
- ModelRoute 页可创建、编辑、测试、停用/启用和逻辑删除路由；越权、跨租户、存在活动引用及保存冲突均给出稳定反馈。
- 被编辑前创建的 Session/Run 仍使用原快照；被 REVOKED 的路由不能用于新的默认绑定或 Deployment 配置。
- 单元/集成/前端测试、严格 OpenSpec 校验、Vite build 和 `git diff --check` 通过。
