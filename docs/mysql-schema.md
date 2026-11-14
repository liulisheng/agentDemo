# MySQL 持久化与租户边界

状态：控制面核心表由 `docs/migrations/V001__core_schema.sql` 定义；对话消息索引、流分段、上传 intent 和内容清理队列由 `docs/migrations/V002__conversation_messages.sql` 定义。V001+V002、只读 doctor 和非流式 MySQL 消息仓储已于 2026-11-13 在一次性 MySQL 8.4.13 中验证。流式 chunk 仓储/恢复、GC worker、生产对象存储/KMS 尚未交付。日期：2026-11-13。

## 数据库约定

- 目标为 MySQL 8.4、InnoDB、`utf8mb4_0900_ai_ci`。
- 平台级表以 `platform_id` 为键；租户级表使用 `(platform_id, tenant_id)` 复合键和外键。
- 用户消息、助手可见回复、工作区正文、凭据和完整模型结果正文不写入控制面数据库。消息正文及 Harness 原生历史保存在租户隔离的加密持久化存储；MySQL 保存消息索引、不透明引用、哈希和大小。`request_digest` 只用于幂等核验，不能还原 prompt。
- 消息正文和会话级审计元数据默认长期保留，直到用户或管理员显式删除；不按时间自动过期。数据库和对象存储备份按部署的备份生命周期清除。
- 用户密码、OIDC Provider、浏览器登录 session 和令牌签发由用户中心负责；V001 中的 `users` 是认证身份目录投影。
- 所有租户数据访问都从显式 `TenantContext` 取得平台和租户范围，跨租户资源按不存在处理。

## Schema 版本

`schema_version` 保存 canonical migration 已应用的版本。V001 插入版本 `1`，V002 插入版本 `2`；应用和 doctor 要求版本至少为 2，只读取版本，不自动修改 schema。部署 migration 应由独立迁移流程依序执行。一次性 MySQL 8.4.13 执行及代表性 V002 集成检查已通过；生产迁移流程仍需单独验证。

## 表结构

### `platforms`

平台主键 `platform_id`，另有唯一 `code`、`name`、`status` 及创建/更新时间。`status` 仅允许 `ACTIVE`、`SUSPENDED`、`CLOSED`。

### `tenants`

复合主键 `(platform_id, tenant_id)`，平台内 `code` 唯一，并通过外键归属平台。状态仅允许 `ACTIVE`、`SUSPENDED`、`CLOSED`。

### `users`

复合主键 `(platform_id, tenant_id, user_id)`，记录 `external_subject`、`username`、`display_name`、`status` 和创建/更新时间。主体在平台内唯一，用户名在租户内唯一。状态仅允许 `PENDING`、`ACTIVE`、`LOCKED`、`DISABLED`。V001 不存邮箱、手机号、密码或登录事件。

### `sessions`

复合主键 `(platform_id, tenant_id, session_id)`，`session_id` 是控制面 ID。`engine_name` 与 `engine_session_id` 保存原生引擎会话定位，后者在租户和引擎范围内唯一。`harness_home_ref`、`workspace_ref` 是外部隔离存储引用；`metadata_json` 只放非敏感引擎元数据；`last_run_at` 记录最近终态时间。V002 增加 `last_message_sequence`，由事务内原子递增，为会话消息分配并发安全的顺序号。状态为 `ACTIVE`、`PAUSED`、`INTERRUPTED`、`CLOSED`。

索引支持按租户更新时间分页以及按状态筛选。

### `runs`

复合主键 `(platform_id, tenant_id, run_id)`，并以同样的租户范围外键关联 session。`idempotency_key` 在租户内唯一；`request_digest` 和 `request_digest_version` 保存 canonical JSON 的 SHA-256 摘要，不存 prompt。状态包括 `QUEUED`、`STARTING`、`RUNNING`、`CANCELLING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`UNKNOWN`。

`worker_id`、`fencing_token`、`lease_expires_at` 支持 worker 租约；`started_at`、`finished_at`、`error_code` 和 `result_ref` 记录运行结果元数据。`result_ref` 是外部对象引用，结果正文不写入数据库。

### `messages`（V002）

复合主键 `(platform_id, tenant_id, message_id)`，同租户外键关联 session 和可选 run。`sequence_no` 在会话内唯一递增；服务端在事务内原子递增 session 的 `last_message_sequence` 后使用新值。`role` 为 `USER` 或 `ASSISTANT`；`status` 为 `PENDING`、`STREAMING`、`COMPLETE`、`PARTIAL` 或 `FAILED`。CHECK 要求 user 消息为 `COMPLETE`，且 `COMPLETE`/`PARTIAL` 消息必须有完整内容引用、SHA-256、字节数和类型。`last_committed_chunk_sequence` 标识从 1 开始连续提交的 chunk 前缀。表只保存引用、哈希、字节数、类型、chunk 序号和时间，不保存消息正文。`runs.result_ref` 与对应助手消息共享最终内容引用。

用户消息在 run 入队前写成 `USER/COMPLETE`。非流式助手回复完成后写成 `ASSISTANT/COMPLETE`。流式助手消息从 `PENDING` 进入 `STREAMING`，正常结束为 `COMPLETE`；执行进程退出时，最后可读的已提交输出标成 `PARTIAL`，run 按证据标记 `UNKNOWN`，不能自动重发模型请求。

### `message_chunks`（V002）

主键 `(platform_id, tenant_id, message_id, chunk_sequence)`，外键关联同租户消息。每行保存不可变分段的对象引用、SHA-256、字节数和创建时间，不保存 delta 文本。分段按到达顺序编号；默认每达到 32 KiB 或 2 秒（先到者）写一个分段。chunk 插入与 `messages.last_committed_chunk_sequence` 推进同事务进行，并且只允许写入前一序号加一，因此已提交分段是无缺口的连续前缀。重连使用同一 MySQL chunk log 做 cursor 查询/long-poll；通知只负责唤醒查询，不能作为数据源。

### `content_upload_intents`（V002）

对象写入前先创建租户范围 upload intent，预留 ASCII 对象引用并设置 `lease_expires_at`。状态为 `UPLOADING`、`PUBLISHED`、`EXPIRED`；发布 message/chunk/session 引用时必须在同事务锁定并校验有效 intent。intent 保存可选的 `session_id`、`message_id`、`run_id` 作为可信服务端关联，但不对此建外键，使 session 删除时可以找到并排队对象，又不会阻塞源行删除。清理器也先锁定 intent，过期时转为 `EXPIRED` 并写入 `content_gc_queue`；intent 发布和清理因锁竞争不能同时成功。

### `content_gc_queue`（V002）

租户范围内唯一的对象清理队列，保存不透明 `object_ref`、对象类型、`QUEUED`/`DELETING`/`RETRY`/`DELETED` 状态、尝试次数、下次重试时间和稳定错误码。该队列表只外键关联租户，不外键关联 session/message，使 session 元数据删除后仍可完成对象清理。只有 `QUEUED`、`DELETING`、`RETRY` 任务暂时保护对象；`DELETED` 不再是孤儿扫描根。若分页扫描发现具有 `DELETED` 任务的对象重新出现，清理器复用该唯一行将状态改回 `QUEUED` 并再次删除。删除事务先把正文、分段、manifest、Harness home、workspace 和所有关联 upload intent 的对象引用（包括仍在 `UPLOADING` 的引用）写入清理队列，并将这些 intent 标为 `EXPIRED`，再删除会话消息、运行、会话级审计和 session 行；事务提交后清理器幂等删除对象并记录终态。

### `audit_events`

复合主键 `(platform_id, tenant_id, event_id)`，显式记录 `actor_type`、可选 `actor_user_id`、session/run 引用、`event_type`、`policy_version`、资源引用、`fencing_token`、`payload_json` 和创建时间。actor 类型仅允许 `USER`、`WORKER`、`SYSTEM`。worker/system 不伪造用户 ID；payload 只接受受限的扁平元数据字段。

## 仓储与授权

所有租户数据仓储操作显式接收 `TenantContext`，SQL 使用参数化绑定并带 `platform_id`、`tenant_id` 条件。创建方法也单独接收租户上下文，并校验它与记录内的租户范围一致。仓储不公开通用 SQL 执行入口。

owner/admin 可修改会话或请求取消；worker 通过 `auth_source="worker"` 和 fencing token执行 run 状态变更；system 仅用于租约过期回收。普通 member 不能修改或取消。非 `ACTIVE` 会话不能接受或执行新的 run。

创建 run 时使用租户范围内的幂等键和 canonical 摘要；相同摘要返回原记录，不同摘要冲突。领取使用 `FOR UPDATE SKIP LOCKED`，状态更新和审计在同一事务内；过期租约标记为 `UNKNOWN` 并递增 fencing token，不自动重放。

审计 payload 使用扁平字段白名单，拒绝嵌套内容、未知键和敏感键；不得保存 prompt、凭据、工具参数、原始工具输出、完整模型结果或模型内部推理。允许记录工具名称、调用状态、审批结果、运行状态和分段 checkpoint 序号等可核验元数据，不复制 delta 文本。

### 对话正文对象

对象存储由部署提供可替换的 adapter；内容引用是服务端生成的 ASCII 安全、区分大小写的不透明 ID，不是公开 URL。对象使用 TLS 传输和部署密钥系统管理的静态加密。服务端读取对象前按认证得到的 `TenantContext` 校验 platform、tenant、session 和 message 归属；不得相信请求体给出的 tenant 或对象键。

非流式消息正文保存为版本化 UTF-8 对象。流式消息的分段对象不可变，完成时写最终 manifest；读取历史时校验分段顺序、长度和哈希。对象上传先于 MySQL 引用提交，并且每次上传都有先建后发的 intent。tenant-scoped 孤儿扫描默认回收 24 小时前上传且不被 messages、chunks、sessions、`runs.result_ref`、租约有效的 `UPLOADING` intent 或活动清理任务（`QUEUED`/`DELETING`/`RETRY`）引用的对象，部署可配置更长宽限期。过期 intent 与 `DELETED` 任务不永久屏蔽对象；扫描发现对象重新出现时必须重新入队。对象存储与 MySQL 不使用分布式事务。

显式删除后，应用立即停止读取消息。MySQL 在同一事务中加入待删对象引用并删除会话级消息、chunk、run、audit 和 session 元数据；对象清理失败可重试。已进入备份的副本按部署备份生命周期到期清除。
