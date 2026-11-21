# Design

## Context

See proposal.md for motivation and capability scope.

当前控制面已有 Conversation、AgentBinding、Session、RunSnapshot、MySQL Run 租约/fencing、RuntimeAdapter 端口和 HarnessRuntimeAdapter 原型。MySQL 模式的 app.py 仍固定使用 FakeRuntimeAdapter；ControlPlaneService 在请求调用链同步运行；Worker 包没有领取循环。Harness EngineRequest 不包含员工上下文，DeepSeek 适配器当前只将 prompt 和 Session ID 传给底层 Harness。

Conversation 与 Session 已分开建模；消息正文通过对象引用持久化；runs 表已有 QUEUED 状态、幂等键、worker_id、lease_expires_at、fencing_token 和 snapshot_json。StaffDeck DolphinScheduler MCP 的脱敏 command/args、working directory、环境变量别名、工具 Schema 和风险映射已导出到项目文档。

## Goals / Non-Goals

**Goals:**

- 复用 MySQL Run 表和现有 lease/fencing 机制，形成可恢复的异步 Worker 执行链路。
- 将精确员工/Skill/Tool/ExecutionProfile/MCP Profile 快照传给 Harness，并限制模型工具可见范围。
- 先完成 DolphinScheduler 只读查询和离线工作流预览，再通过一次性用户审批执行一次创建。
- 把对话列表、消息、Run、审批和 Trace 状态串成可恢复的工作台体验。
- 新数据具备租户隔离、显式 migration、中文 COMMENT 和 UNKNOWN 不重试边界。

**Non-Goals:**

- 本 Change 不实现知识库、SOP 执行或员工配置编辑器改版。
- 首个写纵切不发布、启动工作流或创建调度；这些操作需要后续独立确认。
- 不把 MCP 暴露为 API/HTTP 服务，不实现通用 MCP 代理。
- 不迁移 Runtime 私有历史，也不重写供应商 Agent Loop。
- 不改变现有同步 Run API；工作台通过新增的 Conversation turn API 使用异步执行。

## Decisions

### 1. API 入队，Worker 执行

API 在租户事务中保存用户消息、RunSnapshot、request digest 和 QUEUED Run，提交后返回 HTTP 202。Worker 通过 MySQL FOR UPDATE SKIP LOCKED 领取任务并维护 lease/fencing；首版不引入 Redis/Kafka，MySQL Run 是唯一队列来源。API 不在请求线程或生命周期钩子中执行 Harness/MCP。

Run 提交者通过 `POST /api/v1/runs/{id}/cancel` 请求取消。QUEUED Run 不经 Runtime 直接进入 CANCELLED；已 claim Run 进入 CANCELLING，由 Worker 调用 RuntimeAdapter.cancel。Runtime 不支持取消、取消失败或结果不确定时收敛到 UNKNOWN，不自动重试。

~~~mermaid
flowchart LR
    UI[员工工作台] --> API[FastAPI]
    API --> APP[Conversation / ControlPlane]
    APP --> DB[(MySQL)]
    DB --> W[Run Worker]
    W --> RA[RuntimeAdapter]
    RA --> HE[harness-engine]
    HE --> DH[DeepSeek Harness]
    HE --> MC[MCP Stdio Client]
    MC -->|stdin / stdout| MCP[DolphinScheduler MCP 子进程]
    MCP --> DS[DolphinScheduler]
    W --> DB
    UI -->|Run / Trace / Conversation 查询| API
~~~

Run 主状态为 QUEUED → STARTING → RUNNING。生成写操作预览后转 AWAITING_APPROVAL。Run 提交者可请求取消：尚未 claim 的 QUEUED Run 直接进入 CANCELLED；STARTING/RUNNING 写入 CANCELLING，由当前 Worker 调用 Runtime.cancel；确认停止后进入 CANCELLED，不能确认停止则进入 UNKNOWN 且不重试。拒绝/过期审批时 Run 进入 CANCELLED；批准动作成功、确定失败、结果不确定分别进入 SUCCEEDED、FAILED、UNKNOWN。旧 fencing token 不能提交状态。

### 2. Worker 管理本机 Stdio 子进程

租户范围、不可变版本的 MCPServerProfile 保存 transport=stdio、command、args_json、working_directory_ref、环境 SecretRef、启用工具目录、schema/catalog 摘要和 timeout。实际 command/args/cwd/env alias 在阶段 0 从 StaffDeck 配置导入；数据库不存密钥值。

Worker 通过参数数组和受控工作目录启动 MCP server，使用官方 MCP SDK Stdio client，不自行实现协议帧。MCP server 是 Worker 管理的本机子进程，不创建 HTTP listener；stdout 仅承载协议，日志写 stderr。Run 结束、取消、租约丢失或超时后关闭会话并清理进程组。

Worker 启动后调用 tools/list，核对远端工具名和 input schema 摘要。目录漂移、未知 executable、越界 cwd 或 SecretRef 缺失时 fail closed，不回退 Fake。

### 3. 每个 Run 独立 Harness，EngineRequest 传业务快照

DeepSeek SDK 把 system prompt、profile 和 MCP server 固定在 Harness 初始化配置中，不提供 request-level ToolExecutor。Worker 因而为每个 Run 新建 Harness 进程和唯一配置 overlay；overlay 只包含该 RunSnapshot 的员工指令、Skill 文件和 MCP allowlist，Run 结束后关闭 Harness 并清理 overlay。不能复用一个初始化级全局工具目录来处理不同员工的 Run。

RuntimeAdapter 将平台 RuntimeRequest 转为类型化 EngineRequest，表达 run_id、opaque SessionRef、prompt、system_prompt、顺序固定的 Skill instructions、Skill 文件 manifest/ref、model route、metadata、有效工具定义和受控 MCP Server 配置。每个 Run 的 Stdio proxy 只列出该 Run 的 READ/PREVIEW 工具，并在 `tools/call` 时再次执行 allowlist 校验；WRITE ToolVersion 只保存在快照供审批 Worker 验证，不传入模型工具表。EngineRequest 不含环境变量明文、令牌或 SecretRef 映射。

各 Run 使用该 Session 的稳定 Harness Home 以保留对话历史。控制面生成 `harness-session:<SHA-256(platform_id,tenant_id,session_id)>` opaque ref，Worker 只能将它映射到共享对象卷下 `harness-sessions/<digest>`，目录权限为 0700；不能接受客户端路径。Run 专属 MCP/员工配置只通过不共享的临时 patch 注入。MySQL 对同一平台、租户和 Session 只允许一个 Harness Run 持有执行租约；这样不同 Worker 不能并发修改相同 Session 文件。租约未知时将 Run 收敛到 UNKNOWN、Session 标记 INTERRUPTED 并隔离该 Home；不允许新 Run 自动接管可能仍被旧进程写入的 Session。每次获知 SDK close 成功或确定性失败后才释放或隔离租约。

不复制 Agent Loop。SDK 的官方 `DeepSeekHarness`/`Session.run()` 接口只接受启动配置、文本或内容块及 Session ID；它没有请求级 ToolExecutor。因此采用独立 Harness 进程和 Profile patch，而不是伪造运行时请求级能力。SDK 配置、系统提示和 MCP tools/list/工具调用由每 Run Engine 生成并通过固定 SDK 契约测试。

### 4. 审批行自身作为一次性写动作队列

Agent 只能调用只读和 preview 工具。Worker 把精确参数和预览保存为租户隔离的加密对象，计算 canonical digest，创建 PENDING ToolApprovalRequest 并将 Run 转为 AWAITING_APPROVAL。

只有提交该 Run 的同租户用户可以确认预览，不允许管理员代批。API 校验 IdentityContext.user_id 与审批行 requested_by 相同，并校验 expires_at、preview_digest 和幂等键；同一事务保存 approved_by/approved_at 并置状态 APPROVED。APPROVED 行本身就是动作队列，不再建重复 ToolAction 表。

Worker 使用 FOR UPDATE SKIP LOCKED 领取该行，原子设置 RUNNING、worker_id、lease/fencing 并消费授权，再以固定 Tool/Profile 版本和 arguments_ref 调用一次 MCP 写工具。可信成功、可证明的无副作用错误、结果不确定分别写 SUCCEEDED、FAILED、UNKNOWN。UNKNOWN 不自动重试。模型不直接执行写工具，因此审批不依赖 SDK 暂停并恢复内部 tool callback。

首阶段只开放项目/数据源/工作流只读查询、workflow preview 和 create workflow OFFLINE。发布、启动、调度需要新的 ToolVersion、预览和审批。

### 5. 持久化使用 V006/V007 前向迁移

RunSnapshot 继续存于 runs.snapshot_json，不增加快照表。ToolInvocationRecord 继续存为 platform_trace_spans 的 tool Span；approval_ref 关联审批记录。prompt、消息正文、完整参数/预览和密钥不写入普通 MySQL 字段，正文只保留加密对象引用、哈希和大小。

基于当前 V005，新增 docs/migrations/V006__mcp_profiles_and_approvals.sql：

- 新建 mcp_server_profiles，复合租户主键加 profile/version；外键、目录索引和状态 CHECK。
- 扩展 tool_versions，记录 MCP Profile 精确版本、remote_tool_name、schema digest、preview tool、MCP 风险等级和 idempotency 支持；不复用现有 risk_policy 的 low 等级字段。
- 新建 tool_approval_requests，固定 Run/Tool/Profile/ToolInvocation、requested_by/approved_by、参数/预览引用和摘要、审批状态、worker lease/fencing、过期与错误字段。
- 为新 Run 保存认证后的 submitted_by_user_id；扩展 runs.state CHECK 支持 AWAITING_APPROVAL。历史 Run 的提交者不可可靠推断，列保持 nullable，历史 Run 不允许创建写审批。
- 新表和变更列均写中文 COMMENT；MySQL 集成测试检查 information_schema.TABLES/COLUMNS。

Profile 和 Approval 的所有查询均带 platform_id + tenant_id。ToolInvocation ID 关联 trace span_id；审批行限制每个 Run 同时最多一条 PENDING 请求。

在 V006 后新增 docs/migrations/V007__session_runtime_leases.sql，创建按 `(platform_id, tenant_id, session_id)` 唯一的 `session_runtime_leases` 表，记录运行中的 `run_id`、`worker_id`、与 Run fencing token 对应的单调 token、lease 到期时间和 ACTIVE/QUARANTINED 状态。所有字段和表使用中文 COMMENT，外键限定同一平台/租户。V007 在登记 schema version 前把已有 STARTING/RUNNING/CANCELLING Run 的 Worker/token/expiry 迁入 lease；若同一 Session 有多个活动 Run，唯一键冲突使迁移停止，不自动选择其一。升级前先停止领取并排空 Worker。claim 在一个事务内领取 Run 并创建 Session lease；heartbeat 原子续租两者；确定成功/失败/确认取消时释放 Session lease；运行结果未知或租约过期时将 lease 隔离、Session 标记 INTERRUPTED，必须创建新 Session 才可继续。V007 down 只在 disposable database 验证。

### 6. Conversation 列表是授权聚合查询

Conversation 继续独立于 Session，一个 Conversation 可绑定多个 Agent；工作台按 employee_id 筛选。新增 GET /api/v1/conversations 按 updated_at DESC、conversation_id DESC 稳定游标分页，返回标题、最后消息摘要、员工绑定、Session、最新 Run 状态和待审批数。消息或 Run 状态变化时在同一事务推进 updated_at；摘要只返回调用者有权读取的正文。

新增 POST /api/v1/conversations/{id}/turns，原子写用户 Message、RunSnapshot 和 QUEUED Run，按租户、幂等键、请求摘要去重。请求可提供 employee_id；省略时仅在 Conversation 恰有一个 ACTIVE AgentBinding 时自动选择，多绑定场景返回明确歧义错误。对象正文先写入租户对象存储，MySQL 在同一事务提交 USER Message 引用、RunSnapshot、QUEUED Run、Session 消息序号和 Conversation 更新时间。相同幂等键/摘要返回原 Message/Run ID，不同摘要冲突；现有 POST /sessions/{id}/runs 保持兼容。首版轮询 Conversation/Run/Trace，持久事件 cursor 和 SSE/long-poll 后续另行设计。

### 7. 工作台三栏展示执行与审批

左栏为员工过滤后的对话列表；中栏主时间线显示用户消息、实际工具调用/结果、审批和员工答复，Session/Skill/Context/Runtime/Model/Result 等内部 Trace 默认收起，展开后提供中文说明及原始事件类型；右栏显示 RunSnapshot、工具权限和审批预览。审批卡展示目标、项目/数据源、DAG、节点参数、OFFLINE 状态、摘要和有效期。

Run 状态用文本和颜色共同表达；UNKNOWN 展示为“结果待对账”，不提供重试写操作。窄屏按列表、对话、详情堆叠。员工目录和编辑页不属于本 Change。

## Risks / Trade-offs

- [多个 Worker 争用同一 SDK Session Home] → V007 Session runtime lease 按平台/租户/Session 串行执行；过期时隔离 Home，不自动接管。
- [每 Run 创建 Harness 增加启动开销] → 保留同一 Session Home 和模型会话历史；只重建 Harness 进程及临时 MCP 配置 overlay。
- [Worker 与 StaffDeck 分属独立 Docker 卷] → Worker 只读挂载 MCP 子目录，环境别名以 SecretRef 映射；不挂载 StaffDeck 完整数据卷。
- [非幂等写请求超时造成本地/远端状态分歧] → 预览摘要绑定一次批准；写前消费授权；超时进入 UNKNOWN 并只读对账。
- [状态 CHECK 不接受 AWAITING_APPROVAL] → 先显式运行 V006，再部署允许新状态的 API/Worker；应用启动只读校验 schema。
- [租约失效但 MCP 子进程仍运行] → heartbeat 失败后终止本地进程组；旧 token 不可提交，远端结果不确定则标 UNKNOWN。
- [环境变量/工具参数泄露] → SQL/Trace/log 只存 SecretRef、摘要和对象引用；不记录 env 明文。
- [列表摘要暴露消息] → 所有分页、预览和关联读取使用认证用户的租户/Conversation 授权。

## Migration Plan

1. 导出并核对 MCP 命令、args、工作目录、环境变量别名、工具目录/schema、preview/write 对应关系和幂等能力。
2. 在隔离环境验证 Harness 类型化上下文、MCP Stdio 工具表及 Session 生命周期；先通过 Fake 和只读工具。
3. 显式执行 V006/V007，验证 schema version、中文 COMMENT、外键、索引、Session lease 并发约束和状态迁移。
4. 部署兼容 AWAITING_APPROVAL 的 API；写工具默认关闭；部署独立 Worker。
5. 导入 Profile，只启用 READ 工具，验证 Conversation 列表、项目/数据源/工作流查询、Trace 和租约回收。
6. 开启 PREVIEW，验证用户看到的预览与 preview_digest 一致，未确认时远端写调用数为 0。
7. 对隔离项目启用唯一 create workflow OFFLINE；验证重复确认、远端超时和 lease 丢失均不重放。
8. 发布/启动/调度另行立项，不能借用本 Change 的创建审批。

回滚先关闭 Profile 和 Worker 领取，再部署兼容代码。V006/V007 已有数据后保留 schema、审批记录、Session lease/quarantine 和快照，不删除被引用的表/列。未完成审批安全过期或关闭；旧版本不得把 AWAITING_APPROVAL 当作可执行状态。

## Open Questions

- StaffDeck 的 command/args、cwd、环境别名及工具 Schema 已脱敏导出；Worker 容器只读挂载和 SecretRef 注入仍由本机验收配置完成，不需要把密钥值写入 Profile 或文档。
- DeepSeek SDK 的系统指令、Skill 内容和 MCP 工具目录是 Engine 初始化级配置；已选择每 Run 独立 Harness + 过滤后的 Stdio proxy。Session Home 通过 V007 lease 串行保护，未知 Run 将 Session 隔离为 INTERRUPTED。
