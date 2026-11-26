# Tasks

## 1. Conversation 模式与团队基础

- [x] 1.1 为 Conversation 增加 `SINGLE_AGENT`/`TEAM` 模式字段和领域校验，验证已有单员工 Conversation 默认保持 `SINGLE_AGENT` 且模式非法值被拒绝。
- [x] 1.2 实现 Team、TeamVersion、TeamMember、ConversationTeamBinding 领域模型和服务，验证草稿可编辑、发布后版本不可变、未发布版本不能绑定 Conversation。
- [x] 1.3 扩展 Conversation 创建和 `continue-as-team` 应用服务，验证单员工路径创建一个 Session、团队路径创建成员绑定且源 Conversation 不变。
- [x] 1.4 为模式路由、团队权限和跨租户资源添加单元测试，验证 Viewer/Member/Owner 的配置操作权限和跨租户查询拒绝。

## 2. MySQL Schema 与租户持久化

- [x] 2.1 在 `docs/migrations/` 创建团队、编排、任务、Attempt、事件、Artifact、Delivery 和 Worker 心跳 forward migration；每张表和每个字段写准确中文 `COMMENT`，验证 migration preflight 能识别新对象。
- [x] 2.2 为 `conversations`、`runs` 和 `agent_bindings` 增加模式/编排关联 nullable 字段、租户复合索引和状态约束，验证历史单员工数据可迁移且没有空租户范围。
- [x] 2.3 创建对应 down migration，验证回滚顺序不会删除仍被 Conversation、Run 或 Artifact 引用的对象。
- [x] 2.4 实现团队、编排、任务、依赖、Attempt、事件和 Artifact MySQL Repository；验证事务写入、platform_id/tenant_id 读写隔离、稳定游标分页和服务重启后读取。
- [x] 2.5 编写 MySQL 集成测试，查询 `information_schema.TABLES` 和 `information_schema.COLUMNS` 验证新增表名、字段名、中文表注释和中文字段注释，包括 V014 Worker 心跳。
- [x] 2.6 增加 V014 Worker 心跳前向迁移和 down 脚本；验证 preflight 对部分 schema 失败关闭、`information_schema` 中文注释、30 秒健康阈值和 Worker 停止状态更新。

## 3. Router Agent 与任务编排

- [x] 3.1 实现 OrchestrationRun、Task DAG 和 PlanRevision 服务，验证 Router 计划的任务数量、DAG 无环、成员范围、能力、预算和租户引用校验。
- [x] 3.2 实现 Hybrid 计划确认、USER/ROUTER 分配策略和用户成员锁定，验证未确认计划不派发子 Run，用户锁定成员不会被 Router 覆盖。
- [x] 3.3 实现 Task 状态机、依赖解锁和 TaskEvent 追加事件，验证 READY/ASSIGNED/CLAIMED/RUNNING/SUCCEEDED/FAILED/BLOCKED/CANCELLED 状态和非法跳转。
- [x] 3.4 实现 TaskAttempt 幂等、租约和 fencing token，验证同一幂等键不重复创建子 Run，过期 Worker 不能覆盖终态。
- [x] 3.5 实现 Orchestration 聚合、暂停、恢复、取消、失败继续和部分失败状态，验证取消停止新派发、运行中 Run 收到取消请求、已完成任务仍可交付。
- [x] 3.6 为 Router 计划、任务状态、取消、超时、重试和依赖失败编写服务层测试，验证错误码、状态事件和聚合计数可恢复。

## 4. 异构 Engine Worker Pool

- [x] 4.1 扩展 EngineProviderRegistry 装配多个 RuntimeAdapter 和按 `engine_name` 的 Worker Pool 注册，验证 Codex、DeepSeek、Fake 队列互不领取任务。
- [x] 4.2 扩展队列领取、Worker 心跳、租约和引擎健康查询，验证 Worker Pool 不可用时任务进入 `ENGINE_POOL_UNAVAILABLE` 并保留可重试状态。
- [x] 4.3 实现任务能力要求到成员 EngineProvider capabilities 的匹配，验证 Workspace、MCP、Tool、Reasoning、Approval 不支持时返回 `ENGINE_CAPABILITY_UNSUPPORTED` 且不启动 Runtime。
- [x] 4.4 扩展子 Run 的 RunSnapshot/Trace 关联，验证不同成员可以并行使用不同 EngineProvider/ModelRoute，运行中的 Session 不允许切换引擎。
- [x] 4.5 实现标准化 TaskOutput 上下文封装，验证跨引擎只传摘要、结构化结果和授权 Artifact 引用，不传 SecretRef、私有 Session 或原始思考链。
- [x] 4.6 编写 Worker Pool 路由、引擎能力、超时、凭据错误和取消测试，验证 `ENGINE_MODEL_INCOMPATIBLE`、稳定凭据错误和 FAILED/UNKNOWN 语义。

## 5. API、权限与可观测性

- [x] 5.1 增加 Team、TeamVersion、Worker Pool、Orchestration、Task、Activity、Event、Artifact 和 Delivery API，验证 OpenAPI schema、响应字段和错误类别与 delta spec 一致。
- [x] 5.2 保持单员工 Conversation、turn、Message、Run、Trace 和取消接口兼容，验证 TEAM 调用单员工 turn 返回 `CONVERSATION_MODE_MISMATCH`，SINGLE_AGENT 调用团队接口被拒绝。
- [x] 5.3 实现 `continue-as-team`、计划确认、任务分配/重试/取消、编排暂停/恢复/取消和配置修订冲突，验证幂等键、403、404、409 和跨租户隔离。
- [x] 5.4 实现成员活动和最近事件查询，验证返回 Task/Run/Trace/Engine/Worker Pool/sequence 信息，Viewer 响应隐藏可执行操作。
- [x] 5.5 接入审计事件和 Trace 关联字段，验证 Team 配置、计划、分配、认领、运行、Artifact、交付和权限拒绝均可按租户查询。
- [x] 5.6 编写 API 集成测试，验证正常路径、权限拒绝、模式不匹配、幂等冲突、超时、取消和跨租户资源不存在语义。

## 6. 产出物与交付

- [x] 6.1 实现按引擎收集 Runtime 文本、Workspace 文件、SQL、补丁和报告的 ArtifactCollector，验证 MediaAsset、ExecutionArtifact 来源、SHA-256 和租户保留策略一致。
- [x] 6.2 实现 Artifact 生命周期、扫描、预览、下载、组合打包和 Delivery Manifest，验证 SCANNING/FAILED/READY/DELIVERED 状态和 Artifact 来源可追溯。
- [x] 6.3 实现 ArtifactDelivery 幂等、部分失败交付和短期下载 URL，验证重复交付不生成重复记录、Viewer 不能发起交付、跨租户下载被拒绝。
- [x] 6.4 编写 Artifact、ObjectStore、MediaAsset GC 和 Delivery 集成测试，验证对象保护、失败重试、部分成功和撤销读取权限。

## 7. 前端工作台与原型实现

- [x] 7.1 在 `ConversationWorkbench`/页面增加“数字员工对话/团队协作”互斥模式切换，验证右上角只显示当前模式上下文：团队为当前团队，单员工为当前数字员工。
- [ ] 7.2 实现团队创建、草稿配置、成员 Deployment/EngineProvider/ModelRoute/能力选择、Router/Finalizer 角色、策略编辑和版本发布；验证只读身份隐藏写操作、发布后版本不可变且旧 Conversation 版本固定。
- [ ] 7.3 将团队任务队列、成员当前活动、任务详情弹框和最近事件弹框接入 API；验证状态、认领成员、阶段、Worker Pool、Attempt、Trace 和 sequence 均对应持久化记录。
- [x] 7.4 实现单员工运行配置、Session/Run 快照和“以团队协作继续”，验证新团队 Conversation 创建后原单员工 Conversation 不变。
- [ ] 7.5 将计划确认、暂停/恢复/取消、任务指派/重试/取消、Artifact 预览/下载/打包/交付接入服务端；验证操作权限、幂等、加载、空、错误、阻塞、部分失败和只读反馈。
- [ ] 7.6 按 `docs/原型/agent团队群聊.html` 逐项验收 React/CSS 字段、文案、颜色、圆角、间距、弹框、状态和响应式断点；完成条件为所有可见操作使用 API 状态且无未记录差异。
- [x] 7.7 维护独立 HTML 原型 `docs/原型/agent团队群聊.html`，验证 HTML parser、内嵌脚本 `node --check`、DOM 引用检查和 `git diff --check` 均通过。

## 8. 中文文档与验收

- [x] 8.1 更新 `docs/agent团队群聊设计.md`，验证架构图、流程图、数据模型、API、状态、权限、凭据、异常/重试/幂等、审计、迁移和原型映射与实现一致。
- [x] 8.2 更新 `docs/conversations-and-channels.md`、运行引擎和产出物相关中文说明，验证单员工/团队兼容和外部渠道未接入边界明确。
- [x] 8.3 执行后端单元、API、MySQL migration、Worker Pool、Artifact 和前端组件测试，记录通过/未验证项和命令输出。
- [x] 8.4 执行 OpenSpec 严格校验、原型 HTML/脚本/DOM 检查、`git diff --check`、后端全量测试、MySQL 集成、前端全量测试和生产构建；将最终结果与未验证边界写入中文验证记录。

## 9. 端到端可用性补齐

- [x] 9.1 MySQL 团队/编排/Artifact/Worker 心跳仓储端到端验收：服务重启后查询、租户隔离、事务回滚和 V011–V014 中文 schema 注释。
- [x] 9.2 实际 UI 创建团队、配置不同员工引擎并发布；再创建绑定该版本的团队对话，刷新后仍显示同一版本和成员。
- [ ] 9.3 从团队对话提交需求，由固定 Router Session 的 QUEUED Run 交给受信 Run Worker 执行并验证计划，再将固定成员 Session 的 Task Run 排队给 Worker；刷新后任务、Attempt、事件与消息可恢复，API 不调用持有 SecretRef 的 Runtime。
- [x] 9.4 实际 UI 验收团队统计、成员活动、查看任务/最近事件弹框、权限和加载/空/错误/成功状态均与 API 返回一致。
- [ ] 9.5 实际 UI 验收 Artifact 预览、授权下载、组合打包、幂等交付，以及服务重启后的产出物读取。
- [ ] 9.6 按 `docs/原型/agent团队群聊.html` 完成字段、按钮、弹框、布局、文案和响应式验收；同步中文文档并运行规定的 HTML/脚本/测试/构建/差异检查命令。
- [ ] 9.7 验证不同 EngineProvider 的 TeamMember 可固定不同已注册 Provider，并由 `AGENT_PLATFORM_WORKER_ENGINE_NAMES` 过滤的 Worker Pool 各自领取正确 Session Run；未注册或无心跳的引擎显示不可用，不静默切换。

## 归档条件

- 仅在 2.4、7.2、7.3、7.5、7.6、8.4 和 9.1–9.7 均有可复核验证结果后，才可将对应任务标记完成并归档本 change。
- 归档前确认设计、API/状态、验证记录和独立 HTML 原型路径一致；未验证的生产身份、真实 Runtime 与跨机 Worker 边界必须保留在文档中。
