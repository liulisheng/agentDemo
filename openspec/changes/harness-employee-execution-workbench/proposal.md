# Proposal

## Why

当前管理端能配置数字员工，但 MySQL 模式仍固定使用 Fake Runtime，真实 Harness、Worker 和 StaffDeck 中的 DolphinScheduler MCP 尚未接成可执行闭环。用户无法在员工工作台提交任务、查看会话历史、审阅具体写操作并安全地让员工开始工作。

本 Change 建立首个受控执行纵切：海豚调度数仓开发员工查询项目/数据源/工作流，生成 ODS 到 DWD 工作流预览，并在用户单独确认后创建为 OFFLINE。

## What Changes

- 将 Run API 入队与 Runtime 执行解耦，由持久 Worker 按 MySQL lease/fencing 执行，并把平台固定的员工、Skill、Tool、Model 和 MCP Profile 版本传给 Harness。
- 为 Worker 管理本机 Stdio MCP 子进程；通过版本化 MCP Server Profile、工具目录摘要、JSON Schema 校验和员工/Skill/租户 allowlist 控制工具访问。
- 增加 Conversation 游标列表及提交新 turn 的 API，使对话列表、消息、Run 状态、Trace 和审批预览能在员工工作台恢复和展示。
- 增加一次性写操作审批记录与状态。模型只能使用只读/预览工具；获批 Worker 根据批准时绑定的工具版本、预览摘要和参数对象执行一次具体写操作。
- 增加 Run 的 AWAITING_APPROVAL 状态、审批动作 claim/lease/fencing、Trace 和审计记录；无法确认远端写结果时进入 UNKNOWN，不自动重试。
- 首个写场景只创建 OFFLINE 工作流。发布、启动和调度分别要求新的预览和用户确认。
- 用前向 MySQL migration 新增 MCP Profile/审批 schema，并满足中文表字段 COMMENT 和 information_schema 验证约定。
- 不改造为公开 MCP HTTP 服务；知识库保持待定；现有同步 Run API 保持兼容，由新增的 Conversation turn 路径提供异步工作台体验。

## Capabilities

### New Capabilities

无。本 Change 扩展现有引擎、工具、对话、持久化、API 和可观测性能力，不引入近似重复的新能力域。

### Modified Capabilities

- execution-engines：Worker 异步执行 Harness Run，并传递固定员工上下文、Skill 文件和有效工具定义；Runtime 状态和能力缺失显式处理。
- tool-connectors：版本化 Stdio MCP Profile、远端工具目录核验、风险分级以及预览绑定的一次性审批动作。
- conversations-and-channels：工作台对话列表、员工绑定过滤、异步 turn 与审批状态恢复。
- control-plane-persistence：Run AWAITING_APPROVAL 状态、审批动作租约/fencing、幂等和 UNKNOWN 写结果语义。
- platform-api：新增 Conversation 列表、异步 turn、审批查询/确认/拒绝 API。
- execution-observability：将 MCP 调用、审批与批准后的写动作关联到同一 Run Trace 和受限审计记录。

## Impact

- 后端：ControlPlaneService、RuntimeAdapter/Harness EngineRequest、ConversationService、MySQL execution/conversation repositories，以及新的 Run Worker 和 Stdio MCP 管理模块。
- 数据库：在 docs/migrations/ 新增向前 migration；扩展 tool_versions 与 runs 状态约束；新增租户范围 MCP Profile 和 Tool Approval 表。消息正文、prompt、工具参数和凭据不写入普通数据库字段。
- API：新增 Conversation 列表、Conversation turn、Approval 查询/确认/拒绝端点；保持现有 API 路径兼容。
- 前端：员工工作台增加对话列表、执行状态、工具事件、审批预览和 OFFLINE 创建结果。
- 外部依赖：DeepSeek Harness SDK 与 StaffDeck DolphinScheduler MCP Stdio server。已导出脱敏 command/args/cwd、工具 Schema、风险和 preview/write 配对；运行时秘密仅以 SecretRef 提供。
- 首阶段不启用知识库，也不允许模型直接调用发布、运行或调度等写工具。
