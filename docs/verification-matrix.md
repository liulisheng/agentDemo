# 真实验证矩阵

## Task 7 最终验收（2026-11-14）

- 离线命令：`PYTHONPATH=src /tmp/agentdemo-task5-venv/bin/python -m pytest -q`，结果 `162 passed, 4 skipped`。
- 改动文件 Ruff：`/tmp/agentdemo-task5-venv/bin/ruff check` 针对 Task 5 源码和测试，结果通过。全仓 Ruff 仍有 11 项既存问题，未归因于本轮改动。
- wheel：`python -m build --wheel --no-isolation` 成功生成 `harness_engine-0.1.0-py3-none-any.whl`；隔离安装后 doctor 包导入 `PASSED`，migration 和 MySQL 未请求时为 `SKIPPED`。
- MySQL：干净 MySQL `8.4.13` 依次执行 V001、V002，`tests/test_mysql_integration.py tests/test_message_deletion.py` 为 `14 passed`。
- 对象存储/删除：离线和 MySQL 集成覆盖加密 ContentStore、对象写入失败、GC 重试、会话删除、租户隔离、延迟 PUT 重入队。
- DeepSeek SDK 本地中转站复验：`deepseek-harness-sdk==0.2.2rc2` 与匹配 runtime 启动/关闭通过，真实 prompt 返回 `sdk-0.2.2-ok`，`finish_reason=completed`，close 通过；实时 delta/resume 仍为 `UNVERIFIED`。
- Docker：npm CLI 变体的 Docker-only 验收已通过；镜像 `agentdemo/harness-worker@sha256:fa57537bb3bf80d18b8f21b75a52d610aafce35d466d351e9ecc83bcaa27cd1b`，Fake CLI、SDK startup、真实本地中转站 prompt、MCP echo、session lifecycle 和 MySQL V001/V002 persistence demo 均通过，生命周期和 MCP echo 也通过。
- Docker SDK image：默认 Python wheel runtime 仍因 Linux SEA native binding 失败；npm CLI 变体 `@deepseek-ai/dsh@0.2.2-rc.3` 通过 `DSH_BIN` 接入项目 CLI 后，SDK startup、真实 prompt、MCP echo 和生命周期 smoke 已通过；MySQL persistence demo 返回 `schema_version=2`、`idempotent_submission=true`、`persisted_run_state=SUCCEEDED`。
- Kubernetes：使用临时 kind v1.32.2 集群和独立 `/tmp` kubeconfig 验证受限 Job，Job 输出 `fake: k8s-sandbox-ok`，Pod Security restricted 标签、`runAsUser=65532`、只读根文件系统、`cap-drop=ALL`、`RuntimeDefault`、无 ServiceAccount token、资源上限和 default-deny NetworkPolicy 均已核对；强制删除长任务后 Job 进入 Failed 且无重试。目标生产集群、节点 taint/toleration、真实 SDK/MCP、NetworkPolicy 实际出站阻断和跨节点隔离仍未验证。

## P4 Python 控制面

- 离线命令 `python -m pytest -q`：`88 passed, 1 skipped`。跳过项是只在显式配置 MySQL 时运行的真实数据库测试。
- P4 源码和测试的 Ruff 检查通过；wheel 构建成功。隔离安装 wheel 后运行 doctor，包导入为 `PASSED`，未打包 migration 和未请求 MySQL 均为 `SKIPPED`。
- `harness doctor --json`：包导入和 V001 文件检查为 `PASSED`，未请求数据库时 MySQL 为 `SKIPPED`，exit code 为 `0`。
- 仓库全量 Ruff 检查仍报告 18 项既存问题，位于 DeepSeek Demo、沙箱基线及非 P4 测试；未将全仓 lint 记为通过。

## MySQL 8.4

使用干净 MySQL 8.4 实例执行 `docs/migrations/V001__core_schema.sql`，再验证：

- 平台、租户、用户、会话之间的联合外键拒绝跨平台/跨租户关联。
- CHECK 拒绝非法状态值；迁移在目标字符集和排序规则下成功。
- 两个消费者使用 `FOR UPDATE SKIP LOCKED` 时不会领取同一 queued run。
- 旧 fencing token 的 heartbeat/finish 影响行数为 0。
- 同一租户重复幂等键返回冲突或原运行，不创建第二条记录。
- 连接池复用不会携带上一次请求的租户参数；所有查询显式带 platform_id 和 tenant_id。

### P4 本地验证结果（2026-11-11）

在一次性 Docker MySQL `8.4.13` 数据库上执行 V001 和真实连接器集成测试：

- migration 成功创建全部表和 `schema_version=1`。
- `tests/test_mysql_integration.py`：`1 passed`；验证联合外键、CHECK、幂等冲突、`FOR UPDATE SKIP LOCKED`、审计失败事务回滚、旧 fencing token 拒绝、租约过期转 `UNKNOWN`，以及会话创建审计、运行时间戳和结果引用持久化。
- DB-API 单元测试在同一连接上连续执行两个租户查询，确认第二个查询显式绑定自己的租户范围；MySQL 真实测试另行验证仓储租户过滤。
- 集成测试针对随机生成的数据行，并在 `finally` 清理；最近一次验证使用的临时数据库和容器已删除。

以上仅证明 Windows 开发机上的 MySQL 8.4.13 行为，不涵盖生产备份/恢复、高可用、负载、权限最小化或目标 Linux 环境。

## 对话正文、流式 checkpoint 与删除清理（阶段 4.5，Task 1–5 已实现）

Task 1–4 的历史快照：V001+V002、只读 doctor、加密 ContentStore、消息/分段仓储及流式服务已于 2026-11-13 在一次性 MySQL 8.4.13 上验证；当时 `tests/test_mysql_integration.py` 为 7 passed，其中包括 schema、消息与 chunk 事务、服务级流式完成/续读和过期 worker 恢复。当前最终验收结果见上方 Task 7 小节。

### MySQL 8.4 / V002

- V001 后依序执行 V002；CHECK、复合外键、分段唯一键、租户范围对象引用唯一键、upload intent lease 列和 `schema_version=2` 均成功。
- `harness doctor --require-mysql --json` 对已安装 schema version 2 和 V002 文件报告通过；运行前后 `schema_version` 均为 2，doctor 不执行 migration。
- V002 代表性字段、sequence CHECK 和租户外键通过真实 MySQL 检查；消息仓储使用锁定 session 行分配 `sequence_no`，并在消息插入事务回滚时一起回滚计数。
- MySQL 消息集成覆盖 user/assistant 顺序为 1、2、同事务事务回滚，以及租户/平台范围过滤；并发序号竞争没有 barrier stress test。
- `append_chunk` 在同一 MySQL 事务锁定 run、message 和 upload intent，验证租户、有效 fencing token/lease 与下一个连续序号，再插入 chunk、推进 `last_committed_chunk_sequence` 并发布 intent。MySQL 8.4.13 集成测试已验证提交、回放和部分完成。
- `list_chunks_after` 按租户查询高水位和 chunk 序列，拒绝超前 cursor、中间缺口和尾部缺口；跨租户按资源不存在处理。离线仓储测试覆盖重复/跳号、过期 intent、旧 fencing token。
- `PersistentSessionService.tail_stream(tenant, message_id, last_chunk_sequence)` 只从 durable chunk log 读取并核对内容哈希与 UTF-8；空查询后提交 chunk、再次查询能够读到新 chunk。调用方收到通知或等待超时后必须重新调用该 API。
- stream manifest 发布与 assistant `COMPLETE`、run `SUCCEEDED`、`runs.result_ref` 在一个事务中提交；MySQL 服务级集成验证最终 manifest 和按 cursor 续读。
- MySQL 集成验证 upload intent 续租、按 DB 时间过期并排入 GC queue、过期后续租/发布拒绝；publication-vs-expiry 的 barrier 并发测试尚未做。
- 会话删除、24 小时孤儿扫描和 `DELETED` 任务重入队已由 `MySqlContentGcRepository` 与 `MySqlContentGarbageCollector` 实现；MySQL 8.4.13 集成验证了删除后读取撤销、GC 队列、对象删除失败重试，以及首次清理后完成的延迟 PUT 重新入队。
- `COMPLETE` / `PARTIAL` 消息必须有完整内容引用、SHA-256、字节数和 content type；空引用仅允许无内容或生成中的状态。
- 消息和会话删除会在同一事务将正文/chunk refs、`runs.result_ref`、Harness home、workspace 和上传 intent 引用排入 `content_gc_queue`，过期 intent 与索引一起撤销；队列 worker 可重试对象删除且不恢复应用读取。

### ContentStore 与消息服务

- 本地加密 ContentStore 已通过 25 个离线测试：AES-GCM 往返与密文检查、租户隔离、篡改/错误密钥拒绝、不可变写、幂等删除、年龄分页、Windows junction/设备名/大小写路径检查及写入失败清理。
- 可选消息仓储已接入：用户输入和助手最终 `EngineResult.text` 写入加密 ContentStore；MySQL 行与审计仅保存 opaque ref、SHA-256、字节数、类型、状态及允许的安全元数据。
- 内容写入失败时不会发布 run/message。消息插入后的审计失败测试确认 run、message、intent publication 和序号在一个事务中回滚；对象留在待过期 reservation 下，不出现可读数据库引用。
- `LocalContentStore` tenant-scoped 分页仅枚举当前租户 namespace；GC 仓储在枚举结果上再次校验 messages、chunks、runs.result_ref、session refs 和有效 upload intents，保留有效根并重新排队已删除后重现的对象。
- 相同幂等 key/digest 返回原 run 与 USER 消息 ID；不同 digest 冲突且不新增可见消息；成功后 ASSISTANT `content_ref` 与 `runs.result_ref` 完全相同。
- MySQL 集成验证跨平台/租户 message/chunk lookup、upload renew/pending-message scope 失败为 not found；manifest 是加密对象，MySQL 只保存其引用与校验元数据。
- 独立新进程 transcript 读取、生产对象存储/KMS、DB 提交异常分类和死锁自动重试仍待验证/实现。

### 流式输出、恢复和推理边界

- 有真实 `VisibleTextDelta` 的引擎会在生成前创建助手 `PENDING` 消息，并按序将可见文本以最多 32 KiB、最多 2 秒（先到者）写成加密 checkpoint；边界保留 UTF-8 字符。最终 manifest 只列 chunk 引用、字节数和 SHA-256。
- DeepSeek `deepseek-harness-sdk==0.2.1rc2` 的 Python 通知回调只接收协议通知，`assistant/message` 在模型轮次结束后才包含完整消息；实时 `agent/assistant-stream` 属于进程内事件。适配器现报告 `streaming=False`，没有把完成后的消息伪装成实时 delta。[Pinned SDK protocol](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/packages/sdk/protocol/README.md)、[pinned LLM streaming contract](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.1-rc.2/docs/subsystems/llm-streaming.md)。DeepSeek 实时流仍未验证。
- worker 过期后系统回收器用轮换后的 fencing token，把已提交前缀写入 `PARTIAL` manifest 并将 run 保持为 `UNKNOWN`；空流转成无正文的 `FAILED` 消息。回收失败会留在可重试的 `PENDING`/`STREAMING` 集合中；不会重发模型请求。
- 服务只接受 engine-neutral `VisibleTextDelta`，忽略普通 vendor/lifecycle 事件；推理、prompt、凭据和未批准 vendor payload 不进入 chunk、manifest 或审计详情。真实 SDK 原生历史是否保留内部推理仍需单独验证。
- session 级显式删除、UPLOADING intent 清理、GC 重试以及晚到 PUT 再扫描已通过离线和 MySQL 8.4.13 验证；备份副本依未来部署的备份周期清理。

当前实现状态：V001+V002 和只读 doctor 在一次性 MySQL 8.4.13 验证通过；离线全套 162 passed/4 skipped，Task 5 改动文件 Ruff 检查通过，MySQL 集成套件为 14 passed。Task 1–5 的消息、分段、manifest、durable tail、过期 worker 恢复、会话删除和 GC 重试已实现。DeepSeek 实时 delta、生产对象存储/KMS、Kubernetes 沙箱和真实 SDK/MCP 容器链路仍未验证；本地 Docker Fake worker smoke test 已通过。

## Harness SDK

兼容层只允许依赖 `HarnessEngine`，底座 SDK 类型不能出现在 core/application 模块。每个锁定版本都需要运行：

- 初始化、start、close 和子进程清理。
- 真实 prompt、final response、finish reason 和通知顺序。
- 同一 dsh_home 的 session resume，以及历史缺失时的明确错误。
- 审批请求无 responder 时拒绝，有 responder 时只接受合法决策。
- Python MCP 工具发现、参数校验、成功结果和工具异常。
- timeout、cancel、断连和未知结果不得自动重放。

目前已验证 `0.2.1rc2` 的初始化、start、真实 prompt、final response、finish reason 和 close；session resume、审批、timeout/cancel 及目标运行环境仍待执行。适配器当前声明 `streaming=False`：固定版本公开 SDK 没有将生成中的文本 delta 交给 Python 应用的回调；通用 stream 持久化由引擎事件契约和 Fake Engine/MySQL 集成验证，不能据此声称 DeepSeek 实时流通过。

### DeepSeek + MySQL 持久化演示（2026-11-11）

- 在 Windows Python 3.12 的 SDK 验证环境中，使用 `deepseek-harness-sdk==0.2.1rc2`、`deepseek-harness-runtime-bin==0.2.1rc2`、`mysql-connector-python==9.7.0` 和 MySQL `8.4.13` 执行 `harness persistence-demo --engine deepseek`。
- 真实 prompt 返回 `DeepSeek MySQL persistence flow verified.`；命令输出 `result_status=succeeded`、`persisted_run_state=SUCCEEDED`、`idempotent_submission=true`。
- 结束命令后，新 Python 进程重新连接 MySQL 查询到同一 run：`engine_name=deepseek`、`state=SUCCEEDED`、关联 session `status=ACTIVE`。
- 本次执行后的离线及 MySQL 集成测试：`91 passed`；演示相关源码与测试 Ruff 检查通过。

P3 本地工具链已补充验证：在 Windows Python 3.12 虚拟环境安装并锁定
`mcp==2.3.0` 后，stdio 服务成功发现 `echo`、完成调用，非法参数返回
`is_error` 结果；未安装 MCP 的基础环境会明确报依赖错误。DeepSeek 适配器
现在可按 `--mcp-tool echo` 生成带绝对 Python 路径的 profile patch。使用
`deepseek-harness-sdk==0.2.1rc2`、匹配的
`@deepseek-ai/dsh-mcp-client@0.2.1-rc.2`、`mcp==2.3.0` 和本地 OpenAI
兼容代理，已验证真实 Harness 发现 `mcp__python-tools__echo` 并返回
`{"text": "mcp-real-ok"}`。目标 Linux/Docker/Kubernetes 环境仍需单独验证。

## Docker/Kubernetes 沙箱

在 Linux 节点使用固定镜像摘要和受限 Docker/Kubernetes 运行时，验证：worker 启动 Harness、MCP 通信、session resume、产物导出、网络默认拒绝、凭据撤销、租约过期、旧 worker fencing、容器/Pod 强制终止和孤儿回收。必须验证 Pod Security、禁止特权、禁止 hostPath/hostNetwork/hostPID、资源上限和节点隔离。

Docker 单 worker smoke test 已通过：镜像 `agentdemo/harness-worker@sha256:72d3aa46909990a8a1dd773c1da24c72958a6e2aa9f1e24283c34b0338d89216`，容器以 uid/gid `65532:65532`、只读根文件系统、`network=none`、`cap-drop=ALL`、`no-new-privileges` 和 1 GiB 内存限制启动，Fake CLI 返回 `fake: docker-sandbox-ok`。本地 kind Kubernetes smoke test 也已通过；目标生产集群、真实 SDK/MCP、NetworkPolicy 实际出站阻断、节点隔离和跨节点回收仍未验证，本节整体仍未达到生产通过门槛。microVM 后端不在当前阶段范围内。
