# 对话正文、流式输出与执行轨迹持久化设计

## 意图

为 Harness 工程补齐应用可读取的会话历史，使用户输入和助手最终回复在进程退出、worker 更换和会话恢复后仍可查询。流式和非流式调用使用相同的消息记录模型；流式增量只在需要恢复未完成输出时分批持久化。

项目已经选择 MySQL/InnoDB 保存控制面元数据。本文选用 MySQL 消息索引加租户隔离的加密对象存储保存正文，继续把 Harness 原生会话文件作为 SDK 恢复所需的独立数据。三者职责不同，不能相互替代。

已确认的产品选择：采用方案 B；断线或进程重启后要恢复正在生成的回答；消息正文和会话级审计元数据默认长期保留，直到显式删除。

## 当前状态

- `sessions`、`runs` 和 `audit_events` 保存租户范围内的会话、运行及审计元数据；`runs.request_digest` 不可还原 prompt，`runs.result_ref` 是可选外部引用。
- V001 不含消息表。`DeepSeekHarnessEngine.run()` 返回 `EngineResult.text`，当前 `PersistentSessionService` 没有将用户输入或助手正文写入消息存储。
- 设计初稿时 DeepSeek 适配器声明 `streaming=True`，但只在 SDK `run()` 返回后转发已结算事件。Task 4 实施后，适配器已改为 `streaming=False`；通用服务仅接受运行中的 engine-neutral 可见文本 delta，并持久化为消息块。DeepSeek 固定 SDK 的实时 delta 仍未验证。
- 审计 payload 采用受限字段白名单。项目明确不把原始 prompt、凭据、完整工具参数或完整模型结果写入控制面审计表。

因此，当前持久化演示证明了运行状态和幂等提交可保存，不代表应用层聊天记录已经保存。Harness home 中的原生会话历史也不能替代统一的消息读取接口。

## 目标

1. 保存每轮用户输入和助手对用户可见的最终回复，并允许按当前 `TenantContext` 分页读取。
2. 流式和非流式调用写出同一类规范化消息；流式消息能标识生成中、完成、部分完成或失败。
3. 在需要跨进程恢复未完成流时，能够读取已持久化的有序文本分段。
4. 保留足够的工具、审批和运行状态轨迹来复查执行过程，不把这些轨迹误当成模型内部思维链。
5. 维持现有租户授权、幂等、fencing token 和无副作用自动重放规则。
6. 允许按租户策略读取、删除和设置保留期；正文不通过公开对象 URL 暴露。

## 非目标

- 不将模型原始内部推理文本、隐藏 reasoning token 或 chain-of-thought 写入数据库、对象存储、普通日志或用户会话历史。
- 不为逐 token 查询建立 MySQL 大字段日志；只有启用流恢复时才按批次写不可变内容分段。
- 不把完整工具参数、工具原始输出、凭据或模型 prompt 复制到 `audit_events`。
- 不替换 Harness SDK 的原生会话恢复数据，也不把原生会话格式作为应用对话历史 API。
- 不在本设计中锁定 S3、MinIO 或云厂商；对象存储由可替换的存储适配器接入。

## 方案比较

### A：所有消息正文直接放进 MySQL

可以通过 SQL 简单查询全文，组件较少；会使控制面数据库承载大量敏感正文、备份和索引成本，也增加正文访问的影响范围。

### B：MySQL 保存消息索引，正文保存到加密对象存储（选定）

MySQL 保存租户、会话、运行、消息顺序、状态和不透明内容引用；对象存储保存正文和可选的流式分段。该方案沿用现有“控制面元数据与隔离持久内容分开”的边界，也支持换存储后端。代价是必须实现对象存储适配器、跨存储一致性补偿和垃圾回收。

### C：只依赖 Harness home 中的原生历史

减少重复内容，但读取和恢复会绑定 SDK 格式；应用无法稳定地分页展示消息、统一不同引擎或单独执行消息保留策略。因此不作为应用层会话记录方案。

## 选定设计

### 1. 数据归属

| 数据 | 保存位置 | 说明 |
| --- | --- | --- |
| 平台、租户、会话、run 状态、幂等摘要、fencing token | MySQL | 继续使用现有仓储和租户范围条件 |
| 消息角色、顺序、状态、时间、正文引用、正文哈希和大小 | MySQL `messages` | 新增应用可查询的消息索引 |
| 用户输入、助手最终可见回复、可选的部分输出分段 | 加密对象存储 | 每条消息或分段使用不可猜测的内部对象键 |
| 上传前 reservation、lease 与发布状态 | MySQL `content_upload_intents` | 先预留后写对象，保留可信 session/message/run 归属以支持删除，防止延迟上传在清理后发布失效引用 |
| Harness SDK 原生历史及 workspace | 独立隔离卷/对象存储 | 只用于同一引擎原生恢复，不作为 UI transcript |
| 工具调用、审批和 run 状态变更的审计元数据 | MySQL `audit_events` | 仅记录白名单字段、状态和引用 |
| 原始内部推理 | 不持久化 | 不转发到用户 transcript 或审计 payload |

对象引用是服务端生成的 ASCII 安全、区分大小写且不可猜测的后端内部标识，不是公开 URL。服务端读取正文前必须从可信认证上下文校验 `platform_id`、`tenant_id`、`session_id` 和消息归属；不能接受客户端自报的租户或对象键。

### 2. 消息索引

新增 V002 migration 与 `messages`、`message_chunks`、`content_upload_intents`、`content_gc_queue` 表，并给 `sessions` 增加消息序号列；V001 保持不可变。建议字段如下：

- 主键：`(platform_id, tenant_id, message_id)`。
- 外键：同租户范围关联 `sessions`；`run_id` 可空，用于不属于模型 run 的系统消息。
- `sequence_no`：会话内严格递增；唯一范围为 `(platform_id, tenant_id, session_id, sequence_no)`。
- `role`：`USER` 或 `ASSISTANT`。工具操作进入审计轨迹，不把工具原始响应自动伪装成对话消息。
- `status`：`PENDING`、`STREAMING`、`COMPLETE`、`PARTIAL`、`FAILED`。
- `content_ref`：ASCII opaque object key，对象存储中的正文或最终 manifest 引用；对象不完整时为空。
- `last_committed_chunk_sequence`：当前已提交连续分段前缀的最大序号，初始为 `0`；插入 chunk 时与分段行同事务推进。
- `content_sha256`、`byte_length`、`content_type`：用于完整性校验和读取，不存正文。
- `created_at`、`updated_at`、`completed_at`：支持稳定分页和恢复展示。

`sessions.last_message_sequence` 由 V002 新增并默认为 `0`。服务端在与消息插入相同的 MySQL 事务内原子递增它，再读取新值作为 `sequence_no`；并发请求因此由 session 行锁串行分配，不依赖时间戳或旧事务快照。`message_chunks` 以 `(platform_id, tenant_id, message_id, chunk_sequence)` 为主键，外键关联同租户消息，并保存 `content_ref`、SHA-256、字节数和创建时间。`messages.last_committed_chunk_sequence` 只允许按 `n + 1` 推进并与 chunk 插入同事务提交，因此所有可读 checkpoint 从 1 起构成连续前缀。`content_upload_intents` 在外部对象上传前预留对象引用和 lease，状态为 `UPLOADING`、`PUBLISHED` 或 `EXPIRED`；还保存可信服务端提供的可选 `session_id`、`message_id`、`run_id`，但不对这些源 ID 建 FK，以便 session 删除时找到并清理其上传对象。引用发布事务与过期清理事务锁定同一 intent，过期 intent 不能再发布。`content_gc_queue` 以租户范围内的不透明对象引用唯一，保存待删对象、重试次数、下次重试时间和稳定错误码；它只引用 tenant，不引用 session/message，保证源记录删除后仍能继续清理对象。

消息正文对象使用版本化的 UTF-8 JSON 或纯文本封装，至少携带 `message_id`、`role`、`content` 和格式版本。是否存储额外模型元数据按白名单决定；不将完整 SDK response 原样序列化。

`runs.result_ref` 指向同一 run 的最终助手消息正文引用，避免再复制一份完整结果。`request_digest` 继续用于请求幂等核验，不能代替用户消息记录。

### 3. 非流式写入

1. 服务端分配 `message_id` 和 `run_id`，先在 MySQL 创建带 session/message/run 归属和有效租期的 `content_upload_intents` 行取得对象引用，再加密写入用户输入对象。
2. 在 MySQL 事务中锁定 intent，确认状态仍为 `UPLOADING` 且 lease 未过期，然后写入 `USER/COMPLETE` 消息索引和对应 queued run，并把 intent 设为 `PUBLISHED`；幂等重试返回既有 run 与消息，不重复插入。
3. 模型完成后，先创建助手正文的上传 intent，再将最终可见文本加密写入对象存储。
4. 在一个 MySQL 事务中锁定 intent 并校验 lease，写入 `ASSISTANT/COMPLETE` 消息索引、设置 run 终态并更新 `result_ref`，再把 intent 设为 `PUBLISHED`；这些数据库状态和审计事件同事务提交。
5. 只有正文对象和数据库引用均成功后，才把回复报告为已持久化。

跨存储没有分布式事务。每个上传在对象写入前都先创建持久 intent；对象写成功而 MySQL 发布失败时，intent 保持 `UPLOADING`，过期后由清理器锁定、标为 `EXPIRED` 并写入清理队列。对象引用发布也必须锁定 intent 并验证 lease，所以不会有过期清理与延迟发布同时成功的竞态。ContentStore 还提供 tenant-scoped、可分页的旧对象枚举来发现历史/外部对象；孤儿扫描器只检查超过 24 小时宽限期的对象，并保护 messages、chunks、sessions、`runs.result_ref`、lease 有效的 `UPLOADING` intent，以及状态为 `QUEUED`、`DELETING` 或 `RETRY` 的清理任务。`EXPIRED` intent 和 `DELETED` 清理任务不再视为对象根；若扫描发现一个已有 `DELETED` 任务的对象重新出现，必须把该任务重新置为 `QUEUED` 并再次删除。这样即使过期 PUT 在首次删除完成后才落地，也会在对象达到孤儿宽限期后再次被回收。24 小时是本项目默认值，可由部署设置更长值。

### 4. 流式写入与恢复

流式和非流式都以一条用户消息及一条规范化助手消息呈现。流式响应中的可见文本 delta 按到达顺序合并；默认每达到 32 KiB 或 2 秒（先到者）写入一个不可变分段对象，并在 MySQL 记录该分段的 `chunk_sequence`、哈希、大小和引用。`message_chunks` 只保存分段元数据，不保存文本正文。

助手消息在首个 delta 到达前为 `PENDING`，开始生成后为 `STREAMING`。每个分段先创建上传 intent，再写对象；随后一个事务锁定 intent、校验 fencing token 和 lease、插入 chunk、将 `chunk_sequence` 从 `last_committed_chunk_sequence + 1` 推进，并把 intent 改为 `PUBLISHED`。这些写入同事务提交或回滚，已提交 checkpoint 始终从 1 起构成连续前缀。进程重启后，按序重建该前缀；不得假设未提交 delta 已持久化。

恢复通过 cursor-based durable tail 完成，不采用“先回放一次、再切换到另一个 volatile live subscription”的流程：

- 客户端带 `last_chunk_sequence` 重连，服务端只从 MySQL 按序读取 `chunk_sequence > last_chunk_sequence` 的已提交分段，并持续查询/long-poll 同一 durable chunk log，直到 run 终态。通知或进程内事件只用来唤醒等待，不能作为数据源；收到通知或等待超时后都重新查 MySQL。客户端 cursor 大于 `last_committed_chunk_sequence` 时拒绝请求；读到的 chunk 必须是 cursor 后连续序号，否则返回明确的 gap 错误。
- worker/执行进程退出：恢复已持久化到最后 checkpoint 的部分文本并标注 `PARTIAL`/`UNKNOWN`。除非上游 SDK 明确提供同一 run 的原生续流句柄并经过验证，不自动重发模型请求，也不承诺从模型生成位置继续。这样恢复的是已确认输出和用户可见进度，避免重复有副作用的工具调用。

模型正常完成时：

1. 确保持久化最后一个分段。
2. 创建包含有序分段引用、最终长度和哈希的不可变 manifest。
3. 将消息更新为 `COMPLETE`，`content_ref` 指向 manifest，并将 run 更新为 `SUCCEEDED`。

取消、崩溃或超时时：

- 已存在可见内容则消息标为 `PARTIAL`，manifest 指向最后一个已提交分段；否则保留 `FAILED` 或无正文的失败消息状态。
- run 根据实际证据进入 `CANCELLED`、`FAILED` 或 `UNKNOWN`，未知执行结果仍不得自动重放。
- 前端应明确显示部分内容及终态，不能把 partial 当成完整回答。

如果后端没有真实增量事件能力，则不能仅因 `EngineCapabilities.streaming=True` 就声称实现了持久化流式；适配器必须把真正按序到达的用户可见 delta 交给 service 层。SDK 回调的原始 vendor 对象不得直接存入数据库。

### 5. 执行轨迹与推理内容

记录可验证的动作，而不是生成模型的内部思维文本。`audit_events` 可以记录：

- run/session 状态迁移、时间、actor、policy version、fencing token。
- 工具名称、调用状态、开始/结束时间、稳定错误码、脱敏后的资源引用。
- 审批请求和决策类型、审批 actor、结果状态。
- 流式消息的开始、checkpoint 序号、完成或部分终止状态；不在 audit payload 重复 delta 文本。

`audit_events.payload_json` 继续使用扁平白名单。默认不写工具参数、原始工具输出、prompt、凭据、隐藏 reasoning 或完整模型结果。若产品要给用户展示“为什么这样回答”，将其作为普通的可见 `ASSISTANT` 消息单独持久化，并标注其为解释文本；不能称其为模型内部推理记录。

### 6. 加密、授权、保留与删除

- 对象存储传输使用 TLS，静态加密使用接入方 KMS/密钥管理方案的信封加密；密钥策略和轮换由部署配置提供。
- 旧对象枚举必须接收 `TenantContext`，仅扫描对应租户的服务端命名空间；不得接受请求体指定的桶、前缀或对象路径。
- 对象键从服务端生成并纳入租户/会话作用域；租户不能指定桶、路径或挂载参数。
- 消息列表和内容读取仓储都强制接收 `TenantContext`；跨租户读取按资源不存在处理。
- session 关闭不立即删除历史。显式删除 session 时，在一个 MySQL 事务内把消息正文、分段、manifest、`runs.result_ref`、Harness home、workspace，以及所有关联上传 intent（包括 `UPLOADING`）的对象引用写入 `content_gc_queue`，将这些 intent 标为 `EXPIRED`，删除消息、chunks、会话级审计、runs 与 session 元数据，并立即让应用查询返回不存在；事务提交后由清理器异步删除对象，失败可重试且可审计。若对象 PUT 在清理任务进入 `DELETED` 后才完成，孤儿扫描必须识别其重新出现并重新入队。
- 正文和会话级审计元数据默认不按时间自动过期；保留到用户/管理员显式删除会话或数据。删除时清理正文、分段、manifest、消息索引和该会话级审计记录。数据库及对象存储备份按部署的备份生命周期到期清理。
- 删除事务提交后立即撤销应用读取授权；对象清理由 `content_gc_queue` 重试并提供可查询状态。已进入备份的副本按备份生命周期清除，不能通过普通历史查询读取。
- 下载和导出还要执行租户校验、大小限制和路径/符号链接安全检查。

### 7. 当前项目改动边界

本设计后续实现时预计涉及：

- `docs/migrations/V002__conversation_messages.sql`、`docs/mysql-schema.md`：新增消息及流分段元数据 schema。
- `harness_engine.doctor`：识别已应用的 schema version 2 和 V002 文件；继续只读检查，不自动修改数据库。
- `harness_engine.persistence.models`、`protocols`、`mysql`：消息/分段/上传 intent 记录及租户范围查询。
- 新的 content-store protocol 和配置驱动适配器：租户作用域的预留后加密对象 put/get/delete、分页枚举、幂等键和孤儿清理。
- `session_service`：在提交和执行生命周期中协调消息、run 与内容引用。
- `HarnessEngine` 流式事件契约及 DeepSeek adapter：提供实时、有序、可区分可见文本和内部事件的增量。
- README、实施计划、真实验证矩阵和 runbook：区分已经实现、待实现与真实环境验证范围。

对象存储实现先提供可测试的本地开发后端和可替换 protocol；生产后端由接入方选择。上传 lease 必须能在对象写入过程中续期；清理器只过期已到期的 `UPLOADING` intent。启用 24 小时旧对象枚举回收前，必须先排空所有尚未创建 upload intent 的旧版 writer；否则仍在运行的旧 writer 可能写入一个尚无数据库引用的对象。任何对象存储错误都不能使 worker 回退到宿主机执行或跳过租户授权。

## 验收标准

1. 用户输入在 run 提交前有可读取的租户范围消息记录；同幂等请求不产生重复消息。
2. 非流式回复成功后，跨进程可读取原始完整用户消息和助手回复；MySQL 中只有索引与正文引用。
3. 流式响应被分成有序、可校验分段；完成后读取为与原始可见输出一致的一条助手消息。
4. 中断流只显示最后一个已提交 checkpoint，并标明 `PARTIAL`；客户端重试不会自动重跑未知副作用请求。
5. 对象写入成功而 MySQL 事务失败时不产生可见脏引用；上传 intent 与清理器互斥 fencing 延迟发布，孤儿对象最终被清理。
6. 重连查询与 chunk commit/通知并发时仍可从同一 durable log 读到所有连续序号；消息 chunk 只能提交为从 1 起连续前缀。
7. 孤儿扫描把遗留 `runs.result_ref` 作为有效根引用，不能误删 P4 已保存的结果对象。
8. 过期或已删除的 intent/清理状态不永久屏蔽对象；在首次清理后才完成的延迟 PUT 会被孤儿扫描重新入队并最终删除。
9. 跨租户消息/分段读取、删除和导出均被拒绝；fencing 旧 worker 不能写入 checkpoint 或结束消息。
10. 审计记录可说明发生了什么工具/审批/状态动作，但不含 prompt、隐藏推理、凭据或完整工具参数。
11. 保留策略、会话删除和对象清理可重试并留下脱敏审计记录。
12. DeepSeek 适配器只有在经验证能提供真正增量事件后才声明流式存储链路通过；否则报告未验证。

## 尚待确认

- 生产对象存储后端和 KMS 的选择；接口保持 vendor-neutral。
