# Tasks

## 1. Provider 注册与服务默认配置

- [x] 1.1 先为 `EngineProviderRegistry` 写失败测试：DeepSeek Runtime 的清单只返回规范 ID `deepseek_harness`，`deepseek` 历史名称解析到同一 Adapter；Fake 环境不解析 DeepSeek。验证：`cd backend && pytest -q tests/test_engine_provider_registry.py`。
- [x] 1.2 更新 Provider 注册/别名解析实现并通过 1.1；测试 `GET /api/v1/engine-providers` 只返回一个 DeepSeek 选项，且历史 `deepseek` Profile 仍显示可用，不包含运行命令或秘密引用。验证：`cd backend && pytest -q tests/test_engine_provider_registry.py`。
- [x] 1.3 先为环境入口写失败测试：MySQL 模式未设置引擎时使用 DeepSeek，`AGENT_PLATFORM_MODE=fake` 仍默认 Fake，显式 `AGENT_PLATFORM_RUNTIME_ENGINE=fake` 时不装配 DeepSeek。验证：`cd backend && pytest -q tests/test_app_configuration.py`。
- [x] 1.4 更新环境默认与 Compose 配置：API 和 Run Worker 读取同一个 `AGENT_PLATFORM_RUNTIME_ENGINE`，同步传入非敏感 DeepSeek profile/provider/model 参数，`.env.example` 默认 DeepSeek，Fake 可显式覆盖；验收 overlay 同时启用 API 与 Worker。验证：`docker compose --env-file deploy/control-plane/.env -f deploy/control-plane/docker-compose.yml config`，检查 backend/worker 两端参数一致。
- [x] 1.5 为 Profile 创建与发布补回归测试：DeepSeek 注册后管理员可创建并发布规范 Profile；成员无权创建，未注册 Provider 返回稳定错误。验证：`cd backend && pytest -q tests/test_engine_provider_registry.py tests/test_execution_profile_versions_api.py`。

## 2. Harness 运行验收与交付材料

- [x] 2.1 添加 API/Runtime 确定性 Mock 回归，断言已发布 DeepSeek Profile 和授权 ModelRoute 固定到 Session/Run Snapshot、Run 进入 DeepSeek Harness Adapter；缺少 SecretRef 时 Worker Run 失败且不调用 Fake。再用 MySQL Compose 提交异步 Conversation turn，由独立 Worker 通过实际 Harness SDK 调用本地确定性 ModelRoute，并检查成功 Run/Trace。验证：`cd backend && PYTHONPATH=../harness-engine/src .venv/bin/python -m pytest -q tests/test_app_configuration.py tests/test_session_runtime_resolution.py tests/test_claimed_run_execution.py tests/test_deepseek_runtime_e2e.py`，以及本地 MySQL Compose Run。
- [x] 2.2 创建 `docs/DeepSeekHarness默认引擎运行与验收.md`，覆盖架构图、流程图、现有数据模型、API/状态、管理员/成员与租户边界、SecretRef、安全、失败状态、运维配置、逐步使用、验收标准、验证命令、已验证和未验证项。验证：文档中的路径与命令可在本地验收环境执行。
- [x] 2.3 创建自包含静态原型 `docs/原型/DeepSeekHarness默认引擎配置.html`，覆盖主配置流程、租户上下文、权限状态、空/加载/错误/结果状态；使用演示数据、不调用 API、不含凭据，支持键盘操作和窄屏。验证：离线打开原型并检查全部状态及交互。
- [x] 2.4 执行跨层验证：后端测试、Ruff、前端测试和生产构建通过；Compose 配置中 API/Worker 引擎一致；本地 Harness Run 的 Profile、Route、Run 状态及 Trace 与验收目标一致。验证命令：`cd backend && pytest -q && ruff check src tests`、`cd frontend && npm test -- --run && npm run build`、Compose `config` 与本地验收 Run。
