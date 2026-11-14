# Design

## Context

当前仓库包含独立的 `harness-engine/` Python 子项目，已经提供 DeepSeek Harness 适配器、Fake 引擎、引擎生命周期、工具注册、MCP stdio 桥接、MySQL 控制面持久化、消息正文引用、租约/fencing token 和 Docker/Kubernetes 沙箱设计。现有核心协议不依赖具体供应商 SDK，现有文档还把 HTTP 控制面、数字员工产品层、上下文记忆、全链路 Trace 和 Token 计量留在后续阶段。

本变更在同一仓库内增加平台控制面文档契约和模块边界。控制面先使用 Fake IdentityProvider 和直接模型配置；后续通过端口替换为 Dadi User Center、new-api 或 Dadi Model Gateway。运行状态仍由 Conversation、Session、Run 管理，数字员工和 Skill 使用不可变版本。

平台不再试图成为第二个通用 Agent Runtime。DeepSeek Harness、Codex、Hermes 或其他 Runtime 自己负责 Agent Loop、供应商私有上下文压缩、内部 Session 状态、内部记忆格式和工具循环；平台通过 Runtime Adapter 负责企业控制面、业务上下文、策略、生命周期、事件和用量接入。

## Goals / Non-Goals

**Goals:**

- 建立独立的 `backend/` 平台后端目录，保持 `harness-engine/` 可独立使用。
- 通过 HTTP API 管理数字员工、Skill、Tool、ExecutionProfile、ModelRoute、Deployment 和运行查询。
- 让引擎选择可以按用户、租户、平台配置，默认优先级为 User > Tenant > Platform > System。
- 支持 DeepSeek Harness、Codex app-server、Codex exec、Generic CLI 和 Fake 引擎的统一生命周期。
- 通过 provider-neutral ModelRoute 支持直接模型配置，并预留 new-api、Dadi Model Gateway 适配器。
- 建立 Conversation、群聊触发规则、IM Channel Adapter 和消息投递边界。
- 建立面向业务上下文和企业记忆的 ContextManager、MemoryStore，以及负责工作区/沙箱生命周期和策略的 WorkspaceProvider、SandboxProvider 边界。
- 记录 Session 到 Run、Context、Memory、Model、Tool、Approval、Sandbox 和 Channel 的完整关联链路。
- 记录精确或估算 Token、费用、模型请求引用、工具调用和受控原始入参出参。
- 使用与 Dadi 对齐的 Fake 平台、租户、用户、角色和 Scope 数据。

**Non-Goals:**

- 本变更不直接实现生产级 Dadi User Center、new-api 或 Dadi Model Gateway 连接。
- 本变更不交付具体 IM 平台的生产凭据、Webhook 或渠道认证。
- 本变更不把 Codex 或 DeepSeek 的供应商类型泄露到领域层。
- 本变更不由平台维护供应商模型兼容性矩阵；模型兼容由模型网关或引擎适配器处理。
- 本变更不重新实现 DeepSeek、Codex、Hermes 或其他 Runtime 的 Agent Loop、上下文压缩、内部 Session、私有记忆格式、工具循环或内部沙箱。
- 本变更不把供应商私有历史转换成跨引擎可迁移的统一完整历史；平台只保存可见消息、标准化事件、Usage 和 opaque 引用。
- 本变更不实现完整的向量数据库、记忆自动学习或跨租户知识共享策略。
- 本变更不改变现有 Harness 原生历史格式，也不承诺跨引擎会话迁移。

## Decisions

### 1. Repository and dependency boundaries

新增 `backend/` 作为控制面工程，依赖 `harness-engine/` 提供执行协议和具体引擎适配器；`harness-engine/` 不反向依赖平台后端。

```text
backend/
  api/              HTTP routes and DTOs
  application/      use-case orchestration
  domain/           business entities and rules
  ports/            replaceable boundaries
  adapters/         engines, models, identity, channels
  infrastructure/  database, queue, secrets, object store
  workers/          run, delivery, recovery

harness-engine/
  core/             engine-neutral lifecycle contracts
  engines/          DeepSeek, Fake, future vendor adapters
  tools/            trusted tool registry and MCP bridge
  persistence/      existing control-plane repositories
```

选择单独后端目录，是为了让 API、权限、发布和调度代码不会进入 SDK 核心；选择模块化单体作为第一阶段形态，是为了减少跨服务分布式协议，同时保留清晰端口以便以后拆分。

备选方案是把所有功能继续放在 `harness-engine`，该方案会让 CLI/SDK、控制面和产品领域互相污染；另一方案是立即拆成多个微服务，会提前引入网络、队列、部署和一致性成本，本阶段不采用。

### 2. Domain model and immutable versions

稳定对象和运行对象分层：

```text
DigitalEmployee -> DigitalEmployeeVersion -> Deployment
Skill           -> SkillVersion
Tool            -> ToolVersion
ExecutionProfile -> ProfileBinding
ModelRoute      -> ModelProfile

Conversation -> AgentBinding -> Session -> Run
```

数字员工、Skill、Tool 和 ExecutionProfile 的已发布版本不可直接修改。Deployment 固定一个已发布版本；Run 创建时保存版本快照、策略版本和解析来源。

这种设计让草稿修改、发布、灰度和回滚不会影响正在运行的 Run，也能回答一次执行究竟使用了哪一个数字员工、Skill、Tool、引擎和模型配置。

### 3. Agent composition and Skill execution

平台中的 Agent 是一个已发布的 `DigitalEmployeeVersion`，不是单独的模型调用。它由身份、ExecutionProfile、SkillVersion、ToolVersion、上下文策略、记忆策略、工作区策略和审批策略组成。

```text
DigitalEmployeeVersion
  +-- identity and role
  +-- ExecutionProfile
  +-- SkillVersion[]
  +-- ToolVersion[]
  +-- ContextPolicy
  +-- MemoryPolicy
  +-- WorkspacePolicy
  +-- ApprovalPolicy
```

Skill 分为三类：

- `instruction`：把业务指令和输入输出契约加入 Context，由引擎完成推理。
- `tool-bundle`：把一组经过策略授权的工具作为一个业务能力暴露给本次 Run。
- `workflow`：按已发布步骤、条件和工具执行确定性流程，步骤结果再交给引擎生成解释或下一步计划。

SkillVersion 保存输入 Schema、输出 Schema、触发规则、指令内容、允许工具、步骤定义、记忆策略和审批要求。一个数字员工可以组合多个 SkillVersion，但每个已发布版本都必须固化 Skill 顺序、输入映射和输出处理方式。

一次 Agent Run 的组装流程为：

```text
Message
  |
  v
Agent Router -> Skill Resolver
  |
  v
Validate input and policy intersection
  |
  v
BusinessContextAssembler adds agent + skill instructions
  |
  v
RuntimeAdapter runs the selected Runtime and exposes normalized events
  |
  v
SkillExecution -> ModelCall / ToolCall -> RunResult
```

Skill 工具权限采用交集：

```text
Skill declared tools
  AND DigitalEmployee deployment policy
  AND Tenant policy
  AND Current user authorization
```

任一层禁止，工具不会进入本次模型请求的工具集合。每个 SkillExecution 记录 SkillVersion、输入输出引用、触发来源、步骤、工具调用、模型调用、状态、耗时和错误类别。

这种设计把业务能力从引擎中分离出来：DeepSeek、Codex 和 Fake 引擎都可以运行同一个 SkillVersion；更换引擎不会改变 Skill 的版本和权限契约。

### 4. ExecutionProfile and selection policy

`ExecutionProfile` 绑定引擎、模型路由、工作区、工具策略、审批策略、沙箱策略和上下文策略。`ExecutionProfileBinding` 绑定到 platform、tenant 或 user。

解析顺序固定为：

```text
User binding
  > Tenant binding
  > Platform binding
  > System default
```

每次 Run 只解析一次，保存 `resolved_by_scope`、profile id 和版本。用户请求不能直接覆盖引擎或模型配置；用户只能从被授权的 Profile 或 Deployment 中发起执行。

将引擎、模型、工作区和审批放在一个 Profile 中，是为了避免用户得到一个不一致组合，例如引擎可以执行代码但工作区策略禁止访问代码，或模型路由和引擎配置由两个互相独立的策略产生冲突。需要局部覆盖时通过新 Profile 版本实现。

### 5. Pluggable execution engines

平台使用统一的 `RuntimeAdapter` 端口，保留现有 `HarnessEngine` 的生命周期语义，并增加能力声明、事件转换、Usage 转换和 opaque 引用管理。

```text
DeepSeekHarnessEngine
CodexAppServerEngine
CodexExecEngine
GenericCliEngine
FakeEngine
```

DeepSeek Harness 适合通用业务 Agent 和 MCP 工具调用；Codex app-server 适合产品内的长期代码开发 Session、流式事件、审批和恢复；Codex exec 适合一次性或批处理任务；Hermes 可作为带有渠道、记忆和环境能力的专用 Runtime；Generic CLI 用于内部已有的命令行 Agent；Fake Runtime 用于离线契约测试。

CLI 适配器必须使用参数数组、受控工作目录、明确环境变量、超时、进程树终止、stdout/stderr 分离和退出码转换。平台 API 不直接执行任意宿主命令。

Runtime Adapter 不实现供应商内部 Agent Loop、上下文压缩、内部记忆或工具循环。它只负责创建/恢复/执行/取消 Runtime Session，声明能力，把供应商事件和 Usage 转换为平台事件，并保存 `runtime_session_ref`、`runtime_thread_ref`、`runtime_workspace_ref` 和 `runtime_result_ref` 等 opaque 引用。

平台不把不同 Runtime 的私有 Session 历史强行转换成统一完整历史。跨引擎切换创建新的 Runtime Session；原 Session 只保留原引擎引用和审计关联。

### 6. Model routes and gateway boundary

平台不维护供应商兼容性矩阵。`ModelRoute` 只描述模型别名、连接类型、入口地址、认证引用、作用范围和状态。

```text
DirectModelProvider
NewApiModelProvider
DadiModelGatewayProvider
```

三种实现都实现相同的 `ModelProviderPort`。引擎适配器只消费已解析的 ModelRoute 或由目标引擎生成等价配置。若引擎或网关拒绝配置，Run 返回明确的 model invocation error，并记录供应商请求引用。

模型网关返回的 usage、费用和 provider request id 由平台标准化保存。如果上游不返回精确 usage，平台可以用估算值，但必须标记 `ESTIMATED` 和估算方法。

Secret 只保存 `auth_secret_ref`，运行时由 SecretProvider 注入 Worker；API Key 不进入版本、审计、普通日志或用户可见消息。

### 7. Conversation, Session and Run

Conversation 表示用户可见的 Web、单聊、群聊或 IM 线程；Session 表示某个数字员工的上下文；Run 表示一次执行。

```text
Conversation
  +-- shared visible Messages
  +-- Participants
  +-- AgentBindings
          +-- Agent A -> Session A -> Runs
          +-- Agent B -> Session B -> Runs
```

群聊只在 `@mention`、命令或显式规则命中时创建 Run。普通消息仍然保存到 Conversation。Agent 回复携带来源和路由深度，默认不能再次触发 Agent，避免 Agent-to-Agent 循环。

IM 适配器只负责验签、规范化入站消息、发送出站消息和报告渠道能力。入站事件先幂等落库，再异步路由；出站消息有独立 Delivery 状态和重试记录。

### 8. Context management

`BusinessContextAssembler` 在每次 Runtime 调用前生成 `ContextSnapshot`，输入来源包括：

- DigitalEmployeeVersion 的系统指令。
- 当前 SkillVersion。
- Conversation 最近消息。
- Session 摘要。
- 允许范围内的记忆检索结果。
- 当前 Run 的工具结果。
- 业务系统或知识工具返回的引用。

`ContextPolicy` 限制平台注入的业务上下文、工具结果和数据引用，并记录输入 Token 预算。供应商 Runtime 自己负责内部上下文压缩、摘要和私有历史管理；平台不重复实现同一套压缩算法。ContextSnapshot 保存业务来源引用、哈希、纳入/排除记录和 Token 估算，不要求把所有正文复制到 MySQL。

平台可向 Runtime 传递 `context_policy_ref` 和业务上下文，但不能假设不同 Runtime 接受相同的内部消息格式。Runtime Adapter 负责把平台上下文转换为目标 Runtime 支持的输入。

### 9. Memory management

平台只管理企业级记忆：Conversation、User、Project、Tenant 和 Agent 作用域的业务事实、偏好、规则和引用。Runtime 私有记忆（例如引擎内部摘要、线程状态或供应商特定记忆）由 Runtime 自己管理，平台只保存 `runtime_memory_ref`（如果适配器提供）。每个企业 MemoryItem 记录作用域、来源 Run、可见性、置信度、保留策略和过期时间。

MemoryReader 只检索当前授权范围。MemoryWriter 通过显式提取策略决定是否写入企业长期记忆，不能把全部原始聊天自动转成长久记忆。记忆读写都生成 Trace Span，原始正文按数据策略保存到加密对象存储或向量存储。平台不读取或改写 Runtime 私有记忆格式。

### 10. Workspace and sandbox

`WorkspaceProvider` 负责代码、文件和项目工作区的创建、授权、租约、配额和回收；`SandboxProvider` 负责进程、资源、网络和凭据边界。Runtime 负责在已授权环境内读写文件、执行命令和使用工具，平台不重新实现 Runtime 内部的 shell、编辑器或文件操作。

执行环境分为：

```text
none                 日志查询、业务 API、知识问答
remote-tools         通过 API / MCP / A2A 查询远程系统
ephemeral-workspace  单次代码分析、修改和测试
persistent-workspace 长期开发 Session
```

代码 Agent 从指定仓库和提交创建隔离工作区，以 diff、patch、分支或产物引用作为输出。日志 Agent 默认不下载完整日志，使用范围受限的查询工具。运维 Agent 默认只读，重启、扩容、删除和配置变更通过能力代理和审批执行。平台保存 `workspace_ref`、`sandbox_ref` 和生命周期状态，不要求兼容所有 Runtime 的内部工作区格式。

### 11. Observability and usage ledger

平台把可观测性拆成五类数据：

```text
Conversation Message  用户可见内容
Trace / Span          执行过程和耗时
Audit Event           授权、审批和状态变更
Usage Ledger          Token、费用、调用次数
Raw Payload Object    受控的完整请求和响应
```

每个平台 Session 和 Run 都有 trace_id。Span 至少包含 span_id、parent_span_id、run_id、turn_id、类型、时间、状态和错误类别。Runtime 私有事件通过 Runtime Adapter 映射为标准 Span；平台不要求读取供应商内部全部事件。

模型调用保存：

- engine_ref、model_route_ref、model_alias。
- provider_request_id。
- request_ref、response_ref。
- input_tokens、output_tokens、total_tokens。
- cached_input_tokens、reasoning_tokens（若上游提供）。
- cost_amount、currency、pricing_version。
- exact 或 estimated 的计量来源。

如果 Runtime 只能提供最终 Usage 或 provider request id，平台保存可获得的标准化信息并标记缺失字段；平台不通过猜测 Runtime 内部事件来制造精确账单。

工具调用保存工具名称、版本、协议类型、输入输出引用、审批引用、耗时、状态、错误类别和重试次数。

MySQL 保存索引、状态、Token、费用、哈希和对象引用；完整 prompt、模型入参出参、工具入参出参和长文本保存到租户隔离的加密对象存储。是否保留原始 payload 由租户或项目数据策略决定。

### 12. Identity and authorization

Fake IdentityProvider 提供与 Dadi 对齐的 Platform、Tenant、User、status、roles、scopes 和 membership。领域服务只依赖 `IdentityContext` 和授权端口。

后续 Dadi 接入只替换 IdentityProvider。所有仓储、模型路由、工作区、Conversation、Session、Run、Trace 和 Usage 查询都必须接收显式 TenantContext。

### 13. API and persistence strategy

HTTP API 只负责协议转换和请求校验；业务流程进入 Application Service；Domain Service 负责规则；Repository、RuntimeAdapter、ModelProvider、MemoryStore、TraceStore 和 WorkspaceProvider 通过端口注入。

第一阶段继续使用 MySQL/InnoDB 作为控制面元数据存储，沿用现有 platform_id + tenant_id 复合边界和租约/fencing token。新领域表按以下顺序增加：身份投影、数字员工/版本、Skill/Tool 版本、ExecutionProfile/Binding、ModelRoute、Deployment、Conversation/Message、Trace/Usage 和工作区引用。

### 14. Reference-derived behavior model

本项目参考本地 StaffDeck 实例的黑盒行为，形成独立的页面和领域模型文档，详见 `docs/staffdeck-reference/`。参考内容包括员工广场、员工工作台、能力绑定、CompositionSnapshot、Session/Message/Turn/Trace 和管理 API 的可观察行为。

参考行为采用本项目自己的对象映射：

```text
StaffDeck observed concept       Project-owned concept
Digital employee card            DigitalEmployeeVersion / Deployment
Capability binding               CapabilityBinding
Composition snapshot             CompositionSnapshot
Chat session                     Session / RuntimeSessionRef
Message and turn                 Message / Turn / Run
Events and trace                 RunEvent / TraceSpan
Runtime engine                   RuntimeAdapter
```

这些页面和 API 只作为行为规格输入。实现阶段不得复制 StaffDeck 源代码、静态资源、CSS、图片或私有模块；平台前端使用自己的组件和路由，后端使用自己的领域模型和 Runtime Adapter。

### 15. Chinese documentation contract

项目文档统一放在 `docs/` 下，新增项目级文档使用中文文件名和中文正文。OpenSpec CLI 规定的 `proposal.md`、`design.md`、`tasks.md` 和 `spec.md` 保持固定英文名称，作为变更工具文件。

每个功能域至少维护以下图示：

```text
架构图       模块、端口、适配器和依赖方向
流程图       用户请求到业务结果的主要步骤
时序图       API、Worker、Runtime、Tool 和存储交互
状态图       Draft/Published、Session/Run、Delivery 等状态转换
数据模型图   领域对象、关系和租户边界
```

代码任务完成条件包含三部分：实现、自动化验证、中文文档。文档必须标明状态、更新时间、已验证行为、未知项和外部依赖，并链接到对应代码目录和 OpenSpec 任务。

### 16. Configurable object storage

媒体、用户附件、运行产物和 Runtime 工作区归档通过统一 `ObjectStore` 端口访问。第一阶段默认使用本地文件存储，部署配置可以选择 S3、MinIO、OSS 或 COS。

```text
ObjectStore
  +-- LocalObjectStore
  +-- S3ObjectStore
  +-- MinioObjectStore
  +-- OssObjectStore
  +-- CosObjectStore
```

平台使用 `StorageProfile` 描述后端：

```text
StorageProfile
- provider: local | s3 | minio | oss | cos
- endpoint
- region
- bucket
- base_prefix
- credential_ref
- encryption_policy
- signed_url_ttl_seconds
- retention_policy
```

`StorageBinding` 可以按 platform、tenant 或 project 绑定 StorageProfile，默认解析顺序为 project > tenant > platform > system default。凭据只保存 Secret 引用，不写入数据库或日志。

本地存储不提供宿主路径给客户端，而是通过一次性上传/下载接口模拟受控上传地址。外部存储使用短期预签名 URL；外部存储不可用时请求失败，不自动回退到本地存储。

对象键必须由服务端生成并包含租户边界：

```text
platform/{platform_id}/tenant/{tenant_id}/media/{asset_id}/original
platform/{platform_id}/tenant/{tenant_id}/media/{asset_id}/derivatives/{type}
platform/{platform_id}/tenant/{tenant_id}/runtime/{session_id}/{name}
```

MySQL 只保存对象引用、哈希、大小、媒体状态和处理元数据；二进制内容放入 ObjectStore。删除先撤销读取，再将对象引用加入 GC 队列，清理失败可重试。

## Runtime Flow

```text
HTTP / IM / API request
        |
        v
IdentityProvider -> TenantContext
        |
        v
Authorization + Idempotency
        |
        v
Conversation / Message persistence
        |
        v
Agent Router
        |
        v
Deployment -> DigitalEmployeeVersion
        |
        v
PolicyResolver -> ExecutionProfile
        |
        v
BusinessContextAssembler -> EnterpriseMemoryReader -> ContextSnapshot
        |
        v
Scheduler -> WorkspaceProvider -> SandboxProvider
        |
        v
RuntimeAdapter
        |
        +--> DeepSeek / Codex / Hermes Runtime
        +--> Runtime-owned context compaction
        +--> Runtime-owned session state
        +--> Tool Connector through platform policy
        +--> EnterpriseMemoryWriter
        |
        v
Trace + Usage + Audit + Message + Delivery
```

## Risks / Trade-offs

- **[Risk] 原始 prompt、工具参数和模型响应包含业务敏感数据。** → 采用租户/项目级 payload policy、加密对象存储、RBAC、哈希引用和可配置保留期；普通 Trace 只保存元数据。
- **[Risk] 上游网关不返回准确 Token 或费用。** → 保存 `ESTIMATED` 状态和估算方法；优先约定 new-api/Dadi Gateway 的标准 usage 响应。
- **[Risk] 多引擎生命周期能力不同。** → 使用 capability declaration；不支持恢复、取消或流式时明确返回能力错误。
- **[Risk] 多个 Agent 在群聊中互相触发。** → 默认只响应 @提及、命令和显式规则；消息记录来源和路由深度并设置循环保护。
- **[Risk] 持久工作区包含代码、凭据或未提交变更。** → 工作区按租户/项目隔离，使用租约、SecretProvider、沙箱和审计，默认以 diff 或产物交付。
- **[Risk] 将控制面和执行面放在同一进程扩大故障范围。** → API、Worker 和 SandboxProvider 分层，执行进程不直接拥有控制面数据库权限。
- **[Risk] 直接配置、new-api 和 Dadi Gateway 的入口协议不同。** → 平台固定 ModelProviderPort，只让适配器处理入口差异。
- **[Risk] 过早拆分微服务增加一致性复杂度。** → 第一阶段采用模块化单体和独立 Worker，保留端口以便按吞吐和隔离需求拆分。

## Migration Plan

1. 创建独立 `backend/` 工程和模块目录，不移动现有 `harness-engine/`。
2. 增加 Fake IdentityProvider、基础 Platform/Tenant/User 数据和 HTTP 身份上下文。
3. 增加数字员工、Skill、Tool、ExecutionProfile、ModelRoute 和 Deployment 的领域仓储。
4. 将现有 `harness-engine` 注册为 DeepSeek 和 Fake 引擎适配器；增加 Codex 和 Generic CLI 适配器的端口与 Fake 契约实现。
5. 增加 Conversation、Message、群聊路由、Session/Run 绑定和 Fake Channel。
6. 增加 BusinessContextAssembler、企业 MemoryStore、WorkspaceProvider 和 SandboxProvider 的最小实现，不实现 Runtime 内部压缩、私有记忆或文件操作。
7. 增加 RuntimeAdapter、opaque Runtime 引用、Trace、Usage、ModelCall、ToolCall 和原始 payload 引用表/对象存储接口。
8. 通过 Fake Runtime、Fake ModelProvider、Fake Channel 和 Fake IdentityProvider 验证端到端控制面流程，并验证 Runtime 私有状态只通过引用进入平台。
9. 逐步接入真实 DeepSeek、Codex、new-api、Dadi User Center、IM 和生产 Sandbox；每个适配器都保留独立能力和验证记录。

回滚策略：新后端通过独立启动入口和独立数据库迁移启用；如果平台控制面不可用，现有 `harness-engine` SDK/CLI 不受影响。单个引擎、模型、渠道或身份适配器可通过配置禁用，不删除已发布版本和运行审计。

## Open Questions

- HTTP 框架、异步队列和生产对象存储的具体实现可以在任务拆解阶段确定，不改变本设计的端口和行为规格。
- Trace 查询 API 的分页和导出格式可以在平台 API 实现阶段确定。
- 记忆的向量检索后端可以在 `MemoryStore` 契约稳定后选择，不影响 Session/Run 和记忆作用域模型。

## Runtime Boundary References

- [DeepSeek Harness Python SDK](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/python-sdk.md)：SDK 负责 Runtime Session、自动上下文压缩和 Harness home/session 持久化。
- [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform)：应用负责产品界面、业务上下文、工具和审批，Codex Harness 负责 Agent Loop。
- [Codex Agent architecture](https://developers.openai.com/api/docs/guides/agents-api/architecture)：Harness、Environment 和 Application Server 是独立边界。
