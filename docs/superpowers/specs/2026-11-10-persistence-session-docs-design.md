# P4 持久化、会话与文档交付设计

## 意图

在现有生命周期协议、工具插件和冻结的 MySQL schema 之上，补齐控制面持久化边界。目标是让会话、运行、租约和幂等状态可以在进程退出、重复请求和 worker 切换后被安全查询和恢复，同时让文档、doctor 检查和验证报告与实际代码保持一致。

## 当前状态

- `TenantContext`、`SessionRef`、`EngineRequest`、`EngineResult` 已存在于 engine-neutral core。
- `docs/migrations/V001__core_schema.sql` 定为唯一 canonical schema，已补齐请求摘要、运行时间戳、错误分类、对象引用和审计 actor 字段。
- 已交付 Python 仓储、MySQL 驱动可选依赖、会话协调服务和只读 doctor；Docker MySQL 8.4.13 集成验证通过。
- 离线测试继续使用 DB-API 测试替身；真实数据库结果单独记录，不能用 SQLite 替代。

本阶段把 `V001__core_schema.sql` 定为唯一 canonical schema；`mysql-schema.md` 和本设计文档必须与 migration 同步。由于 migration 尚未用于生产，P4 可以在 V001 上补齐缺少的控制面字段。

## 目标

1. 提供显式 `TenantContext` 参数的 session/run/audit 仓储接口，不允许从请求体或全局变量推导租户。
2. 用参数化原生 MySQL SQL 实现 session 创建、列表、状态更新、run 幂等创建、领取、续租、完成和 unknown 标记。
3. 保证跨租户读取和更新被拒绝；旧 fencing token 的 heartbeat、finish 和审计写入不生效。
4. 把 engine-neutral `SessionRef` 与持久化记录互相转换；恢复只使用已存在的原生 engine session，不偷偷创建新会话。
5. 提供 doctor 命令检查 schema、连接和关键能力，并输出通过、跳过、未验证的结果。
6. 更新中文 README、配置示例、工具/自定义引擎教程、迁移说明和真实验证矩阵。

## 非目标

- 不实现用户中心、密码、OIDC Provider 或 token 签发。
- 不把完整 prompt、工具参数、Harness 历史或工作区正文写进 MySQL；这些内容只保存为受租户/会话隔离的对象引用。
- 不实现 Docker/Kubernetes 调度器；P4 只提供持久化接口和本地 MySQL 验证材料。
- 不增加 SQLite 兼容层，也不提供绕过租户边界的通用 `execute(sql)` 接口。

## 方案比较

### A：合约优先 + 原生 MySQL SQL（选定）

先定义仓储协议和 engine-neutral 记录类型，再用 `mysql-connector-python` 连接池和参数化 SQL 实现。离线测试使用受控的 DB-API 测试替身验证 SQL 调用、租户参数、事务和影响行数；真实 MySQL 8.4 测试单独运行。这个方案让租户条件、锁定读和 fencing token 直接可审查，符合已冻结的 schema。

### B：SQLAlchemy Core

连接池和事务边界更方便，但会引入额外表达式层；对本项目已冻结且租户条件必须显式出现的 SQL，收益不足，暂不采用。

### C：只以真实 MySQL 为主

能较早暴露数据库差异，但会让离线开发依赖 Docker/网络，无法稳定覆盖状态机和授权失败路径，暂不采用。

## 选定设计

### 模块边界

- `harness_engine.persistence.models`：不可变的 `SessionRecord`、`RunRecord`、`Lease`、`Page` 和状态枚举。
- `harness_engine.persistence.protocols`：`SessionRepository`、`RunRepository`、`AuditRepository` 和 `DatabaseHealth` 协议。
- `harness_engine.persistence.mysql`：连接池、事务、参数化 SQL 和 MySQL 行映射；所有公开方法第一个业务参数是 `TenantContext`。
- `harness_engine.session_service`：把 engine 生命周期与仓储协调起来，负责新建/恢复会话、幂等 run 请求和状态映射；不暴露 MySQL 类型给引擎适配器。
- `harness_engine.doctor`：只读检查包导入、配置、schema 版本和可选真实数据库连接，默认不发起模型请求。

### 会话规则

- `create_session` 先生成控制面 `session_id`，再调用引擎创建原生 session，随后持久化 `engine_session_id`。这样引擎创建成功、数据库失败时仍有可报告和补偿的控制面 ID。`SessionRef.session_id` 保持为原生 engine session ID。
- `resume_session` 先读取当前租户记录并校验 `engine_name`；记录缺失、引擎不匹配或原生恢复失败都返回明确错误，不创建替代会话。
- `list_sessions` 只按 `(platform_id, tenant_id, ...)` 查询，并使用稳定的 `(updated_at, session_id)` 游标。
- 只有 `ACTIVE` 会话可以接收或领取新的 run；`PAUSED`、`INTERRUPTED` 和 `CLOSED` 会话拒绝新请求及执行。
- 关闭、暂停和中断是显式状态变化，不能由普通列表或恢复操作隐式修复。合法转换为 `ACTIVE -> PAUSED|INTERRUPTED|CLOSED`、`PAUSED -> ACTIVE|INTERRUPTED|CLOSED`、`INTERRUPTED -> ACTIVE|CLOSED`；`CLOSED` 终态。重复写入同一状态是幂等 no-op。

### Run、幂等和租约规则

- 幂等键唯一范围是 `(platform_id, tenant_id, idempotency_key)`；摘要是请求 canonical JSON（session、prompt、metadata、parent_run_id）经过 SHA-256 的十六进制值，带 `request_digest_version`。相同键且摘要相同返回原 run，不重复执行；摘要不同返回冲突。
- `EngineResult` 可选携带外部存储的 `result_ref`；持久化服务仅记录该引用，不保存结果正文。Harness home、workspace 和对象存储的创建、隔离及访问控制由接入方提供。
- `claim_next` 在事务内使用 `SELECT ... FOR UPDATE SKIP LOCKED`，没有可领取记录返回 `None`；`claim(run_id, ...)` 用于已选定 run 的定向领取。两者都只领取 `QUEUED` 记录，递增 fencing token 并写入 worker/租约过期时间。
- `heartbeat`、`finish`、`mark_unknown` 的 `UPDATE` 必须同时匹配租户、run_id 和当前 fencing token；影响行数为零即拒绝旧 worker。
- run 合法转换为 `QUEUED -> STARTING -> RUNNING -> SUCCEEDED|FAILED|CANCELLING|UNKNOWN`，`STARTING` 也可进入 `FAILED|CANCELLING|UNKNOWN`，`CANCELLING -> SUCCEEDED|CANCELLED|FAILED|UNKNOWN`；`CANCELLING -> SUCCEEDED` 处理取消请求与成功结果之间的竞态，终态仍不可覆盖。
- 终态不可被普通更新覆盖。租约过期先进入 `UNKNOWN`，不自动重新提交模型任务。
- 取消先把控制面状态改为 `CANCELLING`，最终由 worker 回报 `CANCELLED`、`FAILED` 或 `UNKNOWN`。

### 事务和错误

- 每个写操作显式开启、提交或回滚事务；连接归还池前清理事务状态。
- 状态行更新与对应 audit_events 插入在同一事务中；只有条件 UPDATE 影响行数为 1 时才插入审计。旧 fencing token 的状态更新失败，因此不会产生对应的审计写入。审计 payload 使用扁平字段白名单，不接受嵌套对象、未知键或不安全值。
- 业务错误使用稳定错误类型：租户越权、资源不存在、幂等冲突、租约丢失、非法状态转换和数据库不可用。
- SQL 参数只来自方法参数和已验证的记录，不拼接用户输入。

### V001 字段补齐

- `sessions` 增加 `harness_home_ref`、`workspace_ref`、`last_run_at`；这些是经过存储层校验的 opaque 引用，不是本地路径。
- `runs` 增加 `request_digest`、`request_digest_version`、`started_at`、`finished_at`、`error_code` 和 `result_ref`；不保存 prompt 或完整模型结果。
- `audit_events` 增加显式 `actor_type`、`session_id`、`run_id`、`policy_version` 和 `fencing_token`；worker/system actor 可以没有 `actor_user_id`，但必须有明确 actor type。
- migration 增加 `schema_version` 表和一条版本记录，doctor 只读检查该版本，不执行隐式迁移。

## 文档交付

- 根 README：安装、启动本地 MySQL、运行测试和 doctor。
- `harness-engine/README.md`：仓储/会话 API、配置优先级、自定义引擎和工具插件示例。
- 中文迁移与验证说明：实际命令、环境变量、通过/跳过/未验证的含义。
- 真实验证矩阵：记录 migration、租户越权、幂等、SKIP LOCKED、fencing、连接池清理和会话恢复证据。

## 验收标准

- 离线测试覆盖记录映射、租户边界、状态转换、幂等冲突、单 worker 领取、旧 token 拒绝和连接回滚。
- MySQL 8.4 集成测试执行 `V001__core_schema.sql` 并验证跨租户联合外键、CHECK、SKIP LOCKED、幂等唯一键和 fencing 更新。
- doctor 在缺少 MySQL 时明确报告 `SKIPPED` 或 `UNAVAILABLE`，不把 Fake 或 SQLite 结果标成真实通过。
- 文档中的每条命令都能在当前 Python 子项目中定位到真实入口；最终报告区分已通过、跳过和未验证。
