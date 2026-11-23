# Proposal

## Why

当前仓库已有 DeepSeek Harness SDK 适配器，但 MySQL Compose 的 API 未收到运行引擎配置，仍只向 ExecutionProfile 表单公布 Fake；已有 acceptance overlay 也只切换 Worker，导致控制面可见选项与执行端能力不一致。将 DeepSeek Harness 作为 MySQL/Compose 默认引擎，可以让管理员按租户配置真实可运行的 Profile，并从创建配置到提交 Run 完成验证。

## What Changes

- MySQL/Compose 缺省使用 DeepSeek Harness；Fake 模式仍使用 Fake，运维可通过同一个 `AGENT_PLATFORM_RUNTIME_ENGINE=fake` 显式切回。
- API、Run Worker 和验收 Worker 使用相同的运行引擎配置；EngineProvider 清单只显示规范项 `deepseek_harness`，旧 `deepseek` Profile 继续兼容解析。
- 复用现有 Profile、ModelRoute、SecretRef 和 RunSnapshot 边界；补充可执行的本地配置与验收说明，以及静态管理/运维原型。
- 使用本机确定性模型 Mock 验证真实 Harness SDK 适配层与 Worker/Run 链路；不在测试中调用真实 DeepSeek 云端模型。
- 不改变数据库结构；本期不增加新模型供应商 API，也不自动创建或切换租户 ModelRoute。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `execution-engines`：MySQL/Compose 默认 EngineProvider、API 与 Worker 引擎一致、规范名称与旧别名解析、DeepSeek Profile 的真实 Run 执行语义。
- `platform-api`：EngineProvider 清单和 ExecutionProfile 创建/发布只接受当前服务实际注册的 Provider；权限和租户边界继续生效。

## Impact

- **配置与运行时：** `backend/src/agent_platform/api/app.py`、`backend/src/agent_platform/application/engine_providers.py`、`deploy/control-plane/docker-compose.yml`、验收 Compose overlay 和 `.env.example`。
- **接口：** 不新增路由；既有 `GET /api/v1/engine-providers` 在 MySQL/Compose 默认返回 DeepSeek Harness，`POST /api/v1/execution-profiles` 可创建相应 Profile；兼容旧 Profile 名称。
- **执行：** Run Worker 继续从不可变 RunSnapshot 选择 Adapter；租户 ModelRoute SecretRef 仅在 Worker 解析，凭据不进入 API 响应、Profile、Trace 或 Snapshot。
- **验证与交付：** 后端单元/API 与 Compose 配置校验、确定性 Mock 的 Harness Run 验收、中文说明 `docs/DeepSeekHarness默认引擎运行与验收.md`、独立原型 `docs/原型/DeepSeekHarness默认引擎配置.html`。
- **阶段边界：** 本期完成本地 MySQL/Compose 的 EngineProvider 到 Worker 执行链路。真实 DeepSeek 云端凭据/公网推理与生产 Secret Manager 验收属于后续运维工作，不假设已配置或已授权。
