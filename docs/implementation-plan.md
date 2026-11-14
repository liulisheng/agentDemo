# Harness 工程实施计划

状态：阶段 0 和阶段 2 的最小本地链路已完成；P2 生命周期协议、P3 工具插件和 P4 运行控制面持久化已落地。V002 已于 2026-11-13 在一次性 MySQL 8.4.13 实例执行；只读 doctor、ContentStore、上传 intent/消息仓储、会话级删除和可重试 GC 已实现并验证。生产对象存储/KMS、审批、Kubernetes 沙箱和真实 SDK/MCP 容器链路仍待验证或实现；本地 Docker Fake worker smoke test 已通过。

当前已交付：DeepSeek SDK/runtime `0.2.1rc2` 的真实 prompt 验证、
`DeepSeekHarnessEngine` 适配器，以及 CLI 的 `--engine deepseek` 入口。
P2 还补齐了引擎启动、会话引用/恢复、运行取消结果、生命周期状态、
引擎无关事件和 CLI 的统一关闭路径；未验证的 SDK 能力会显式返回不支持错误。
P3 增加了 `ToolDefinition`/`ToolRegistry`、`agent_harness.tools` 白名单发现、
输入 schema 校验、错误脱敏、内置 echo 工具、官方 MCP stdio 服务边界，
以及 DeepSeek `@deepseek-ai/dsh-mcp-client` profile patch 生成。

后续服务器阶段见 [多租户沙箱设计](sandbox-design.md)。下列阶段仅交付本地基础版；不得跳过沙箱安全验收，将本地执行方式直接开放给不可信租户。

本轮冻结产物：[MySQL schema](mysql-schema.md)、[Docker MySQL 8.4 配置](../deploy/mysql/docker-compose.yml)、[Kubernetes 沙箱基线](../deploy/kubernetes/README.md)、[V001 核心 migration](migrations/V001__core_schema.sql)、[V002 对话消息 migration](migrations/V002__conversation_messages.sql)、[migration 验收说明](migrations/README.md)、[用户中心认证协议](auth-protocol.md)、[租约与幂等协议](lease-idempotency.md)、[SDK 验证记录](sdk-validation.md)、[真实验证矩阵](verification-matrix.md)、[真实链路 runbook](real-validation-runbook.md)、[对话正文持久化设计](superpowers/specs/2026-11-13-conversation-content-persistence-design.md)、[实现任务拆解](superpowers/plans/2026-11-13-conversation-content-persistence.md)。

## 阶段 0：兼容性验证

- 确认可安装的官方 SDK/runtime 精确版本及 Python、Windows x64 支持情况。
- 核实 run、通知、取消、恢复及审批接口的实际签名。
- 验证本地 stdio MCP 配置以及禁用/审批高风险原生工具的方式。
- 将版本、验证命令及结果记录到兼容性记录；若不支持设计必需能力，先更新决策，禁止静默替代为模型 API 调用。

完成标准：存在可复现安装路径和接口依据，确认不需要自建通信协议。该阶段不默认发起收费模型调用。

## 阶段 1：离线骨架

- 在 `harness-engine/` 子项目内创建 `src` 布局、pyproject、uv 锁文件及开发检查配置；根目录不放 Python 源码和测试。
- 实现核心类型、引擎协议、注册工厂和 Fake 引擎。
- 实现配置优先级、基础 CLI 和上下文资源管理。
- 编写统一引擎契约测试及插件加载测试。

完成标准：无网络、无密钥也能通过 Fake 引擎演示执行闭环；未知引擎、重复插件和错误配置均明确报错。

## 阶段 2：官方引擎适配

- 封装官方 SDK，转换结果、事件和异常，不泄露底座类型到业务层。
- 实现独立 Harness home、启动、关闭、取消与超时处理。
- 接入 CLI 审批和 SDK 审批处理器；未配置处理器时拒绝高风险行为。
- 增加模拟 SDK/运行时测试及显式开启的真实集成测试。

完成标准：可验证启动关闭和失败清理；不得把协议响应超时当作任务已取消；不自动重试有副作用任务。

## 阶段 3：工具插件

- 实现工具定义、白名单发现、输入校验及独立 MCP 服务。
- 提供无副作用示例工具及安装注册示例。
- 配置 DeepSeek 连接本地 MCP 服务，并管理服务生命周期；目标 profile 需安装与 runtime 对齐版本的 `@deepseek-ai/dsh-mcp-client`。
- 测试成功、非法参数、工具异常、重复名称、未授权工具以及服务退出。

完成标准：真实 Harness 能发现并调用 Python 示例工具；非法输入不得执行函数，stdout 不混入日志。

## 阶段 4：MySQL 持久化、会话与文档交付

- 实现 MySQL/InnoDB schema、迁移、仓储层及本地开发数据库配置；开发和生产保持同一数据库类型。
- 落实 `platform_id + tenant_id` 约束、应用层租户授权、复合索引和连接池事务边界；每个租户数据查询均显式限定两者。
- 使用事务锁/租约和 fencing token协调跨进程会话并发；实现幂等状态变更。
- 完成新建、列表、恢复、运行状态记录和中断识别；控制面保存 Harness home、workspace 和结果的不透明引用，隔离持久卷/对象存储由接入方提供并负责授权。
- 编写中文 README、脱敏配置示例、自定义引擎及工具教程。
- 完成 doctor 命令和最终测试报告，区分已通过、跳过与未验证项目。

## 阶段 4.5：对话正文、流式输出与执行轨迹持久化

状态：Task 1–5 已实现，Task 6 文档已收尾；V001+V002、只读 doctor、流式消息仓储、会话删除和 GC worker 在一次性 MySQL 8.4.13 上验证。离线套件为 162 passed、4 skipped；Task 5 相关 MySQL 集成与删除/晚到 PUT 验证为 14 passed。Task 7 已完成离线、wheel/doctor、MySQL、对象存储/删除和 Docker smoke 验收；默认 Python wheel runtime 仍有 Linux native binding 问题，但 npm CLI 变体已在受限 Docker 中通过 SDK startup、真实本地中转站 prompt、MCP echo、session lifecycle 和 MySQL persistence；实时 delta/resume、目标生产 Kubernetes context、真实 MCP Pod、NetworkPolicy 实际阻断和节点隔离仍保持未验证。完整设计见[对话正文持久化设计](superpowers/specs/2026-11-13-conversation-content-persistence-design.md)，任务拆解见[实现计划](superpowers/plans/2026-11-13-conversation-content-persistence.md)。

- 新增 V002 `sessions.last_message_sequence` 字段及 `messages`、`message_chunks`、`content_upload_intents`、`content_gc_queue` 表；V001 保持不变。MySQL 仅保存租户范围消息索引、状态、哈希、字节数与对象引用。
- 更新只读 doctor 识别 V002/schema version 2；迁移仍由独立部署流程运行，应用不得自动改 schema。
- 通过租户感知的 ContentStore adapter 在加密隔离对象存储中保存用户 prompt、助手可见回复、流式分段和 Harness home；对象 key 不对客户端公开。
- 上传对象前先在 MySQL 建有 lease 的 `content_upload_intent`；对象引用发布与清理过期 intent 互斥锁同一行，延迟 writer 不能在对象被排入删除后发布引用。
- ContentStore 提供 tenant-scoped 分页对象枚举；孤儿回收默认只处理超过 24 小时且没有有效 DB 引用的对象，包含对已有 `runs.result_ref` 的保护。失效 intent 和 `DELETED` 清理记录不能永久屏蔽晚到 PUT；对象重新出现时扫描必须重新入队。部署可设更长宽限期。启用枚举回收前需排空旧版不创建 upload intent 的 writer。
- 用户消息在 run 入队前持久化；同幂等请求复用原消息和 run。助手最终内容保存为对象后再以一个 MySQL 事务完成消息、run、`result_ref` 与审计状态。
- 真正的流式 delta 按序以 32 KiB 或 2 秒（先到者）落为不可变对象分段，MySQL 只存 chunk 引用。`last_committed_chunk_sequence` 与 chunk 行同事务提交，防止序号缺口；重连持续 tail 同一个 MySQL durable log，通知仅作为唤醒提示，不在一次性 replay 后切换到可能漏事件的 live subscription。
- worker 退出后恢复最后已提交文本并标为 `PARTIAL`/`UNKNOWN`；没有经验证的上游续流句柄时，不自动重发模型请求。
- 审计记录工具名、状态、审批决定、时长、错误码和引用；不存原始内部推理、隐藏 reasoning、prompt、凭据、完整工具参数或完整工具输出。
- 消息正文与会话级审计元数据默认长期保留，直到显式删除；不设自动 TTL。删除事务先入 `content_gc_queue`，立即撤销读取，再异步清除正文、分段、manifest、Harness home、会话级 audit 与关联元数据；备份副本按部署备份周期清理。

完成标准：非流式与流式均可跨进程查询完整用户/助手消息；流式断线按序恢复；进程退出显示最后已提交部分内容；对象引用与 MySQL 事务失败可安全补偿；租户越权、fencing 旧 worker、重复幂等请求及删除重试均有验证证据；内部推理和敏感 payload 不落库。DeepSeek 只有在真实 SDK 增量事件可验证后才把流式持久化标为通过。

## 阶段 5：服务器 Docker/Kubernetes 验证

- 在 Linux 节点准备固定摘要的 worker 镜像和 Docker/Kubernetes 运行时。
- 在 Docker 容器和 Kubernetes Pod/Job 内启动 Python worker、官方 Harness SDK 和本地 MCP 服务，验证任务提交、事件转发、取消、会话恢复和产物导出。
- 验证网络默认拒绝、模型网关放行、凭据撤销、租约过期、fencing 旧 worker、容器/Pod 终止和孤儿回收。
- 通过对抗测试后，才把对应 SandboxProvider 状态从 unavailable 改为 available。

Docker Desktop Linux Engine 已用于 P4 MySQL 8.4.13 验证；阶段 5 另已完成一个含固定 SDK/runtime 依赖的 digest 镜像 Fake worker Docker smoke test，并用临时 kind v1.32.2 集群验证受限 Job、Pod Security、default-deny NetworkPolicy、资源限制、无 token 和强制删除回收。固定 `0.2.1rc2` SDK/runtime 在 arm64 与 amd64 Linux 容器启动时都缺少 `node-addon-require-builtin` native binding；目标生产集群、真实 Harness/MCP、NetworkPolicy 实际阻断、节点隔离和跨节点回收仍待目标 Linux/Kubernetes 环境验证。microVM 后端延后，不作为本阶段阻塞项。

P4 验证记录：离线 `pytest` 为 88 passed、1 skipped；真实 MySQL 8.4.13 集成测试为 1 passed；P4 相关源码及测试 Ruff 检查通过；wheel 构建成功；wheel 隔离安装后的 doctor 通过导入检查，并将未打包的 migration 与未请求的 MySQL 标为 `SKIPPED`。仓库全量 Ruff 当前仍有 18 项既存问题，集中在 DeepSeek Demo、沙箱基线和非 P4 测试；P4 验收不把它们记作通过。

完成标准：退出程序后可恢复同一引擎会话；缺失历史不被静默替换；并发请求不覆盖彼此状态；普通容器策略不能被任务修改。

## 验收矩阵

| 类别 | 必测场景 |
| --- | --- |
| 静态检查 | Ruff、mypy、包导入及构建检查 |
| 引擎契约 | 新建、执行、事件、取消、关闭、能力不支持 |
| 插件 | 未知名称、重复名称、加载失败、白名单限制 |
| 配置 | 优先级、缺少凭据、无效路径、无效引擎参数 |
| 持久化与会话 | V001/V002 MySQL 迁移、崩溃恢复、事务竞争、跨租户越权、租户上下文混淆、非活动会话拒绝新 run、缺失/损坏历史、引擎不匹配、消息顺序、run 幂等与结果引用 |
| 对话正文与流式恢复 | 外部对象写入/读取/校验、32 KiB 或 2 秒 checkpoint、断线重放、进程退出 partial/unknown、对象孤儿清理、显式删除与重试 |
| 故障 | 启动失败、协议异常、超时、崩溃、取消未确认 |
| 安全 | 无审批拒绝、凭据脱敏、未启用工具不可调用、内部推理不存储、审计 payload 字段白名单、对象读取租户鉴权 |
| 真实集成 | 一次模型调用、一次 Python 工具调用、一次跨进程会话恢复 |

离线测试默认运行，不依赖 API Key。真实模型测试必须显式开启，可能产生费用；没有凭据时报告未验证，不用 Fake 结果替代。第一版不以第二个生产引擎、多租户或 HTTP 服务作为验收条件。

关联：[架构设计](design.md)、[技术决策](decisions.md)。
