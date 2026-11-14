# Proposal

## Why

当前项目已经具备 DeepSeek Harness、工具插件、会话持久化和沙箱执行基础，但还缺少面向公司业务线的 Agent 平台控制面。数字员工、技能、工具、可替换执行引擎、模型路由、上下文记忆、群聊入口和全链路用量审计需要统一的领域边界，才能支持后续 API、前端、IM 和多业务线部署。

本变更先建立平台后端的文档契约和模块化代码边界。用户中心先使用 Fake 实现，模型连接先支持简单配置；后续可替换为 Dadi User Center、new-api 或 Dadi Model Gateway，而不改变平台领域模型。

## What Changes

- 新增独立 `backend/` 平台控制面，保留 `harness-engine/` 为独立 SDK 和执行引擎层。
- 增加数字员工、技能、工具连接器、发布版本和部署管理能力。
- 明确 Skill 的类型、输入输出契约、工具约束和执行流程，并定义从数字员工配置到一次 Run 的 Agent 组装方式。
- 增加平台、租户、用户级 `ExecutionProfile` 绑定，支持选择 DeepSeek Harness、Codex、Generic CLI 等执行引擎。
- 增加模型路由抽象，先支持直接配置，预留 new-api 和 Dadi Model Gateway 适配器；平台不维护供应商兼容性矩阵。
- 增加 Conversation、Message、群聊路由和 IM Channel Adapter 边界。
- 增加上下文组装、上下文预算、会话摘要和分层记忆管理。
- 增加代码工作区、日志查询和运维 Agent 的执行环境及沙箱边界。
- 将平台定位为企业级 Agent Control Plane：治理身份、业务上下文、策略、发布、工具、渠道、审计和用量，不重复实现供应商 Runtime 的 Agent Loop、上下文压缩、私有 Session 或内部沙箱。
- 增加从 Session 创建到 Run、Context、Memory、Model、Tool、Approval、Channel 的 Trace 链路。
- 增加模型 Token、费用、工具调用和原始入参出参的分层存储方案。
- 增加 Fake Identity Provider，使用与 Dadi 对齐的 platform、tenant、user 和授权语义。
- 形成 HTTP API、设计文档、流程描述和后续实现任务；本变更不直接实现生产级 Dadi、IM、模型网关或沙箱集成。
- 将 DeepSeek Harness、Codex、Hermes 和 Generic CLI 作为可替换 Runtime，通过 opaque session、workspace、memory 引用和能力声明接入。
- 增加基于本地运行行为整理出的独立页面信息架构、数字员工能力绑定、CompositionSnapshot、Session/Turn/Trace 和 API 流程参考；这些参考不包含 StaffDeck 源代码或资源依赖。
- 增加中文设计文档和图示约束，要求代码架构、模块边界、数据模型、状态机、运行流程、API、部署和故障恢复都有对应文档落地。

## Capabilities

### New Capabilities

- `platform-api`: 面向管理端和未来前端的模块化 HTTP 控制面。
- `digital-employees`: 数字员工定义、版本、发布和部署。
- `skills`: 可复用技能、技能版本和技能执行配置。
- `tool-connectors`: API、MCP、A2A 工具目录、权限和调用边界。
- `execution-engines`: DeepSeek Harness、Codex、Generic CLI 和 Fake 引擎的统一生命周期协议。
- `model-routes`: 直接模型配置、模型路由和未来模型网关适配边界。
- `context-and-memory`: 上下文组装、预算、摘要和分层记忆。
- `conversations-and-channels`: Conversation、群聊触发规则、消息和 IM 渠道适配。
- `workspaces-and-sandboxes`: 源代码、日志、运维任务的工作区和隔离执行环境。
- `execution-observability`: Session、Run、Model、Tool、Memory 和 Channel 的 Trace、Span 与审计链路。
- `usage-accounting`: Token、模型费用、工具费用和按平台/租户/用户聚合的用量台账。
- `identity-context`: Fake 用户中心和后续 Dadi 身份适配的统一上下文与授权接口。
- `documentation-and-design`: 中文架构、流程、数据模型、接口、部署和代码维护文档。

### Modified Capabilities

无。当前 `openspec/specs/` 没有已存在的正式能力规格。

## Impact

- 新增 `backend/` Python 后端工程、HTTP API、应用服务、领域模型、端口和适配器。
- 扩展现有 `harness-engine` 的引擎注册和事件边界，但保持 SDK 与平台后端单向依赖。
- Runtime Adapter 只负责生命周期、能力、事件、Usage 和引用转换；平台不接管供应商私有上下文压缩、记忆格式或内部 Agent Loop。
- 新增 MySQL 控制面表或迁移设计，使用现有 platform/tenant 复合隔离方式。
- 新增加密对象存储引用、Trace/Usage 元数据和原始 payload 保留策略。
- 需要为 DeepSeek、Codex app-server、Codex exec、Generic CLI、Fake Identity 和模型路由增加适配器设计。
- 需要补充管理 API、运行时流程、上下文记忆、工作区、渠道、审计、用量和安全文档。
