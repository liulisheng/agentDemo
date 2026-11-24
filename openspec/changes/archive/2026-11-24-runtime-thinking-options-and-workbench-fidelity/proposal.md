# Proposal

## Why

当前原型已经明确了员工工作台、Deployment 运行配置、默认运行环境、思考模式和秒级时间线的目标体验，但平台规格与 Runtime 端口还没有把这些字段、状态和引擎差异映射固定下来。若直接实现，容易再次出现原型、React 页面、API 和不同执行引擎的入参不一致。

本 Change 以现有 `docs/原型/` 为用户可见行为基线，补齐类型化回答策略、平台到 RuntimeAdapter 的统一入参、不同引擎的映射边界、工作台阶段展示和秒级时间，并要求实现严格复现原型布局与状态。

## What Changes

- 将 Deployment/默认运行环境中的回答策略定义为类型化 RuntimeOptions：思考模式、推理强度、答复详细程度、推理预算、最大输出、最大工具步骤和单轮超时。
- 为用户/租户默认运行环境持久化回答策略绑定，保证默认运行环境原型中的保存动作和作用域解析真实可用。
- 在 Session 创建和 RunSnapshot 中固定有效回答策略；后续配置变更不得影响已有 Session/Run。
- 扩展 EngineProvider capability，明确引擎是否支持 reasoning、effort、budget、streaming 和 output limits。
- 通过统一 `RuntimeRequest` 传递平台语义字段，由 DeepSeek Harness、Codex App Server 和 Fake Adapter 分别映射；前端不直接使用供应商私有字段。
- 工作台严格按现有原型展示三栏布局、回答策略、思考中/调用工具/生成答复阶段、内部事件折叠、审批卡和 Run 快照。
- 对话列表、消息、思考阶段、工具事件、审批和 Run 状态显示本地时间到秒；同一秒内按服务端序号排序。
- 保持原始思考链不展示、不写入消息或审计正文；只记录阶段、摘要、耗时和可用的 reasoning token 用量。
- 将当前原型中的加载、空、错误、只读、成员权限、兼容性失败和 UNKNOWN 状态纳入实现验收。
- 严格按原型进行视觉回归：色板、字体层级、边框、卡片、状态徽标、间距、窄屏布局和键盘焦点行为不得另起一套风格。

## Capabilities

### New Capabilities

无。回答策略和工作台展示属于现有 Runtime、模型管理、工作台和观测能力的扩展。

### Modified Capabilities

- `execution-engines`：增加统一回答策略输入、引擎 capability 和 Provider-specific mapping；不支持能力必须显式拒绝。
- `model-management`：默认运行环境和 Deployment 配置增加回答策略的类型化字段、作用域和兼容性提示。
- `platform-api`：Session/Run 创建、Runtime 配置读取和工作台查询返回回答策略、阶段事件和秒级时间。
- `conversations-and-channels`：工作台消息和运行阶段展示回答策略与秒级时间，保持 Conversation 历史顺序。
- `execution-observability`：Trace/Usage 记录思考阶段、策略摘要、耗时和 reasoning token，不保存原始思考链。
- `control-plane-persistence`：Session/RunSnapshot 持久化回答策略，保证租户隔离、幂等和配置修订不可变。
- `documentation-and-design`：现有 HTML 原型成为验收基线，新增原型状态、流程、API、数据模型和验证记录的追踪要求。

## Impact

- **前端：** `ConversationWorkbench`、`ConversationWorkbenchPage`、`DeploymentRuntimeConfigurationDialog`、默认运行环境页、工作台样式和对应测试。
- **后端：** `DeploymentRuntimeConfigService`、`RuntimeRequest`、`RuntimeCapabilities`、`EngineProviderRegistry`、Session/Run 快照、RuntimeAdapter、Usage/Trace 序列化。
- **引擎适配器：** DeepSeek Harness、Codex App Server 和 Fake 需要分别声明能力和处理统一策略；真实供应商字段需契约测试验证。
- **数据库：** Deployment、Session 和 RunSnapshot 继续复用现有 `runtime_options` JSON；用户/租户默认回答策略需要新增租户范围的策略绑定表并提供中文 COMMENT migration。不得把思考正文写入 MySQL。
- **原型与文档：** 以 `员工Deployment运行配置.html`、`DeepSeekHarness默认引擎配置.html`、`海豚调度员工工作台.html` 和 `当前方案总览.html` 为视觉/交互基线，更新中文设计文档和验收记录。
- **不做的事情：** 不展示原始思考链；不允许引擎适配器绕过平台权限；不因不兼容自动换模型或静默降级；不改变已建立 Session 的运行配置。

## 分阶段边界

### 本期

- 类型化 RuntimeOptions、Session/Run 快照、能力校验和工作台展示。
- 原型中的思考模式、回答策略、阶段状态和秒级时间全部进入可验收实现。
- Fake 和确定性 Mock 完成适配器契约测试。

### 后续

- 真实 DeepSeek/Codex Provider 的 reasoning 参数、reasoning token 和长时恢复验证。
- 上下文自动压缩、成本预算、模型 fallback、工具失败重试策略。
- Workspace、Sandbox、网络策略和更细粒度租户限额。

## 验收目标

- 原型和实现的工作台视觉、布局、交互状态一致。
- 用户能看到本轮回答策略、思考中/调用工具/生成答复阶段以及到秒的时间。
- Session/Run 使用固定的回答策略快照；修改配置只影响新 Session。
- 不同引擎通过统一平台请求进入各自 Adapter；不支持的能力明确返回错误。
- 多租户、成员只读、审批提交者、UNKNOWN、不兼容模型和失败/空/加载状态均有测试和文档证据。
