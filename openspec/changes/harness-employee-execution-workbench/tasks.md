# Tasks

## 1. MCP Profile 与 Harness 契约准备

- [x] 1.1 从 StaffDeck 导出 DolphinScheduler MCP 的无密钥 command/args/cwd/env alias、工具名和 JSON Schema，并标注 READ/PREVIEW/WRITE、preview 对应项和幂等能力；验证清单不含任何凭据值并更新中文执行设计文档。
- [x] 1.2 在隔离环境验证 Harness SDK 可接收 system prompt、Skill 指令/文件、有效工具 Schema 并创建/恢复 Session；增加可重复的适配契约验证并把实际结果记录到中文 SDK 验证文档，未通过时禁止启用真实 MCP。

## 2. 持久化模型和租户仓储

- [x] 2.1 编写 docs/migrations/V006__mcp_profiles_and_approvals.sql，新增 MCP Profile 与 Tool Approval 表、扩展 ToolVersion MCP 映射、保存 Run 的 submitted_by_user_id 并允许 AWAITING_APPROVAL；每张表和每个字段添加中文 COMMENT，验证 MySQL migration runner 与 rollback 边界。
- [x] 2.2 增加 Profile/Approval 领域对象、仓储接口及 MySQL 实现，保存 Run 提交者、审批人、精确版本、digest、对象引用和 lease/fencing；以单元和 MySQL 集成测试验证租户隔离、唯一约束、并发领取及 information_schema 中文 COMMENT。
- [x] 2.3 新增 docs/migrations/V007__session_runtime_leases.sql，为同一 Session Home 增加平台/租户范围 ACTIVE/QUARANTINED 执行租约；运行 Run claim、heartbeat、终态释放与 UNKNOWN 隔离使用同一 fencing token；验证并发 claim、租户隔离、Session INTERRUPTED 和 down 边界及中文 COMMENT。

## 3. Harness、MCP Stdio 与 Worker

- [x] 3.1 实现 Worker 管理的 Stdio MCP host，使用参数数组、allowlisted cwd 和 SecretRef 启动进程，核对工具目录/schema digest 并在超时、取消、租约丢失时清理进程；增加进程生命周期、安全拒绝和目录漂移测试并更新执行设计。
- [x] 3.2 扩充 EngineRequest 与 RuntimeAdapter 的类型化员工上下文和工具定义；每 Run 新建 Harness 进程，使用 Session 稳定 Harness Home 和独立工具配置 overlay，把固定 RunSnapshot 的系统指令、Skill 文件及过滤后的 READ/PREVIEW Stdio MCP allowlist 传入 Harness；Run 结束关闭进程并清理 overlay；补充 DeepSeek/Fake/SDK 契约和跨 Run 隔离测试并验证不传递密钥。
- [x] 3.3 实现独立 Run Worker 的 MySQL claim、STARTING/RUNNING 迁移、heartbeat、fencing、取消和 UNKNOWN 回收，并在 control-plane Compose 中增加 Worker 服务；验证 API 入队后 Worker 可处理 Run，旧 token 不能提交，更新部署/运维说明。

## 4. Conversation API

- [x] 4.1 扩展 ConversationRepository/Service，按员工和租户稳定游标分页并聚合最后消息、Run 状态及审批数量；消息或 Run 状态变化时推进 Conversation 更新时间，添加仓储/API 多租户分页测试并更新中文接口文档。
- [x] 4.2 增加 Conversation turn API，在同一事务保存用户消息、RunSnapshot 和 QUEUED Run，使用请求摘要与幂等键去重并返回 202；验证重复请求、冲突请求和提交失败回滚，保持现有同步 Run API 兼容。

## 5. 审批和一次性离线创建

- [x] 5.1 实现审批预览读取、确认和拒绝服务/API；只允许同租户且提交该 Run 的用户确认，核对 expiry、preview_digest 和幂等键；验证非提交者、过期、摘要不匹配、拒绝和重复确认均不触发 MCP 写调用。
- [x] 5.2 Worker 领取 APPROVED ToolApproval 时原子消费授权并执行绑定的一个 WRITE ToolVersion，成功后更新 Run/Approval/Trace/审计；注入远端超时、Worker lease 丢失和重复领取验证 UNKNOWN 不自动重试，更新错误与对账说明。
- [x] 5.3 完成首个 DolphinScheduler 纵切：只读列项目/数据源/工作流，生成 2 节点 ODS→DWD 预览，批准后创建 OFFLINE；在隔离项目验证未审批时写调用数为 0、创建结果可回查并记录中文运行证据。

## 6. 员工运行工作台

- [x] 6.1 实现三栏员工工作台：左侧员工过滤的对话列表，中间消息/工具/Run 时间线，右侧快照和审批预览；增加列表选择、分页、空/错误/窄屏和 AWAITING_APPROVAL/UNKNOWN 状态组件测试并更新中文前端交互设计。
- [x] 6.2 接入 Conversation/Run/Trace/Approval API，支持新对话、提交任务、轮询状态及由 Run 提交者确认/拒绝预览；验证重复提交禁用、预览摘要一致、跨租户无数据泄露，并运行前端测试与构建。
- [x] 6.3 主时间线优先显示用户消息、工具调用/结果、审批和最终答复；将 Session/Run/Skill/Context/Runtime/Model/Result 内部 Span 默认收起并提供中文说明；增加默认收起、展开和用户可见事件持续可见的前端测试。

## 7. 跨组件验收

- [x] 7.1 运行 OpenSpec strict validation、后端全量测试、MySQL V006/V007 migration/执行/Conversation/Session lease 集成测试、DeepSeek SDK + Stdio proxy 与 RuntimeAdapter 测试、前端测试/构建；记录验证命令、环境和未验证项，确认变更设计与代码状态一致。
- [x] 7.2 在非生产环境完成端到端验证：Worker 重启、租约过期、Stdio 子进程清理、Run submitter 审批、离线创建和 UNKNOWN 对账；确认旧同步 API 兼容、知识库未接入，并更新中文验收记录。
