# Proposal

## Why

当前平台已经支持单个数字员工 Conversation、固定 Session/Run、回答策略和工具审批，但缺少一个可审计的团队协作入口。用户无法在一个工作台中清楚区分单员工对话和团队协作，也无法看到成员认领了哪些任务、正在使用哪个引擎执行、产出了哪些文件以及最终如何交付。

本期需要在保留现有单员工路径的基础上，引入智能编排型 Agent 团队：由 Router Agent 拆分任务，Scheduler 按依赖和能力并行调度，成员使用各自固定的 EngineProvider 和独立 Worker Pool 执行，平台统一追踪任务状态和产出物交付。

## What Changes

- 增加统一“执行工作台”，在“数字员工对话”和“团队协作”之间切换。
- 固定 `ConversationMode`：`SINGLE_AGENT` 继续使用单 Session/单 Run；`TEAM` 使用 TeamVersion、多个 AgentBinding 和 OrchestrationRun。
- 增加 Team、TeamVersion、TeamMember 和 ConversationTeamBinding，支持新建团队草稿、配置成员、指定 Router Agent/汇总 Agent、发布不可变版本。
- 增加基于 DAG 的任务拆分、依赖、分配、认领、执行、阻塞、完成、失败、取消、重试和重新规划。
- 为 Codex CLI、DeepSeek Harness 等 EngineProvider 建立独立 Worker Pool 和按引擎队列；Worker 只领取对应 `engine_name` 的 Run。
- 支持团队成员使用不同 EngineProvider、ExecutionProfile 和 ModelRoute；任务按能力要求匹配成员，禁止隐式切换引擎。
- 增加标准化 TaskOutput、ExecutionArtifact、ArtifactDelivery 和 Delivery Manifest，支持文本、结构化结果、文件、预览、下载、打包、部分失败交付和后续外部渠道投递。
- 在工作台展示当前团队/当前数字员工、运行配置、成员当前活动、任务详情弹框、最近事件弹框、Worker Pool 状态和产出物交付状态。
- 增加从单员工 Conversation“以团队协作继续”的入口，创建新的 TEAM Conversation；原 Conversation、Session、Message 和 Run 保持不变。
- 增加团队配置、编排、任务、活动、产出物和交付 API，以及相应 MySQL forward migration、租户索引和中文 COMMENT。
- 保持现有单员工 ConversationTurnService、RuntimeAdapter、RunSnapshot、Trace、Usage 和权限边界兼容。

## Scope and Non-goals

### 本期范围

- Web 工作台中的单员工/团队模式统一入口。
- Router Agent 计划提案、Hybrid 确认和 DAG 并行调度。
- 独立 Engine Worker Pool、能力校验、任务租约和子 Run 关联。
- 团队成员、团队配置、成员活动、任务详情和最近事件原型对应的可观察行为。
- 产出物索引、对象存储复用、交付清单和 Web 交付确认。
- 加载、空、错误、只读、阻塞、失败、取消、部分成功和交付失败状态。

### 非目标

- 不在本期接入新的外部 IM 渠道。
- 不允许运行中的 Session 切换 EngineProvider。
- 不共享不同引擎的原生 Session、Thread、隐藏上下文或凭据。
- 不允许 Agent 直接修改任务状态、团队配置或产出物索引。
- 不自动把引擎故障静默切换到 Fake 或其他引擎。
- 不展示原始思考链、隐藏 Prompt、SecretRef 明文和完整敏感工具响应。

## Capabilities

### New Capabilities

- `agent-team-orchestration`: 团队版本、成员能力、Router Agent 计划、任务 DAG、独立 Worker Pool 调度、任务状态和成员活动。

### Modified Capabilities

- `conversations-and-channels`: 增加 `SINGLE_AGENT`/`TEAM` ConversationMode、团队绑定、单员工与团队工作台兼容、继续协作入口和团队消息/任务路由语义。
- `execution-engines`: 增加按 EngineProvider 隔离的 Worker Pool、引擎队列、任务级能力匹配和子 Run 的引擎快照约束。
- `media-and-artifacts`: 增加 Runtime 产出物索引、Task/Run 来源、ArtifactDelivery、交付清单、部分失败交付和产出物预览/下载行为。
- `platform-api`: 增加 Team、Orchestration、Task、Activity、Artifact、Delivery 和 continue-as-team API，并保持既有单员工端点兼容。

## Impact

- 后端领域模型、应用服务、端口、控制面和 Runtime Worker 调度。
- `ConversationService`、`ConversationTurnService`、`ControlPlaneService`、`EngineProviderRegistry` 和 `RuntimeAdapter` 集成边界。
- Codex CLI、DeepSeek Harness、Fake Runtime 的 Worker Pool 装配和队列领取。
- MySQL schema、租户复合索引、任务事件、Attempt、Artifact 和 Delivery 持久化。
- ObjectStore、MediaAsset 和对象 GC 的产出物引用保护。
- FastAPI 路由、请求/响应模型、幂等、权限和审计接口。
- 前端执行工作台、团队配置弹框、任务与产出物面板、成员活动弹框和单员工/团队模式切换。
- 中文项目文档、OpenSpec 规格、迁移验证和独立 HTML 原型：`docs/原型/agent团队群聊.html`。

## Acceptance Goals

- 用户可以在同一执行工作台切换数字员工对话和团队协作，且右上角只显示当前模式对应的上下文：单员工显示当前数字员工和运行配置，团队显示当前团队和团队配置。
- 单员工 Conversation 继续创建单个固定 Session/Run；团队 Conversation 能由 Router Agent 生成可确认的任务 DAG，并并行创建多个成员子 Run。
- 团队成员可以分别使用 Codex CLI、DeepSeek Harness 等 EngineProvider；每个 Worker Pool 只领取自己的 Run，引擎不可用时展示明确阻塞原因。
- 工作台能看到成员当前认领的任务、阶段、Worker Pool、运行时长、任务详情和最近事件。
- 用户可以查看任务状态、重试、取消、重新分配并看到父级编排聚合状态。
- 成员产生的文本、JSON、SQL、CSV、图片、补丁和报告可以通过 Artifact 面板预览、下载、加入交付包并在部分失败时交付已完成内容。
- 当前 HTML 原型中的团队配置、新建团队、运行配置、任务详情、最近事件、产出物和交付交互成为实现验收基线。

## Phased Delivery

1. **基础与兼容**：ConversationMode、TeamVersion、成员绑定、单员工/团队工作台入口、独立 Worker Pool 和任务状态基础。
2. **智能编排**：Router Agent 计划、Hybrid 确认、DAG 并行调度、能力匹配、任务重试/取消/重新分配和成员活动。
3. **产出物交付**：Artifact Collector、ExecutionArtifact、Delivery Manifest、预览/下载/打包、部分失败交付和原型完整状态验收。
4. **后续扩展**：外部渠道附件、运行中动态重规划、Worker Pool 自动恢复和人工任务协作。
