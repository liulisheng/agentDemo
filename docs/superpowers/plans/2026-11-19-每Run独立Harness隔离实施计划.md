# 每 Run 独立 Harness 隔离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个 Run 使用独立 Harness 进程和工具配置，同时保留 Conversation Session 历史并隔离同 Session 并发与不确定执行。

**Architecture:** Worker 按固定 RunSnapshot 创建请求级 Engine；Harness 使用稳定的租户 Session Home 和独立临时 Profile overlay，过滤后的 Stdio proxy 只发布当前 Run 的 READ/PREVIEW 工具。MySQL V007 Session lease 串行访问同一 Home；Run UNKNOWN 时隔离该 Session，避免旧进程和后续 Run 共享状态。

**Tech Stack:** Python 3.13、FastAPI、MySQL 8.4、DeepSeek Harness SDK `0.2.2rc2`、官方 MCP Python SDK、pytest、Ruff、OpenSpec。

**Spec:** `openspec/changes/harness-employee-execution-workbench/design.md`、`openspec/changes/harness-employee-execution-workbench/specs/execution-engines/spec.md`、`openspec/changes/harness-employee-execution-workbench/specs/control-plane-persistence/spec.md`。

## Global Constraints

- SQL migration 只放 `docs/migrations/`，新增表/字段使用中文 COMMENT，历史 migration 不重写。
- 每次查询/写入 Session lease 均使用 `platform_id + tenant_id + session_id`。
- WRITE 工具不进入 Harness 工具表；只有 READ/PREVIEW allowlist 可见。
- SecretRef 的值不得进入 EngineRequest、配置 overlay、Trace 或日志。
- Worker 只有在确定 Harness/MCP 已关闭后释放 Session lease；UNKNOWN 时必须隔离 Session。
- 代码注释、迁移字段说明和用户可见文档使用中文；不提交代码。

---

### Task 1: 为每 Run Engine 生命周期写回归测试

**Files:**
- Modify: `backend/tests/test_harness_runtime_adapter.py`
- Modify: `harness-engine/tests/test_deepseek_engine.py`

**Interfaces:**
- 输入：不同 Run 的 `EngineRequest`，分别带不同员工系统指令和 READ/PREVIEW 工具目录。
- 预期：Engine factory 每次得到新的 Engine；每个 Harness 的 prompt 和 MCP overlay 只含自己的 Run 内容；WRITE 工具拒绝进入 overlay。

- [ ] **Step 1: Write the failing tests**

为 `HarnessRuntimeAdapter` 添加 `test_adapter_creates_and_closes_one_engine_per_run`：注入记录请求的 factory，连续运行两个不同 `run_id`，断言 factory 两次返回的实例不同、各自收到自己的 `employee_context`/工具定义且均调用 `close()`。

为 `DeepSeekHarnessEngine` 添加 `test_deepseek_applies_each_run_context_and_read_preview_tools_in_isolated_overlay`：构造两个 Run 的 Engine，断言 system prompt 与 MCP allowlist 各自匹配输入，WRITE 风险工具导致明确策略错误，临时 overlay 路径不同且关闭后删除。

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run: `cd harness-engine && PYTHONPATH=src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_deepseek_engine.py -k isolated_overlay -q`，随后 `cd ../backend && PYTHONPATH=../harness-engine/src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_harness_runtime_adapter.py -k one_engine_per_run -q`。

Expected: 当前 DeepSeek request-scoped fail-closed 分支触发，Backend adapter 仍只有一个 lazy Engine，测试因缺少 per-Run 行为失败。

- [ ] **Step 3: Implement per-Run Harness configuration**

让 `HarnessRuntimeAdapter` 接受请求级 engine factory，在 `run()` 中构造 EngineRequest 后创建 Engine、启动、执行并在 `finally` 中关闭。DeepSeek Engine 从该请求读取固定 EmployeeContext 和 READ/PREVIEW 工具，并为每个 Run 生成单独配置 overlay。

- [ ] **Step 4: Run the focused tests and verify success**

Run: `cd harness-engine && PYTHONPATH=src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_deepseek_engine.py -q`，随后 `cd ../backend && PYTHONPATH=../harness-engine/src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_harness_runtime_adapter.py tests/test_runtime_employee_context.py -q`。

Expected: 新增的跨 Run prompt/tool overlay 测试和已有 SDK adapter 契约全部通过；Fake/DeepSeek Trace 不包含秘密或上下文正文。

### Task 2: 为 Session Home 增加 V007 租约 schema

**Files:**
- Create: `docs/migrations/V007__session_runtime_leases.sql`
- Create: `docs/migrations/V007__session_runtime_leases.down.sql`
- Modify: `backend/tests/test_migration_design.py`
- Modify: `backend/tests/integration/test_mysql_migration_runner.py`
- Modify: `backend/src/agent_platform/infrastructure/migrations.py`

**Interfaces:**
- 新表 `session_runtime_leases`：主键 `(platform_id, tenant_id, session_id)`；字段包含 `run_id`、`worker_id`、`fencing_token`、`lease_expires_at`、`state`、`created_at`、`updated_at`。
- `state` 仅为 `ACTIVE` 或 `QUARANTINED`；ACTIVE 要求 worker 与未过期租约，QUARANTINED 保留 Run/token 且无 lease 到期时间。

- [ ] **Step 1: Write failing V007 schema and preflight tests**

为 MigrationPreflight 添加 V007 sentinel fixture，断言缺表/缺列或 `schema_version < 7` 返回 APPLY_MIGRATIONS/DRIFT；为 MySQL migration runner 添加 information_schema 验收，检查表注释、全部列中文注释、外键、唯一索引及 CHECK。

- [ ] **Step 2: Run migration tests and verify failure**

Run: `cd backend && /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_migration_design.py tests/test_migration_preflight.py tests/integration/test_mysql_migration_runner.py -q`。

Expected: 因缺少 V007 up/down 文件和 sentinel 定义而失败。

- [ ] **Step 3: Add the V007 forward/down migration**

创建 tenant-scoped `session_runtime_leases` 表，表/字段 COMMENT 用中文，FK 同时引用 `sessions`、`runs`；建立按 session 唯一键和租约到期索引；up 登记 schema_version 7，down 删除表并回退版本记录。

- [ ] **Step 4: Run MySQL migration tests**

Run: 使用 disposable MySQL 8.4 执行 `backend/tests/integration/test_mysql_migration_runner.py`。

Expected: V001–V007 apply、V007 down 回退至 V006、中文 COMMENT 和租户复合 FK 全部通过。

### Task 3: 在 Run claim/heartbeat/终态事务中维护 Session lease

**Files:**
- Modify: `backend/src/agent_platform/ports/execution_repository.py`
- Modify: `backend/src/agent_platform/infrastructure/mysql_execution.py`
- Modify: `backend/src/agent_platform/domain/observability.py`
- Modify: `backend/src/agent_platform/application/control_plane.py`
- Modify: `backend/tests/integration/test_mysql_execution_repository.py`
- Modify: `backend/tests/test_run_worker.py`

**Interfaces:**
- 新增不可变 `SessionRunLease` 领域值，包含 tenant scope、session、run、worker、fencing token、expiry、ACTIVE/QUARANTINED。
- `claim_next_queued_run()` 同一事务获取 Run 和 Session lease；候选查询跳过已有 ACTIVE/QUARANTINED lease 的 Session。
- `heartbeat()` 原子续租 Run 和 Session lease；`complete_run()` 对已证明关闭的终态释放 lease；`mark_unknown()` 将 Session 设 INTERRUPTED 并隔离 lease。

- [ ] **Step 1: Write failing MySQL concurrency and lifecycle tests**

新增测试：两个不同 worker 同时 claim 同 Session 的两个 Run 时最多一个获得 claim；不同租户可分别 claim 相同 session_id；成功 Run 释放 lease；UNKNOWN/过期 lease 隔离 Session；Session INTERRUPTED 后工作台 turn 返回 INVALID_STATE。

- [ ] **Step 2: Run targeted integration tests and verify failure**

Run: `cd backend && MYSQL_HOST=127.0.0.1 MYSQL_PORT=3310 MYSQL_USER=root MYSQL_PASSWORD='' AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 PYTHONPATH=../harness-engine/src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/integration/test_mysql_execution_repository.py tests/integration/test_mysql_conversations.py -k session -q`。

Expected: 测试因 lease 表仓储行为和 Session 中断状态尚未实现而失败。

- [ ] **Step 3: Implement atomic lease acquisition and release**

在平台队列 claim 事务内锁定 Run，`INSERT IGNORE` 创建 Session lease，再将 Run CAS 为 STARTING；插入 lease 冲突时不改 Run。heartbeat 必须两处 token/worker/expiry 都匹配才成功；终态释放 lease；UNKNOWN 更新 Session=INTERRUPTED 并将 lease=QUARANTINED。`ControlPlaneService.get_session()` 在持久化模式始终重读 MySQL，避免跨 worker 状态陈旧。

- [ ] **Step 4: Run the focused integration and worker tests**

Run: `cd backend && MYSQL_HOST=127.0.0.1 MYSQL_PORT=3310 MYSQL_USER=root MYSQL_PASSWORD='' AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 PYTHONPATH=../harness-engine/src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/integration/test_mysql_execution_repository.py tests/integration/test_mysql_conversations.py tests/test_run_worker.py -q`。

Expected: 并发 Session claim、heartbeat、known terminal release、UNKNOWN quarantine 和 Worker cancellation 全部通过。

### Task 4: 接入请求级 Harness factory、Session Home 和 Stdio 配置 overlay

**Files:**
- Modify: `backend/src/agent_platform/ports/runtime.py`
- Modify: `backend/src/agent_platform/adapters/runtime/harness.py`
- Modify: `backend/src/agent_platform/application/resolvers.py`
- Modify: `harness-engine/src/harness_engine/core.py`
- Modify: `harness-engine/src/harness_engine/engines/deepseek.py`
- Modify: `backend/src/agent_platform/workers/run_worker.py`
- Modify: relevant backend/Harness tests

**Interfaces:**
- `RuntimeRequest` 附带 opaque `harness_home_ref` 和经固定 Snapshot 核验的 MCP Profile/tool allowlist，不含密钥值。
- `HarnessRuntimeAdapter` 的 factory 接受本次 `RuntimeRequest` 或 `EngineRequest`，一次 `run()` 创建并关闭一个 Engine。
- DeepSeek `EngineRequest` 在启动前编译成系统提示与每 Run patch；Stdio proxy 的工具表与 `EngineToolDefinition` 名称/schema/digest 完全相同。

- [ ] **Step 1: Write failing adapter and SDK contract tests**

添加跨 Run 不同员工/工具目录隔离、READ/PREVIEW 可见且 WRITE 不可见、profile/schema 漂移 fail closed、Harness close 后清理临时 overlay、EngineRequest 不含 SecretRef 明文的测试。

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd harness-engine && PYTHONPATH=src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_deepseek_engine.py tests/test_deepseek_sdk_contract.py -q`，随后运行 Backend Harness adapter focused tests。

Expected: 当前 request-scoped fail-closed 分支和共享 Engine 生命周期导致新增断言失败。

- [ ] **Step 3: Implement one Engine and one MCP overlay per Run**

从固定 RunSnapshot 构造 EngineRequest；将稳定 Session Home 引用安全映射到 Worker 数据根目录；为该 Run 创建权限 0700 的临时 profile patch；配置 Stdio proxy 只发布当前 allowlist 并在调用前重复校验；`HarnessRuntimeAdapter.run()` 在成功、异常、取消和 lease loss 路径都关闭 Engine。

- [ ] **Step 4: Run SDK, adapter and process lifecycle tests**

Run: `cd harness-engine && PYTHONPATH=src /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests -q -rs`，随后在 Backend 运行 runtime adapter、context 和 RunWorker focused suites。

Expected: 两个连续/交错 Run 的 prompt、工具 Schema、Session Home 和临时文件各自隔离；所有进程关闭路径通过。

### Task 5: 同步运维与验证记录

**Files:**
- Modify: `docs/mysql-schema.md`
- Modify: `docs/migrations/README.md`
- Modify: `docs/执行引擎适配设计.md`
- Modify: `docs/Harness员工执行闭环设计.md`
- Modify: `docs/验证记录.md`
- Modify: `deploy/control-plane/docker-compose.yml`
- Modify: `deploy/control-plane/.env.example`

- [ ] **Step 1: Record V007 and per-Run isolation behavior**

描述 Session lease ACTIVE/QUARANTINED 生命周期、Session INTERRUPTED 行为、稳定 Session Home 引用、临时配置清理及新的 `AGENT_PLATFORM_DATA_ROOT/harness-sessions` 目录边界。

- [ ] **Step 2: Run final validation**

Run full OpenSpec strict validation、Backend MySQL suite、Harness Engine suite、Frontend tests/build、Ruff/compileall 和 Compose config；把真实运行命令、结果及 StaffDeck/DolphinScheduler/容器未验证项写入中文验证记录。

- [ ] **Step 3: Review the pending OpenSpec tasks and diff**

只有 V007、每 Run Harness 隔离和各验收要求均有测试证据后标记 task 2.3/3.2/7.1 完成；StaffDeck 与非生产 DolphinScheduler 缺失时保留 1.1/5.3/7.2 未完成。
