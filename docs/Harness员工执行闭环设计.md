# Harness 数字员工执行闭环设计

状态：方案基线，尚未接通生产 Worker 与 DolphinScheduler MCP 写操作<br>
更新时间：2026-11-19<br>
适用场景：海豚调度数仓开发员工完成数仓工作流设计与受控创建<br>
原型：[海豚调度员工工作台](原型/海豚调度员工工作台.html)

本文统一描述 Harness 员工工作台、Worker 执行链路、MCP Stdio 接入、数据契约、审批和页面交互。文中分别标记当前实现与目标设计，避免把设计状态误认为已上线。

## 1. 目标、范围和决策

### 目标

用户在管理页面选择员工并提交任务后，员工能够使用固定版本的员工指令、Skill、Tool 和模型配置理解任务；由 Worker 调用 DeepSeek Harness；通过 Stdio 使用 DolphinScheduler MCP 查询和操作；把每次执行的结果、失败或未知状态记录到对话、Run、Trace 和审计。

### 首批范围

- 首个场景：生成 2 节点 ODS 到 DWD 工作流草案。
- 首批只读能力：列出项目、数据源、现有工作流。
- 首个写操作：创建工作流并保持 OFFLINE。
- 发布、启动、调度各自需要独立预览和独立确认。
- 工作台包括按员工过滤的对话列表、消息、运行事件、审批预览和结果。
- Worker 使用本机 Stdio 启动 MCP Server；不要求 API 暴露 MCP HTTP 服务。
- 知识库保持待定，不作为本轮阻塞项。

### 已确认决策

| 决策 | 设计约束 |
|---|---|
| 执行入口 | UI 调用 backend API；API 固化租户与版本快照并入队；独立 Worker 执行 |
| MCP 传输 | Worker 侧使用 Stdio 子进程和标准输入/输出管道，不经 HTTP 代理 |
| 工具策略 | 员工绑定、Skill 精确版本声明、租户策略、MCP Profile 工具目录取交集 |
| 写操作审批 | 每次创建、发布、运行或调度都对精确参数预览单独确认 |
| 自动重试 | 非幂等 MCP 写操作超时或 Worker 丢失后标记 UNKNOWN，不自动重发 |
| 知识库 | 暂不设计入第一阶段 |
| 原型 | 两种页面布局都包含对话列表；按钮仅模拟本地状态，不调用真实 MCP |

### 待补事实

StaffDeck 中目标员工“小刘”绑定已发布 Skill `dolphinscheduler-dw-skill-2`，使用 Stdio Server `dolphin_ds`。服务目录发现 25 个工具，员工绑定 20 个；完整 command/args/cwd/env alias、Schema、风险注解、预览配对和幂等提示已导出至[配置清单](StaffDeck海豚调度MCP配置清单.md)及 JSON 清单。凭据值未导出；Worker 接入必须将环境变量映射为 SecretRef。

## 2. 当前代码状态与差距

### 已有基础

- 控制面已有数字员工、版本化 Skill/Tool、Deployment、Conversation、AgentBinding、Session、Run、Message、Trace、Usage 和 MySQL 仓储。
- application/resolvers.py 能将精确员工版本、Skill 文件包、Tool 引用、执行配置和模型路由解析为 Run 运行输入。
- domain/observability.py 已有 RunSnapshot、SessionRecord、RunRecord。
- infrastructure/mysql_execution.py 已有幂等提交、FOR UPDATE SKIP LOCKED 租约、心跳、fencing token 和终态持久化。
- ports/runtime.py 已定义 RuntimeRequest、RuntimeEvent、RuntimeResult 和 RuntimeAdapter。
- harness-engine 已定义 EngineRequest、EngineResult、SessionRef、HarnessEngine 生命周期和 DeepSeek/Fake 引擎。
- harness-engine/src/harness_engine/mcp_server.py 已有官方 MCP SDK 的 Stdio Server 示例及 shell-free 命令构造函数。
- MySQL V001 至 V007 migration 位于 docs/migrations/；消息正文通过不透明对象引用保存；V006/V007 已在 MySQL 8.4 disposable database 验证 apply/down、中文 COMMENT、Worker claim 索引和 Session lease。

### 尚未接通的部分

| 位置 | 当前状态 | 接入时要解决的问题 |
|---|---|---|
| backend/src/agent_platform/api/app.py、workers/run_worker.py | Conversation turn 经 MySQL 入队；本机 Compose backend/Worker 已应用 V007；默认 Fake，acceptance overlay 显式配置 DeepSeek Harness 与本地兼容 mock | Fake Worker 重启、新 Session/旧同步 API、过期 lease 隔离均已验证；本机 Harness Run 已通过 StaffDeck Stdio 完成预览审批；真实 DeepSeek provider 和跨 Worker 长会话恢复仍未验证 |
| backend/src/agent_platform/adapters/runtime/harness.py | 每个 Run 用自己的 EngineRequest 创建/关闭 Engine；系统提示、Skill 文件、Schema 和 opaque Session Home 从固定 Run 输入传递；SDK 截短工具名按 Run Schema 还原 | 本机项目/数据源/工作流预览事件进入 Trace，审批引用和摘要可回查；模型端点使用确定性 mock |
| harness-engine/src/harness_engine/core.py | EngineRequest 带固定 EmployeeContext、带 SHA 的 Skill 文件、READ/PREVIEW EngineToolDefinition 和 opaque Home ref；Fake 不记录正文 | SDK 实际业务调用还需真实模型路由与 Dolphin Profile 验收 |
| harness-engine/src/harness_engine/engines/deepseek.py | 每 Run 新建 Harness；Session Home 稳定，prompt/工具 patch 独立；SDK close 会中断当前 Run | 本机假模型与假 MCP 已验证，真实容器/生产模型未验证 |
| backend/src/agent_platform/workers/ | 独立 Run Worker 与 Approval Action Worker 已加入 Compose；claim、heartbeat、fencing、取消与 UNKNOWN 回收通过 MySQL 验证 | 本机项目、数据源和 OFFLINE 工作流写入均由提交人批准；真实远端写后断连由 UNKNOWN 隔离用例验证，未对 D-S 人为注入该故障 |
| backend/src/agent_platform/adapters/mcp/stdio.py、harness_engine/mcp_proxy.py | Worker 只读挂载 StaffDeck `mcp` 子目录；按 SecretRef/cwd/command allowlist 启动，校验 25 项目录摘要并过滤当前 Run 工具 | 本机 Stdio 已完成项目/数据源/工作流读写验收；生产 Secret Manager/网络仍未验证 |
| frontend/src/features/platform/ConversationWorkbench.tsx、ConversationWorkbenchPage.tsx | 三栏 UI 已调用 Conversation/Run/Trace/Approval API；用户消息、实际工具和答复常显，内部 Trace 折叠并附中文解释；前端 55 项回归通过 | 本机端到端通过 API；模型由确定性 mock 触发验收动作 |
| backend/src/agent_platform/application/control_plane.py | Conversation turn 由 API 入队，独立 Worker 调用 `execute_claimed_run`；旧 Session Run API 保持同步 | 按 ExecutionProfile 注入真实 Runtime；HTTP turn 请求不启动 Runtime/MCP 进程 |
| backend/src/agent_platform/api/route_modules/conversations.py | 已提供员工过滤 GET /conversations 与 POST /{id}/turns；MySQL 8.4 列表、turn 联合事务和活动时间推进集成测试通过 | 真实 StaffDeck 工具执行仍待接通 |
| Run 审批 | V006/V007 仓储、加密参数/预览对象、预览/确认/拒绝 API 和独立 Approval Worker 已实现；MySQL 租户隔离、唯一约束与 lease/fencing 集成测试通过 | 本机由 Run 提交人确认后创建项目、数据源和 OFFLINE 工作流；确认前远端列表保持空 |
| Dolphin MCP Profile | 已导出员工“小刘”、`dolphinscheduler-dw-skill-2`、`dolphin_ds` Stdio Profile；25 个发现工具、20 个员工绑定工具和 Schema digest 已记录在 docs | 将 `/data/mcp` 只读子目录挂入 Worker，并把 StaffDeck 环境别名映射到 Worker SecretRef |

MCP Stdio Host 的 Profile 解析和过滤 proxy 已通过官方 SDK 与本机 Docker 验收：使用 shell-free 参数、tenant-scoped SecretRef 和 allowlisted cwd；启动时核验完整 tools/list catalog digest 与 ToolVersion Schema digest；模型只看到当前 Run 的 READ/PREVIEW 工具，直接调用未授权的写工具会被拒绝。DeepSeek Harness 每 Run 独立进程，临时 patch 不覆盖 Session Home，close 后擦除临时环境配置。本机完成真实 StaffDeck Stdio 项目/数据源查询、预览审批和 OFFLINE 工作流创建；生产模型与凭据管理仍待单独验收。

工作台前端已实现左侧员工过滤对话列表、中间消息/工具/Run 时间线、右侧只读 RunSnapshot 和审批预览。`ConversationWorkbenchPage` 已通过租户身份调用 Conversation/Message/Run/Trace/Approval API；任务状态从 Run API 轮询，工具时间线和 `approval_ref` 从 Trace API 读取。Approval 预览只在响应中的 `requested_by` 与当前用户相同时展示；确认时原样提交预览 SHA-256 和持久复用的幂等键。前端负责提交保护和展示，最终租户隔离与提交人授权仍由 API 强制执行。

Conversation API 入队、独立 Run Worker 与 Session lease 路径通过 disposable MySQL 8.4 全量后端套件（340 passed），包含 API 创建 turn、Worker 处理 Run、并发 Session 隔离、旧 fencing token 拒绝、V007 回填、UNKNOWN quarantine 和过期 Approval lease 回收。本机 Docker 使用 DeepSeek Harness SDK、确定性 mock provider、真实 StaffDeck Stdio server 和真实 DolphinScheduler acceptance 服务，完成提交人审批后的项目/数据源/两节点 OFFLINE 工作流创建，并验证自然语言只读工作流查询返回 MCP 结果；这验证运行与工具链路，不代表真实 DeepSeek 模型推理。

## 3. 架构和进程边界

~~~mermaid
flowchart LR
    U[用户] --> UI[员工工作台]
    UI --> API[FastAPI 控制面]
    API --> APP[Conversation / ControlPlane Service]
    APP --> DB[(MySQL: Conversation / Session / Run / Snapshot / Approval)]
    APP --> OBJ[(租户隔离对象存储: 消息与工具正文)]
    DB --> W[Backend Run Worker]
    W --> RA[RuntimeAdapter]
    RA --> HE[harness-engine]
    HE --> DH[DeepSeek Harness Agent Loop]
    HE --> MC[MCP Stdio Client]
    MC -->|stdin / stdout| MP[DolphinScheduler MCP 子进程]
    MP --> DS[DolphinScheduler]
    W --> DB
    W --> OBJ
    UI -->|查询状态 / 消息 / Trace| API
    UI -->|审批精确预览| API
~~~

### 组件职责

| 组件 | 负责内容 | 不负责内容 |
|---|---|---|
| UI 工作台 | 员工选择、对话列表、消息、运行状态、预览展示、审批和拒绝 | 不直连 Harness、MCP 或 DolphinScheduler |
| backend API | 身份、租户隔离、员工/部署校验、版本解析、Run 入队、审批校验、列表查询 | 不启动供应商 CLI 或 MCP 进程 |
| Application Service | 解析员工/Skill/Tool/Profile、固化 Snapshot、事务内保存消息与 Run、审批策略 | 不复刻 Agent Loop |
| MySQL | 业务索引、状态、租约、摘要、对象引用、审计元数据 | 不保存 prompt、完整工具参数/输出或密钥明文 |
| Run Worker | claim、lease、运行上下文装配、引擎/MCP 生命周期、事件落库、回收和错误分类 | 不绕过工具 allowlist 或审批 |
| RuntimeAdapter | 平台 RuntimeRequest 与 harness-engine 类型映射、能力声明、事件和 Usage 归一化 | 不自行决定员工权限 |
| harness-engine | Agent Loop、Runtime Session、模型事件适配、MCP 客户端/工具调用桥 | 不持有平台用户、租户或员工目录 |
| Dolphin MCP | 以 MCP 协议包装 DolphinScheduler 既有操作 | 不成为平台授权的最终决策者 |

### Stdio 生命周期

1. Worker 从 RunSnapshot 解析 MCPServerProfile 的精确版本，读取命令数组、允许工作目录和 SecretRef。
2. Worker 以参数数组启动 Stdio server，不使用 shell 字符串；密钥只通过 SecretProvider 临时注入子进程环境。
3. Worker 与子进程以 MCP 初始化和工具发现协议通信；stdout 只用于 MCP 帧，日志输出到 stderr。
4. Worker 校验远端工具名称、JSON Schema 摘要与固定 Profile 一致；目录漂移时拒绝运行。
5. 工具调用前由 Worker 校验 allowlist、输入 schema、风险等级和审批策略；执行结果成为规范 RuntimeEvent/ToolInvocation。
6. Run 结束、取消、租约丢失或超时后，关闭 MCP 会话并终止其进程组；不把子进程遗留在宿主机。

当前 Host 将租约失效作为 Run owner task 的取消信号处理：Worker heartbeat 可以从监控任务取消 Run task，由 Run task 在创建官方 SDK 上下文的同一任务中关闭 MCP 会话。不要让心跳任务跨任务退出官方 SDK AsyncExitStack。

这里的“同进程 Stdio”指 MCP 是 Worker 管理的本机 Stdio 子进程，在 Worker 的执行生命周期和隔离边界内运行；MCP Server 与 Worker 是不同 OS 进程，通过 stdin/stdout 通信。Worker/API 与 Dolphin MCP 均不提供新的对外 HTTP listener。

## 4. 代码模块和实现逻辑

### 建议模块职责

| 模块 | 目标职责 |
|---|---|
| backend/src/agent_platform/workers/run_worker.py | Worker 主循环、claim、lease heartbeat、Run/Approval 分发、优雅停机 |
| backend/src/agent_platform/adapters/mcp/stdio.py | Worker Run 作用域 Stdio 子进程启动、握手、目录核对、调用、超时和进程组清理 |
| backend/src/agent_platform/workers/approval_worker.py | Approval claim、精确 Tool/Profile 校验、一次性 WRITE 调用、结果引用和 UNKNOWN 收敛 |
| backend/src/agent_platform/ports/runtime.py | 扩充事件回调、能力和工具上下文的类型化 Runtime 端口 |
| backend/src/agent_platform/adapters/runtime/harness.py | 将 RuntimeRequest 转为真实 EngineRequest，实现 start/create/resume/run/cancel/close |
| backend/src/agent_platform/application/resolvers.py | 解析租户有权使用的精确资源版本，构建不可变 RunSnapshot |
| backend/src/agent_platform/application/control_plane.py | 创建 Run 时只入队；保留现有幂等、租户和状态校验 |
| backend/src/agent_platform/application/tool_approval.py | 预览绑定、审批挑战、确认/拒绝、审批结果状态迁移 |
| backend/src/agent_platform/infrastructure/mysql_execution.py | 原子 claim、lease/fencing、审批动作 claim 和审计事务 |
| backend/src/agent_platform/api/route_modules/conversations.py | Conversation 列表、turn 提交、消息分页 |
| backend/src/agent_platform/api/route_modules/deployments.py 或独立审批路由 | Run 状态/Trace 和审批读写 API |
| harness-engine/src/harness_engine/core.py | 定义类型化的系统指令、Skill、工具 schema、事件与 Session 契约 |
| harness-engine/src/harness_engine/engines/deepseek.py | 将标准请求实际传入 Harness SDK；不支持能力时明确失败 |

上表是目标模块拆分，不表示这些文件全部已实现。新建文件时公共类、函数和路由要有中文用途说明。

### Worker 执行伪代码

~~~text
循环直到收到 shutdown:
  以 worker 身份从 MySQL 原子领取一个 QUEUED Run 或 APPROVED ToolApprovalRequest
  拿到 fencing_token 后启动续租；租约失效时立即停止写操作并杀掉本地子进程
  按 platform_id + tenant_id 读取已固定 Snapshot、正文对象和 Profile 版本
  校验部署、Profile、工具目录摘要和工具授权仍可用于该任务
  若任务是 AGENT_RUN:
      构造 RuntimeRequest(system_prompt, skills, files, allowed_tool_specs, prompt)
      调用 RuntimeAdapter，逐个保存经过脱敏的事件和正文对象引用
  若任务是 APPROVED_TOOL_ACTION:
      原子消费唯一审批授权，使用其绑定的精确工具版本和参数对象执行一次
  在 fencing token 仍有效的事务中提交结果、Trace、Usage、审计和 Session 引用
  释放租约并关闭 Harness / MCP 资源
异常:
  可证明远端尚未执行 -> FAILED
  写请求可能已抵达远端但未收到可信响应 -> UNKNOWN，不重试
  Worker 丢失租约 -> 旧 worker 不得提交状态，递增 token 后由协调器标记未知
~~~

### Runtime 请求契约扩展

平台 RuntimeRequest 与 Harness EngineRequest 使用类型化员工上下文、Skill 文件、工具 Schema 和 opaque Session Home 引用；员工目录解析从 RunSnapshot 固定版本重建 READ/PREVIEW ToolVersion 和租户 MCP Profile。RuntimeAdapter 每次 Run 创建独立 DeepSeek Harness，并为该 Run 编译临时 Stdio proxy patch。官方 SDK 契约测试用本机假模型和 MCP 子进程验证两个员工在同一 Session Home 下的 system prompt/tool list 不串用；WRITE 工具不会进入 Run allowlist。

| 字段 | 类型/形式 | 边界 |
|---|---|---|
| run_id | string | 平台 Run ID，用于所有事件关联 |
| session | SessionRef 或 null | Runtime 原生 opaque 引用，不解析其私有格式 |
| prompt | string 或受控内容引用解出的文本 | 不写入审计 payload |
| employee_context | EmployeeContext | employee/version、固定 system prompt、Skill refs/顺序/文本文件摘要和模型路由；repr 不含指令正文，字段不含 SecretRef/密钥 |
| employee_context.skill_instructions | string[] | 精确 SkillVersion 顺序；只进入 Runtime 输入，不写 Trace |
| employee_context.skill_files | SkillFileContext[] | 按固定版本从 ObjectStore 读取文本文件并核 SHA-256；非文本文件不伪装为指令 |
| tool_definitions | EngineToolDefinition[] | 包含精确 Tool ID/version/name/description/schema；类型层拒绝 WRITE，只允许 READ/PREVIEW |
| tool_executor | 进程内 Protocol 或受控引用 | 由 Worker/MCP Gateway 执行；引擎不能自行扩展权限 |
| metadata | string map | 仅 trace_id、profile 摘要等非秘密元数据 |

EngineRequest 不得接收环境变量明文、密码、访问令牌或完整 MCP Profile secret。恢复 Session 必须调用引擎真实 resume_session(SessionRef)；只在本进程缓存集合中命中不算恢复能力。

## 5. 数据模型和关系

### 实体关系

~~~mermaid
erDiagram
    TENANT ||--o{ DIGITAL_EMPLOYEE : owns
    DIGITAL_EMPLOYEE ||--o{ EMPLOYEE_VERSION : versions
    EMPLOYEE_VERSION ||--o{ SKILL_BINDING : binds
    EMPLOYEE_VERSION ||--o{ TOOL_BINDING : binds
    MCP_SERVER_PROFILE ||--o{ TOOL_VERSION : exposes
    TOOL_VERSION ||--o{ TOOL_INVOCATION : invoked_as
    EMPLOYEE_VERSION ||--o{ DEPLOYMENT : published_as
    CONVERSATION ||--o{ AGENT_BINDING : binds
    AGENT_BINDING }o--|| SESSION : owns
    CONVERSATION ||--o{ MESSAGE : contains
    SESSION ||--o{ RUN : executes
    RUN ||--|| RUN_SNAPSHOT : freezes
    RUN ||--o{ TOOL_INVOCATION : traces
    TOOL_INVOCATION ||--o| TOOL_APPROVAL_REQUEST : gates
~~~

Conversation 与 Session 保持独立。一个 Conversation 可有多个 AgentBinding；单员工工作台按 employee_id 筛选，通常每个绑定对应一个独立 Platform Session。每个 Run 在创建时绑定 EmployeeVersion、SkillVersion、ToolVersion、ExecutionProfile、ModelRoute、MCP Profile 及相关摘要。

ER 图中的 RunSnapshot 是 runs.snapshot_json 内的版本化 JSON 文档，不是单独的 MySQL 表。
ToolInvocationRecord 是平台 Trace 中的 tool 类型 Span，不是独立表；tool_invocation_id 关联该 Span 的 span_id。

### 复用对象

| 对象 | 当前关键字段 | 在执行闭环中的用途 |
|---|---|---|
| Conversation | conversation_id、platform/tenant、title、is_group、status | 工作台列表主对象 |
| AgentBinding | conversation、employee、session、status | 把当前员工绑定到独立 Platform Session |
| Message | message、conversation、sender、source、text、routing_depth | 用户任务和员工回复；持久化正文放加密对象存储 |
| SessionRecord | deployment、employee version、trace、runtime_session_ref | 平台会话和 Runtime opaque 引用映射 |
| RunRecord / MySQL runs | status、submitted_by_user_id、idempotency、request_digest、worker、lease、fencing、snapshot_json | 入队、执行、恢复审计和审批授权主体 |
| RunSnapshot | employee/version、Skill/Tool 精确版本、ExecutionProfile、ModelRoute、文件包快照 | 运行输入和授权不可变基线 |
| ToolInvocationRecord | tool name/protocol/version/status/input/output refs/approval ref | 记录工具调用和审批关联 |
| audit_events | actor、event、run/session、fencing、受限 payload | 可核验的操作者与状态变更 |

### 新增或扩展对象

以下是目标 schema 设计，不是现有表。实现时 SQL 只放 docs/migrations/；新表须有中文表级 COMMENT，每列须有准确中文 COMMENT，并用 MySQL 集成测试查询 information_schema.TABLES/COLUMNS 验证。

本 Change 的向前迁移包括 V006__mcp_profiles_and_approvals.sql 和 V007__session_runtime_leases.sql：V006 新增 Profile 与审批表并扩展 Run/ToolVersion；V007 对同一 Session Home 串行 Harness Run，租约过期会隔离 Session，不能自动接管不确定的旧进程。迁移不复制 RunSnapshot 内容，也不存密钥。

#### mcp_server_profiles

一个 Profile 版本描述一个受控 Stdio server，不保存密钥值。

| 字段 | 类型/边界 | 说明 |
|---|---|---|
| platform_id、tenant_id | CHAR(26) + CHAR(26) | 限定平台和租户，不允许跨租户复用未授权配置 |
| profile_id | CHAR(26) | MCP Profile 稳定标识 |
| version | INT UNSIGNED | 不可变配置版本，必须大于 0 |
| name | VARCHAR(128) | 管理员可读名称 |
| transport | VARCHAR(16) | 首期仅允许 stdio |
| command | VARCHAR(512) | 受控可执行程序的绝对路径或注册名称 |
| args_json | JSON | 参数数组，不进行 shell 展开 |
| working_directory_ref | VARCHAR(512) | 允许工作目录的 opaque 引用，拒绝任意宿主路径 |
| environment_secret_refs_json | JSON | env name 到 SecretRef 的映射，只引用秘密仓库 |
| enabled_tool_names_json | JSON | Profile 管理员显式启用的工具名称数组 |
| tool_catalog_sha256 | CHAR(64) ASCII | 发现目录和 schema 的规范 SHA-256 |
| startup_timeout_ms、tool_timeout_ms | INT UNSIGNED | 启动和单次工具超时毫秒数，配置有上限 |
| status | VARCHAR(32) | 仅 DRAFT、ACTIVE、DISABLED |
| created_at、updated_at | TIMESTAMP(6) | UTC 版本创建和修改时间 |

主键为 (platform_id, tenant_id, profile_id, version)，外键关联同租户 tenants；按 (platform_id, tenant_id, status, name) 建目录索引。Profile 发布后不可原地修改；修改命令、参数、目录、环境变量引用或工具目录都要创建新版本。

#### ToolVersion 的 MCP 扩展

沿用已有 ToolVersion 的 protocol、input_schema、auth_secret_ref 等字段，MCP ToolVersion 追加：

| 字段 | 类型/边界 | 说明 |
|---|---|---|
| mcp_server_profile_id | CHAR(26)，可空 | 仅 protocol=mcp 时指向 Profile |
| mcp_server_profile_version | INT UNSIGNED，可空 | 固定 Profile 的不可变版本 |
| remote_tool_name | VARCHAR(128) | MCP tools/list 中的精确名称 |
| input_schema_sha256 | CHAR(64) ASCII | 防止发现到的 schema 与发布 ToolVersion 不一致 |
| mcp_risk_level | VARCHAR(16)，新列 | MCP 风险等级 READ/PREVIEW/WRITE；现有 risk_policy 保持原语义 |
| preview_tool_name | VARCHAR(128)，可空 | 写工具关联的只读预览方法 |
| idempotency_support | BOOLEAN | 声明远端是否接受稳定幂等键；不能据此猜测执行结果 |

MCP Profile ID/version 必须同时为空或同时存在；存在时外键指向同租户精确 Profile 版本。ToolVersion 仍受已有 Employee/Skill 精确版本绑定和 Tool 策略约束。

#### tool_approval_requests

每条请求授权一个具体工具版本和一份精确参数摘要，不是对员工或工具的长期授权。

| 字段 | 类型/边界 | 说明 |
|---|---|---|
| approval_id | CHAR(26) | 审批请求主键 |
| platform_id、tenant_id | CHAR(26) + CHAR(26) | 与运行和操作者相同的租户范围 |
| run_id、session_id | CHAR(26) + CHAR(26) | 同租户审批来源 |
| conversation_id | VARCHAR(64) | 用户可见对话标识 |
| tool_invocation_id | CHAR(26)，唯一 | 关联唯一一次预览/调用意图 |
| tool_id、tool_version | CHAR(26) + INT UNSIGNED | 绑定精确 ToolVersion |
| mcp_server_profile_id、mcp_server_profile_version | CHAR(26) + INT UNSIGNED | 绑定精确 Stdio Profile 版本 |
| arguments_ref、arguments_sha256 | VARCHAR(512) ASCII + CHAR(64) ASCII | 参数存外部加密对象，数据库只保存对象引用和摘要 |
| preview_ref、preview_sha256 | VARCHAR(512) ASCII + CHAR(64) ASCII | 用户所见预览与批准内容绑定 |
| confirmation_idempotency_key | VARCHAR(255) ASCII，可空 | 提交者确认时的租户范围幂等键 |
| state | VARCHAR(32) | PENDING/APPROVED/REJECTED/EXPIRED/RUNNING/SUCCEEDED/FAILED/UNKNOWN；APPROVED 即可被 Worker claim |
| expires_at | TIMESTAMP(6) | 确认过期边界 |
| requested_by | CHAR(26) | 提交该 Run 且唯一有权批准本次写操作的同租户用户 |
| approved_by | CHAR(26)，可空 | 实际批准用户；必须等于 requested_by |
| approved_at、consumed_at、finished_at | TIMESTAMP(6)，可空 | 批准、授权消费、动作终态时间 |
| worker_id | VARCHAR(128)，可空 | 当前持有动作租约的 Worker |
| fencing_token | BIGINT UNSIGNED | Worker claim 代数；旧 token 不能提交状态 |
| lease_expires_at | TIMESTAMP(6)，可空 | 批准动作 Worker 租约截止时间 |
| error_code | VARCHAR(128)，可空 | 稳定错误分类，不保存敏感正文 |

主键为 (platform_id, tenant_id, approval_id)，tool_invocation_id 和非空 confirmation_idempotency_key 均在租户内唯一；使用 state/created_at/approval_id claim 索引和 state/lease_expires_at 回收索引。该行同时承载批准后的动作队列，不另建重复状态表；一个 Run 同时最多保留一条 PENDING 审批。

审计不得保存明文 arguments、完整预览、prompt、密钥或模型内部推理。对象正文必须走租户隔离、加密和对象引用/哈希校验流程。

### RunSnapshot JSON 格式

RunSnapshot 保存用哪个版本与授权执行，不把秘密、prompt 或完整模型内容放入 JSON。以下是规范示意：

~~~json
{
  "schema_version": 1,
  "employee": {
    "employee_id": "employee-ds",
    "version": 2,
    "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "skills": [
    {
      "skill_id": "dolphinscheduler-dw-skill-2",
      "version": 1,
      "manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
  ],
  "tools": [
    {
      "tool_id": "ds-workflow-create",
      "version": 1,
      "profile_id": "dolphinscheduler-mcp",
      "profile_version": 3,
      "remote_tool_name": "create_workflow",
      "input_schema_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "mcp_risk_level": "WRITE",
      "exposure": "APPROVAL_ONLY"
    }
  ],
  "execution_profile": {
    "profile_id": "deepseek-worker",
    "version": 1,
    "engine_name": "deepseek-harness",
    "model_route_id": "route-ds"
  },
  "model_tool_names_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "approval_action_tools_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "resolved_at": "2026-11-18T10:00:00Z"
}
~~~

Run prompt 正文来自消息对象引用。Snapshot 可以记录 WRITE ToolVersion 的精确引用用于审批动作校验，但 exposure=APPROVAL_ONLY 的工具不会传给模型；Engine 只收到 READ/PREVIEW 工具。规范摘要按 UTF-8 JSON、键排序、无多余空格、禁止 NaN 的 canonical JSON 计算；数组顺序有业务语义时保持固定顺序，不能排序改变 Skill 顺序。

### Conversation 列表 DTO

列表项是聚合 DTO，不要求增加冗余的 Conversation 表。服务端必须按已验证的 platform/tenant 范围查询，并按 updated_at DESC、conversation_id DESC 稳定分页。

~~~json
{
  "items": [
    {
      "conversation_id": "conv-01",
      "title": "ODS 到 DWD 工作流",
      "employee": {
        "employee_id": "employee-ds",
        "employee_name": "海豚调度数仓开发"
      },
      "session_id": "sess-01",
      "last_message": {
        "message_id": "msg-09",
        "preview": "先查询项目和数据源，再生成离线工作流预览。",
        "source": "user",
        "created_at": "2026-11-18T10:24:00Z"
      },
      "last_run": {
        "run_id": "run-07",
        "status": "AWAITING_APPROVAL"
      },
      "pending_approval_count": 1,
      "updated_at": "2026-11-18T10:24:00Z"
    }
  ],
  "next_cursor": null
}
~~~

列表预览只给当前调用用户展示有权读取的消息正文；未授权时不返回 preview。pending_approval_count 是展示摘要，不作为审批权限依据。

列表查询不新增 Conversation 冗余表。MySQL 仓储在 `platform_id + tenant_id + conversation_participants.user_id + employee_id` 范围内聚合，消息/Run/Approval 正文不进入查询结果；last_message 对象引用由 ObjectStore 在授权查询后读取，压缩为空白并最多返回 120 个字符。分页游标是 URL-safe Base64 编码的 `(updated_at, conversation_id)`，顺序为 `updated_at DESC, conversation_id DESC`；游标只用于定位下一页，不承担授权作用。

消息索引写入会在保存消息的事务中推进 conversations.updated_at；Run 进入 QUEUED、RUNNING、终态或 UNKNOWN 时，在对应 Run 状态事务里通过 agent_bindings.session_id 推进其 Conversation 更新时间；Approval 创建、确认、领取和结束时也推进更新时间，使待审批数量变化立即反映在列表顺序中。

当前验证：Fake/API 分页、turn 幂等与租户隔离、Approval 预览摘要/提交者授权测试，以及 MySQL 8.4 集成分页、turn rollback、Approval 信息架构注释和真实 `updated_at` 推进均已通过 disposable MySQL 全量套件。

Approval Action Worker 的 Host 测试覆盖精确 WRITE ToolVersion/Profile、Schema 漂移拒绝、租约 heartbeat 失效后的 owner task 取消、写调用超时进入 UNKNOWN、结果对象引用落库和重复 claim 不重放。MySQL 8.4 已实际执行过期 RUNNING Approval lease 回收：Approval 与 Run 进入 UNKNOWN、fencing token 递增，后续 claim 不再次执行。

## 6. 状态、审批和错误语义

### Run 状态迁移

V006 已允许持久化 QUEUED、STARTING、RUNNING、AWAITING_APPROVAL、CANCELLING、SUCCEEDED、FAILED、CANCELLED、UNKNOWN；V007 另用 `session_runtime_leases` 保护稳定 Harness Home。员工工作台 Run 由独立 Worker 执行；旧同步 Run API 仍在 API 进程内执行。

~~~mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> STARTING
    QUEUED --> CANCELLED: 提交者在 claim 前取消
    STARTING --> RUNNING
    STARTING --> CANCELLED: 提交者取消且 Runtime 尚未开始
    RUNNING --> AWAITING_APPROVAL: 预览生成并创建审批请求
    AWAITING_APPROVAL --> CANCELLED: 用户拒绝或审批过期
    AWAITING_APPROVAL --> SUCCEEDED: 批准动作执行成功并写入结果
    AWAITING_APPROVAL --> FAILED: 批准动作确定失败
    AWAITING_APPROVAL --> UNKNOWN: 批准动作结果不确定
    RUNNING --> SUCCEEDED: Agent 执行完成
    RUNNING --> FAILED: 可证明未产生远端副作用的错误
    RUNNING --> UNKNOWN: 远端写请求结果不确定或租约丢失
    RUNNING --> CANCELLING: 请求取消
    CANCELLING --> CANCELLED: 确认已停止
    CANCELLING --> UNKNOWN: 无法证明远端停止
~~~

审批记录另行保存 REJECTED/EXPIRED。Run 进入 CANCELLED 并记录对应原因；不得把拒绝记为工具成功。UNKNOWN 是需人工/只读对账的终态。自动回收可以更新租约和 fencing token，但绝不能据 UNKNOWN 自动重放 DolphinScheduler 写请求。

### 独立 Run Worker 实现边界

`backend/src/agent_platform/workers/run_worker.py` 提供平台范围的 MySQL claim 和单 Run 执行循环。claim 使用 `FOR UPDATE SKIP LOCKED` 领取最早 QUEUED Run，先写入 STARTING、worker_id、租约与递增 fencing token；Worker 校验并解码 Run 关联 USER Message 对象后，以当前 token CAS 到 RUNNING，再调用 ControlPlane 的 `execute_claimed_run`。员工、Skill、Tool、Profile 从 RunSnapshot 的精确版本恢复；后续新发布不会切换该 Run 的版本。

每轮 heartbeat 续租并观察 CANCELLING。租约失效会先将 Run 收敛到 UNKNOWN 并递增 fencing token，再尽力调用 Runtime.cancel；即使旧 Runtime 线程迟到返回，MySQL 终态 CAS 也会拒绝它。运行取消只有 Runtime 确认后才标记 CANCELLED；取消能力缺失、取消超时或执行结果不可信时为 UNKNOWN、不自动重试。Run 提交者通过 `POST /api/v1/runs/{id}/cancel` 发起取消。

Worker CLI 为 `agent-platform-run-worker`，control-plane Compose 单独启动该服务并复用 MySQL 与对象存储卷。常规本机 Compose 使用 FakeRuntimeAdapter；acceptance overlay 使用 DeepSeek Harness 和本地确定性 provider。MySQL 队列/claim/终态、Docker Worker 重启、过期 lease UNKNOWN 隔离、旧同步 API 兼容和本机 DolphinScheduler 纵切均已验证。

### Approval Action Worker 实现边界

`backend/src/agent_platform/workers/approval_worker.py` 提供独立 `agent-platform-approval-worker` CLI 和单动作执行器：原子领取 APPROVED 审批后读取精确 ToolVersion/Profile，验证 WRITE 风险、Profile 状态、参数/预览摘要和 live `tools/list` Schema，再调用且只调用绑定的远端工具。结果正文写入租户对象存储；Run `result_ref`、Tool Trace 摘要和 Worker 审计只保存对象引用与摘要。审批 Worker 已加入基础 Compose；StaffDeck MCP volume subpath 与 SecretRef 则由本机 acceptance overlay 注入。

远端工具返回明确成功后记为 SUCCEEDED；发出写调用后的超时、MCP error 或网络异常一律记为 UNKNOWN，不自动重放；调用前 Profile/Schema/参数拒绝记为 FAILED。Worker heartbeat 丢失会取消 owning Run task 并清理 Stdio 子进程；持久化 RUNNING Approval lease 过期后，下一次 claim 将审批和 Run 一起收敛到 UNKNOWN 并递增 fencing token。即使调用未获可信结果，旧 token 也不能提交成功状态。

Fake Host 测试覆盖精确工具版本、Schema 漂移、结果引用保存、远端超时、lease loss 和重复领取；disposable MySQL 集成覆盖审批租户隔离、唯一约束、消费、fencing 和过期 lease 收敛。真实 DolphinScheduler 已创建项目、数据源和 OFFLINE 工作流；本次没有向该服务注入“远端已写入但响应丢失”，该不确定结果由隔离 Host/MySQL 用例覆盖并保持人工只读对账边界。

### Approval 状态迁移

~~~mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> APPROVED: 用户显式确认，写入 approved_by/approved_at
    PENDING --> REJECTED: 用户拒绝
    PENDING --> EXPIRED: 超过 expires_at
    APPROVED --> RUNNING: Worker claim 并取得 lease/fencing
    RUNNING --> SUCCEEDED: MCP 返回可信成功结果
    RUNNING --> FAILED: 可证明远端未产生副作用
    RUNNING --> UNKNOWN: 远端结果不确定或 lease 丢失
~~~

APPROVED 是动作队列状态。审批记录行自身以 FOR UPDATE SKIP LOCKED 领取；不另建 QUEUED 状态或重复的动作表。

### 一次性预览与审批流程

~~~mermaid
sequenceDiagram
    participant U as 用户
    participant UI as 工作台
    participant API as backend API
    participant W as Worker
    participant H as Harness
    participant MCP as Dolphin MCP
    participant DB as MySQL

    U->>UI: 提交创建 ODS 到 DWD 工作流
    UI->>API: POST conversation turn + idempotency_key
    API->>DB: 同事务保存用户消息、RunSnapshot、QUEUED Run
    API-->>UI: 202 + run_id
    W->>DB: claim Run + fencing_token
    W->>H: prompt + system/Skill + allowlisted tools
    H->>MCP: projects/datasources/workflows 只读查询
    MCP-->>H: 查询结果
    H->>MCP: create preview，只生成预览
    MCP-->>H: 精确工作流定义和参数摘要
    W->>DB: 保存 preview_ref/digest，创建 PENDING approval
    W-->>UI: Run=AWAITING_APPROVAL + preview
    U->>UI: 核对项目、数据源、节点、SQL、OFFLINE
    UI->>API: POST approval confirm + preview_digest + idempotency_key
    API->>DB: 校验租户/权限/有效期/digest；批准并排队一次动作
    W->>DB: claim 批准动作；原子消费授权
    W->>MCP: 按绑定参数调用 create_workflow 一次
    MCP-->>W: 成功结果或明确错误
    W->>DB: 更新 ToolInvocation/Run/Trace/审计
    W-->>UI: 显示 OFFLINE 创建结果
~~~

### 安全的写工具边界

- Agent 模型获得只读工具和 preview 工具；不直接获得 DolphinScheduler 写工具的可调用凭据。
- preview 与 write 是两种不同 ToolVersion。write ToolVersion 只能由审批动作 Worker 调用。
- preview 返回的参数、对象摘要、写工具和目标对象全都绑定到同一 approval_id。
- 确认 API 校验调用者与 requested_by 相同且属于同租户；preview_digest 与当前请求完全相同；请求幂等键未被其他内容使用。
- 数据库事务把审批变为 APPROVED 并排入 Worker 动作队列；重复点击返回原动作状态，不新增写请求。
- 审批行本身承载批准动作队列与租约字段，不另建一个含重复状态的动作表。
- Worker 在出站调用前原子消费授权；执行前再次确认 Profile、ToolVersion、schema digest 和 fencing token。
- 对外请求必须使用稳定的 MCP 幂等键（若远端支持）。若 MCP 不支持幂等而网络超时，则标记 UNKNOWN，不重试。
- 发布、启动、调度需要新 preview、approval_id、digest 和一次性写调用。

### 核心错误映射

| 情况 | Run/审批结果 | UI/API 行为 |
|---|---|---|
| Profile 命令或 MCP SDK 不可启动 | FAILED | 显示配置/依赖错误码，不回退 Fake |
| MCP 工具目录与摘要不一致 | FAILED | 拒绝调用，提示管理员发布新 Profile/ToolVersion |
| Approval 参数/预览摘要不匹配 | FAILED | 出站前拒绝，不调用 MCP 写工具 |
| 工具不在有效 allowlist | FAILED | 不向 MCP 进程发送调用 |
| 输入不满足 ToolVersion JSON Schema | FAILED | 调用前拒绝，Trace 保存错误类别 |
| 审批超时或拒绝 | ToolApproval EXPIRED/REJECTED，Run CANCELLED | 禁用确认按钮，保留预览和审计结果 |
| 批准已消费 | 返回已有状态 | 幂等响应，不重复调用 |
| Approval Worker heartbeat 失效或 lease 过期 | Run/动作 UNKNOWN | 取消 owner task 并回收 Stdio 进程；恢复扫描递增 fencing token，不重领动作 |
| 远端工具返回 isError 或发生调用超时/连接异常 | UNKNOWN | 写调用已经发出，结果需只读对账；不自动重试 |
| Profile/ToolVersion/schema/参数在出站前校验失败 | FAILED | 记录稳定错误类别，不调用远端写工具 |

## 7. API 设计

以下是目标 API 合约。标注“已有”的路径不代表已经异步 Worker 化。

| 方法与路径 | 状态 | 请求/响应重点 |
|---|---|---|
| POST /api/v1/conversations | 已有 | 创建 Conversation；租户从 IdentityContext 校验 |
| POST /api/v1/conversations/{id}/bindings | 已有 | 绑定员工与 Platform Session |
| GET /api/v1/conversations | 已实现；MySQL 8.4 集成通过 | employee_id/status/cursor/limit 过滤；返回 ConversationListItem 和 next_cursor |
| GET /api/v1/conversations/{id}/messages | 已有，需补分页 | 按 cursor/limit 返回消息索引和授权正文 |
| POST /api/v1/conversations/{id}/turns | 已实现；MySQL 8.4 联合事务/回滚集成通过 | {tenant_id, employee_id?, text, idempotency_key}；原子保存用户消息和 RunSnapshot；返回 202 与 Message/Run ID |
| GET /api/v1/runs/{id} | 已有 | 增加 AWAITING_APPROVAL 和异步 queued 状态的规范响应 |
| POST /api/v1/runs/{id}/cancel | 已实现；MySQL fencing/Worker 测试通过 | 仅 Run 提交者可请求；QUEUED 直接 CANCELLED，已 claim Run 转 CANCELLING 交由 Worker 取消 |
| GET /api/v1/runs/{id}/trace | 已有 | 返回工具、模型和审批相关的脱敏 span |
| GET /api/v1/tool-approvals/{id} | 已实现；MySQL 8.4 集成通过 | 仅 Run 提交者可读取解密预览；返回摘要、精确工具/Profile 版本、过期时间和状态，不返回参数对象引用 |
| POST /api/v1/tool-approvals/{id}/approve | 已实现；MySQL 行锁、唯一键和本机 DolphinScheduler E2E 通过 | {tenant_id?, preview_sha256, idempotency_key}；仅提交者可批准，原子排入一次性动作，不直接调用 MCP |
| POST /api/v1/tool-approvals/{id}/reject | 已实现；MySQL 集成与本机拒绝清理通过 | {tenant_id?}；由提交者幂等拒绝并关闭绑定 Run |
| GET /api/v1/runs/{id}/events?after= | 可选后续 | 有持久 cursor 后再提供 SSE/long-poll；首个纵切可轮询 Run 与 Trace |

用户消息提交接口的幂等摘要包括 conversation/session、message body digest、employee/version snapshot digest 和 idempotency key。相同键同摘要返回原 Run；同键不同摘要返回 409 IDEMPOTENCY_CONFLICT。不应让 API 请求线程等待 Harness 或 DolphinScheduler。

API 的 tenant_id 请求字段必须与身份解析出的租户一致；服务端不接受用户传入 worker_id、角色、fencing token、approval state 或 effective tool list。

## 8. 工作台页面和交互

可打开的原型：[docs/原型/海豚调度员工工作台.html](原型/海豚调度员工工作台.html)。

### 页面结构

~~~text
┌──────────────────────────────────────────────────────────────┐
│ Agent Platform   数字员工 / 资源中心 / 部署记录 / 运行工作台 │
├──────────────┬──────────────────────────┬────────────────────┤
│ 对话列表     │ 对话 / 执行               │ 本次 Run 快照       │
│ 新对话       │ 用户任务、员工消息         │ 员工版本、Skill     │
│ 当前员工会话 │ MCP 只读调用、预览卡片     │ Model/MCP Profile   │
│ 运行状态时间 │ 输入框、取消/继续           │ Trace、Usage、状态  │
└──────────────┴──────────────────────────┴────────────────────┘
~~~

### 面板字段和行为

| 面板 | 字段 | 行为 |
|---|---|---|
| 左侧对话列表 | 标题、最后消息摘要、时间、Run 状态、待审批数 | 新对话；按当前员工过滤；更新时间倒序游标分页；选中后恢复消息 |
| 对话主体 | 用户/员工消息、Session 标识、工具调用与阶段状态 | 发送消息时生成 idempotency key；禁用重复提交；排队后轮询 |
| 工具事件 | ToolVersion、只读/预览/写状态、耗时、稳定错误码 | 默认折叠参数正文；不显示密钥；写操作必须进入审批卡片 |
| 审批预览 | 项目、工作流名称、数据源、DAG、节点参数、OFFLINE 标志、摘要、有效期 | 用户显式确认/拒绝；digest mismatch 或过期后禁用确认 |
| Run 快照 | 员工和版本、Skill、Tool allowlist、模型路由、MCP Profile 版本 | 只读展示，解释本次为什么可调用这些工具 |
| 窄屏布局 | 对话列表、主体、详情依次堆叠 | 不横向滚动；列表仍在顶部可访问 |

原型的两种布局：对话驱动优先显示自然语言消息与预览；流程画布把执行步骤和 DAG 预览并列。两者共享对话列表、审批状态和 OFFLINE 安全提示。示例数据标注“待确认/只读完成”，不表示真实调用已发生。

### UI 状态和可访问性

- 对话行使用按钮语义；当前项有明确的选中标志，不只依靠颜色。
- Run 使用文本和颜色共同表达 QUEUED、RUNNING、AWAITING_APPROVAL、SUCCEEDED、FAILED、CANCELLED、UNKNOWN。
- 等待用户处理的审批显示到期时间、精确预览摘要和显式确认/拒绝控件。
- UNKNOWN 显示“结果待对账”，不显示“失败，可重试”。
- 预览内容任何一字段改变都使旧批准失效。

### 前端 API 数据流

1. 页面按当前 `X-Fake-User` 身份读取 ACTIVE 部署，并为每个员工选取最新活动版本；员工选择变化后按 `employee_id` 请求 Conversation 游标页。
2. 选中对话后并行读取消息、最新 Run 与 Run Trace。Run 在 QUEUED/STARTING/RUNNING/AWAITING_APPROVAL 等非终态每 2 秒轮询；Trace 每 3 秒刷新，终态停止轮询。
3. 若 Trace 工具 Span 带 `approval_ref` 且 Run 状态为 AWAITING_APPROVAL，页面读取审批预览。只有响应 `requested_by` 等于当前身份且审批仍为 PENDING 时启用确认/拒绝。
4. 确认请求提交页面展示的原始 `preview_sha256`，相同 Approval 重试复用同一幂等键；审批成功后刷新 Approval、Run、Trace 和对话摘要。Run UNKNOWN 只显示待对账，不提供重新执行入口。
5. 提交 turn 时在请求等待期间禁用提交按钮；相同对话和正文在失败重试时复用幂等键，失败保留输入草稿。成功后立即跟踪返回的 Run ID，并刷新消息与对话列表。
6. 新对话复用现有 API，依次创建 Session、Conversation、AgentBinding。它们不是跨服务原子事务；中途失败可能留下未绑定 Session 或空 Conversation，界面显示错误，后续需由清理流程回收。
7. Query Cache key 包含当前用户标识；切换身份后清除所选员工/对话、临时 Run 关联和本地新建对话，并重新以新身份取数。后端 tenant/participant 授权仍是数据边界，前端缓存键不能替代后端校验。

前端集成验证覆盖新建对话的三步调用、对话分页/恢复、Run/Trace 轮询入口、同一幂等键提交保护、Approval digest 原样确认、拒绝、非 Run 提交者预览隐藏，以及切换租户后不显示旧租户对话。消息列表目前由现有 API 一次性返回；消息级游标分页不在本次实现范围内。

### 工作台首版组件验证

当前 `ConversationWorkbench` 由受控属性接收员工、对话、时间线、RunSnapshot 和审批预览，保证页面展示的数据由上层授权查询结果提供。组件测试覆盖对话选择、分页触发、空/加载/错误状态、窄屏三栏区域可达、审批摘要回传及 UNKNOWN 不出现重试操作。列表 API 和提交/审批动作接线属于下一项任务。

验证命令与结果：

~~~bash
cd frontend
npm test -- --run src/features/platform/ConversationWorkbench.test.tsx src/App.test.tsx src/features/management/ManagementLayout.test.tsx
npm run build
~~~

本次组件/API 路由测试 3 个文件、8 项通过；Vite 生产构建通过。构建仍提示主 JS chunk 超过 500 kB，属于后续代码分包优化项，不影响本次构建成功。

## 9. 分阶段落地顺序

### 阶段 0：Profile 与工具目录对齐

- 从 StaffDeck 导出 MCP 命令、参数、工作目录、环境别名、25 个服务工具 schema 和目标员工绑定的 20 个工具映射；完整内容见[配置清单](StaffDeck海豚调度MCP配置清单.md)。
- 对每个工具标记 READ / PREVIEW / WRITE，记录 preview 与 write 对应关系、远端幂等能力。
- 将敏感环境变量转成 SecretRef；验证可执行路径和允许工作目录。
- 发布版本化 MCP Profile 和 ToolVersion；记录工具目录 canonical digest。
- 知识库仍不接入。

交付：无密钥的可部署配置记录、allowlist 和工具映射表。当前待补：完整启动参数及环境变量别名。

### 阶段 1：只读员工 Run

- 拆 API 入队和 Worker 执行；明确区分 Fake Runtime 和生产 Runtime 注入。
- Worker claim Run、lease heartbeat、fencing 和故障回收。
- Harness 请求正确传入 system prompt、Skill instructions、文件 manifest、模型配置和只读工具 schema。
- Worker 拉起 MCP Stdio 子进程，校验工具目录 digest。
- 完成 projects/datasources/workflows 列表查询，返回标准 Trace 与对象引用。
- 增加对话列表分页和工作台轮询。

退出条件：多租户隔离、重启恢复和 Stdio 退出都可观测；任何 write tool 不会被暴露给模型。

### 阶段 2：预览和人工审阅

- 将 workflow preview 映射成稳定 ToolVersion 和规范 JSON。
- 持久化 preview 正文引用及 digest；展示项目、数据源、节点和 OFFLINE 状态。
- 预览过期、schema 漂移或资源变化时要求重新查询和重新确认。
- 该阶段只产出预览，确认按钮不执行实际写操作。

退出条件：用户所见内容与批准 digest 绑定，未确认前外部系统无写入。

### 阶段 3：单次 OFFLINE 创建

- 增加一次性 ApprovalRequest 和批准动作队列；每个审批可消费一次。
- 批准后 Worker 只执行绑定的 create workflow ToolVersion，保存为 OFFLINE。
- 正常成功回写工具结果消息、Run、Trace、Usage（如 SDK 提供）与审计。
- 错误和超时分别记录 FAILED / UNKNOWN，不自动重发写请求。
- MySQL 新增表/字段统一通过 docs/migrations/ 向前迁移；中文 COMMENT 及 information_schema 测试同步完成。

退出条件：重复确认、并发确认、Worker 超时、租约失效都不会产生二次创建。

### 阶段 4：发布、启动和调度

- 将发布、运行、创建/修改调度当作不同 ToolVersion 和 ApprovalRequest。
- 每个操作重新预览当前远端状态并让用户确认自己的参数摘要。
- 分别建立部署级风险策略、生产环境约束、审计查询和人工对账流程。

退出条件：没有一张审批能覆盖多个不同写操作；UNKNOWN 有只读对账入口。

## 10. 验收与验证

### 功能验收

1. 两个租户互相不可见 Conversation、Session、Run、Profile、审批和消息正文。
2. 新 Run 使用创建时固定的员工/Skill/Tool/Profile 版本；新发布不影响历史 RunSnapshot。
3. Worker 丢失 lease 后旧 fencing token 不能写 Run、Trace 或审批终态。
4. Stdio server 仅从 allowlisted command/args/cwd 启动；stdout 不混入日志；超时/取消会清理子进程。
5. 模型看不到未授权工具；输入 schema 不合法时 MCP 不会收到调用。
6. 写预览尚未确认时 MCP 写工具调用次数为 0。
7. 预览字段改变、digest 不匹配、审批过期或跨租户确认均拒绝。
8. 同一 approval 重复点击最多执行一次；写超时后 Run/动作进入 UNKNOWN 且不自动重试。
9. Conversation 列表稳定分页，摘要按租户和员工绑定过滤。
10. SQL 变更的每张表/字段有中文 COMMENT，并由 MySQL 集成测试读取 information_schema 验证。

### 验证命令

Conversation、turn 和审批 API 的本地可重复验证命令：

~~~bash
cd backend
python -m pytest tests/test_conversation_list.py tests/test_conversation_repository_contract.py \
  tests/test_conversation_activity_touch.py tests/test_conversations.py \
  tests/test_conversation_turn_repository_contract.py tests/test_run_lifecycle_status.py \
  tests/test_control_plane_service.py tests/test_tool_approval_service.py \
  tests/test_tool_approval_api.py tests/test_approval_action_worker.py \
  tests/test_mcp_lease_repository_contract.py tests/test_mcp_approval_domain.py \
  tests/test_extended_api.py tests/test_api_route_modules.py -q
python -m ruff check src/agent_platform/api/route_modules/conversations.py \
  src/agent_platform/application/conversations.py \
  src/agent_platform/application/conversation_turns.py \
  src/agent_platform/application/tool_approval.py \
  src/agent_platform/workers/approval_worker.py \
  src/agent_platform/infrastructure/mysql_conversations.py \
  src/agent_platform/infrastructure/mysql_execution.py
cd ../frontend
npm test -- --run src/features/platform/ConversationWorkbenchPage.test.tsx src/features/platform/ConversationWorkbench.test.tsx src/api/conversations.test.ts src/App.test.tsx
npm test
npm run build
~~~

Run Worker、claim SQL 合同、固定 RunSnapshot、取消 API、Runtime 取消能力和 Compose 静态验证：

~~~bash
cd backend
PYTHONPATH=../harness-engine/src python -m pytest \
  tests/test_run_worker.py tests/test_run_worker_repository_contract.py \
  tests/test_claimed_run_execution.py tests/test_runtime_employee_context.py \
  tests/test_runtime_adapter.py tests/test_harness_runtime_adapter.py \
  tests/test_extended_api.py tests/test_api_route_modules.py \
  tests/test_worker_compose.py -q
python -m ruff check src tests
cd ..
docker compose -f deploy/control-plane/docker-compose.yml config --quiet
~~~

涉及 MySQL 的集成验证需配置一次性 MySQL 8.4 数据库并运行 backend/tests/integration/test_mysql_conversations.py 和 backend/tests/integration/test_mysql_execution_repository.py；这两个文件还包含 turn 重放、幂等冲突、消息/Run 联合回滚及 Approval 列表聚合测试，不能用 SQLite 或 Fake 结果代替。MCP Stdio/DeepSeek SDK 需在隔离的测试账号或非生产环境完成运行证据。

Run Worker/Run claim/V006 information_schema 集成命令：

~~~bash
cd backend
AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 \
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3309 MYSQL_USER=root MYSQL_PASSWORD='<一次性实例密码>' \
python -m pytest tests/integration/test_mysql_execution_repository.py \
  tests/integration/test_mysql_conversations.py tests/integration/test_mysql_migration_runner.py -q
~~~

MySQL 集成测试会创建随机 disposable database 并在 teardown 删除；本地没有可用 MySQL greeting 时不应把被跳过或中断的测试记作通过。

### 已验证 / 未验证

| 范围 | 本次状态 |
|---|---|
| Conversation 列表服务、游标、授权预览与 API | 后端单元/API 测试通过；MySQL 查询契约测试通过 |
| 消息、Run 与 Approval 活动推进 Conversation.updated_at | Fake SQL 契约和 MySQL 8.4 集成测试通过 |
| Conversation turn API | Fake API 幂等/冲突及 MySQL 联合事务、回滚集成测试通过 |
| Approval 预览、确认/拒绝与一次性 Action Worker | API、Host、MySQL 过期 lease UNKNOWN/fencing 测试通过；本机真实 DolphinScheduler 项目/数据源/工作流由 Run 提交人批准后写入 |
| MySQL Run Worker 队列与固定快照执行 | 本机 Compose Worker 处理 Conversation Run；Worker 重启、旧同步 API、过期 lease UNKNOWN 隔离通过 |
| EngineRequest / RuntimeAdapter 员工上下文 | Backend Runtime/DeepSeek factory、工具 Schema digest、SDK 工具短名还原与租户上下文测试通过；Harness Engine 178 passed、3 skipped；本机 Worker 使用确定性 mock 调用真实 StaffDeck MCP |
| 后端全量回归 | disposable MySQL 8.4 下 340 passed；Backend Ruff 与 `compileall` 通过 |
| Harness Engine 回归 | 178 passed，3 skipped（显式 Harness MySQL 门禁与 Windows junction 用例） |
| 三栏员工工作台 UI 与 API 容器 | 前端 14 个测试文件、54 项通过；Vite production build 通过；本机 API/Run Worker/Approval Worker 经 Run 提交人审批创建 OFFLINE 工作流 |
| 本文 Mermaid 图和 JSON 契约 | 设计基线；不替代实际 API/数据库集成验证 |
| Dolphin MCP 完整 command/args/cwd/env alias | 已从 StaffDeck 导出；command/cwd/aliases、25 个工具 Schema 和风险/预览映射见[配置清单](StaffDeck海豚调度MCP配置清单.md) |
| 固定 DeepSeek SDK system/Skill/MCP 工具目录与 Session resume 契约 | SDK 与本机兼容 mock/provider + StaffDeck MCP Stdio 实际通过；真实 DeepSeek provider 和生产 Skill 正文导入仍未验证 |
| Worker 与 Stdio 子进程生命周期 | Run Worker/Session lease、proxy allowlist/Schema、timeout/取消/租约丢失清理测试通过；Docker acceptance overlay 中 Run/Approval Worker 均只读挂载 StaffDeck `mcp` 子目录并执行真实查询/写入 |
| Approval 到 DolphinScheduler 的一次性授权 | 项目、数据源、两节点 OFFLINE 工作流都只在提交人确认后创建；过期 approval lease 与远端不确定结果测试收敛 UNKNOWN，禁止自动重试 |
| OpenSpec strict validation | 本轮通过；迁移和真实运行仍需一次性 MySQL 及非生产 MCP 环境 |

3.2 的 EngineRequest、每 Run DeepSeek Harness、Session Home/V007 lease、过滤 Stdio proxy 和 SecretRef 边界已实现并通过 SDK/Backend 测试。OpenSpec 1.1、5.3、7.2 已完成；本机 DolphinScheduler 项目、数据源和 OFFLINE 工作流均经独立 Worker 与 Run 提交人审批闭环创建。知识库保持待定且未接入；真实 DeepSeek provider/生产凭据及 Skill 原文导入不属于本次本地验收结果。

## 11. 关联文档

- [后端与 Harness 引擎关系](后端与Harness引擎关系.md)
- [执行引擎适配设计](执行引擎适配设计.md)
- [数字员工数据模型](数字员工数据模型.md)
- [接口与状态流程](接口与状态流程.md)
- [MySQL Schema](mysql-schema.md)
- [员工工作台原型](原型/海豚调度员工工作台.html)
