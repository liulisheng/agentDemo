# P4 持久化、会话与文档交付实施计划

> 依据：`docs/superpowers/specs/2026-11-10-persistence-session-docs-design.md`
>
> 方案：合约优先 + 原生 MySQL SQL。每个任务先写会失败的测试，再实现最小代码。

## 全局约束

- `V001__core_schema.sql` 是唯一 canonical schema；文档跟随 migration 更新。
- 生产实现只依赖 MySQL/InnoDB，不增加 SQLite fallback。
- 所有仓储方法第一个业务参数必须是 `TenantContext`；SQL 必须显式绑定 `platform_id` 和 `tenant_id`。
- 不保存 prompt、完整工具参数、凭据或完整模型结果；请求只保存 canonical SHA-256 摘要和对象引用。
- DeepSeek resume 仍由现有适配器明确返回 `UnsupportedCapabilityError`；P4 先交付 Fake/控制面恢复与状态记录。

## Task 1：持久化领域模型与仓储协议

**文件：**

- Create: `harness-engine/src/harness_engine/persistence/__init__.py`
- Create: `harness-engine/src/harness_engine/persistence/models.py`
- Create: `harness-engine/src/harness_engine/persistence/protocols.py`
- Create: `harness-engine/src/harness_engine/persistence/errors.py`
- Test: `harness-engine/tests/test_persistence_models.py`

**接口：**

- `SessionStatus`: `ACTIVE`, `PAUSED`, `INTERRUPTED`, `CLOSED`。
- `RunState`: `QUEUED`, `STARTING`, `RUNNING`, `CANCELLING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `UNKNOWN`。
- `SessionRecord(control_session_id, tenant, user_id, engine_name, engine_session_id, status, metadata, harness_home_ref, workspace_ref, timestamps)`。
- `RunRecord(run_id, tenant, session_id, idempotency_key, request_digest, state, worker_id, fencing_token, lease_expires_at, timestamps, error_code, result_ref)`。
- `Lease(run_id, worker_id, fencing_token, lease_expires_at)` 和 `Page(items, next_cursor)`。
- `SessionRepository`、`RunRepository`、`AuditRepository` 协议，所有读取和写入接收 `TenantContext`。

**测试：**

- 状态枚举只接受 schema 值；record 拒绝空 ID、跨租户嵌套引用和负租约。
- 会话状态转换矩阵与 run 状态转换矩阵拒绝非法转换，重复目标状态为 no-op。
- canonical request digest 对字段顺序稳定，对 prompt、session、metadata 或 parent run 变化敏感。
- `SessionRef` 映射保留原生 engine session ID，控制面 ID 单独存在。

## Task 2：MySQL migration 与 DB-API 适配器

**文件：**

- Modify: `docs/migrations/V001__core_schema.sql`
- Modify: `docs/mysql-schema.md`
- Create: `harness-engine/src/harness_engine/persistence/mysql.py`
- Modify: `harness-engine/pyproject.toml`
- Test: `harness-engine/tests/test_mysql_repository.py`

**接口：**

- `MySqlConfig.from_env()` 读取 `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_POOL_SIZE`。
- `MySqlDatabase.connect()`、`close()`、`health()`；不在包导入时强制导入驱动。
- `MySqlSessionRepository.create(tenant, record)/get/list/update_status()`。
- `MySqlRunRepository.create_or_get(tenant, record)/claim/claim_next/heartbeat/transition/finish/mark_unknown/get/list()`。
- `MySqlAuditRepository.append()` 只接受已验证的脱敏 details。

**测试：**

- 使用 DB-API fake connection 验证所有 SQL 带 platform/tenant 条件、参数化且事务提交/回滚正确。
- 重复幂等键同 digest 返回原 run；不同 digest 抛 `IdempotencyConflictError`。
- 旧 fencing token 的 heartbeat/finish/mark_unknown 影响行数为零，并且不写 audit。
- 跨租户 ID 返回 `NotFound`，不能泄露另一租户记录是否存在。
- 无 `mysql-connector-python` 时只在真正构造 MySQL 数据库时报明确依赖错误。

## Task 3：会话与运行协调服务

**文件：**

- Create: `harness-engine/src/harness_engine/session_service.py`
- Modify: `harness-engine/src/harness_engine/__init__.py`
- Test: `harness-engine/tests/test_session_service.py`

**接口：**

- `PersistentSessionService.create_session(tenant, engine, ...) -> SessionRecord`；`user_id` 来自认证上下文。
- `PersistentSessionService.resume_session(tenant, control_session_id, engine) -> SessionRecord`。
- `PersistentSessionService.list_sessions(tenant, ...) -> Page[SessionRecord]`。
- `PersistentSessionService.submit_run(tenant, control_session_id, request, idempotency_key) -> RunRecord`。
- `PersistentSessionService.execute_run(tenant, run_id, request, engine, worker_id) -> EngineResult`。
- `PersistentSessionService.request_cancel(tenant, run_id, engine) -> CancelResult`。
- `PersistentSessionService.mark_interrupted(...)` 和 `reconcile_expired_runs(...)`。

**行为：**

- 创建先生成控制面 ID，再调用 engine；数据库失败时记录可识别的 orphan/interrupted 状态，不自动重放。
- 恢复读取控制面记录并用已保存的 `engine_session_id`；引擎恢复不支持时原样传播 `UnsupportedCapabilityError`。
- run 先创建/复用幂等记录，再领取 lease；成功、明确失败和无法确认的异常分别写入 `SUCCEEDED`、`FAILED` 或 `UNKNOWN`。
- owner/admin 才能修改或取消；worker 通过 `TenantContext(auth_source="worker")` 执行 lease 操作。
- 只有 ACTIVE session 可以接收或执行新 run；恢复不能替换已保存的原生 engine session ID。
- 取消先持久化 CANCELLING，再调用当前活动引擎；worker 回报最终结果，或 system 在租约过期后将其收敛为 UNKNOWN。

## Task 4：doctor、CLI 和文档交付

**文件：**

- Create: `harness-engine/src/harness_engine/doctor.py`
- Modify: `harness-engine/src/harness_engine/cli.py`
- Create: `harness-engine/README.md`
- Modify: `README.md`, `docs/migrations/README.md`, `docs/verification-matrix.md`, `docs/real-validation-runbook.md`, `docs/implementation-plan.md`
- Test: `harness-engine/tests/test_doctor.py`, `harness-engine/tests/test_cli_doctor.py`

**接口：**

- `run_doctor(*, database=None, require_mysql=False, migration_path=None) -> DoctorReport`，每项带 `PASSED`、`SKIPPED` 或 `UNAVAILABLE`。
- `harness doctor [--json] [--require-mysql] [--migration PATH]`；默认只读，不发模型请求，不执行迁移。wheel 未附带 canonical migration 时为 `SKIPPED`。

**文档：**

- 给出环境变量、migration、测试、真实 MySQL 验收和恢复命令。
- 增加自定义引擎、工具插件、会话服务和租户边界示例。
- 报告 MySQL、Docker/Kubernetes 和 SDK 链路实际验证的范围；不能用离线 Fake 结果替代真实数据库结果。

## Task 5：完整验证与验收报告

- 运行离线 pytest、Ruff、包导入和 wheel 构建。
- Docker/MySQL 可用时执行 V001 和 7 项 migration 验收；不可用时记录明确 `SKIPPED`，不使用 SQLite 替代。
- 验证两个租户的读写隔离、幂等冲突、`FOR UPDATE SKIP LOCKED`、旧 token 拒绝、连接回滚和 Fake 会话恢复。
- 把实际命令、版本、结果和未验证项写入 `docs/verification-matrix.md`。

## 完成标准

- 仓储、会话服务和 doctor 有可重复的离线测试；真实 MySQL 结果与离线结果分开记录。
- 新建、列表、恢复、运行状态记录、租约过期和中断识别都有明确代码路径。
- 文档命令与真实入口一致，敏感配置示例已脱敏。
- 只有所有可执行验收项有证据后，才能将 P4 标记为完成；环境缺失项保持未验证状态。

## 执行记录（2026-11-11）

- 离线：`python -m pytest -q` → `88 passed, 1 skipped`。
- 真实数据库：一次性 MySQL 8.4.13 执行 V001；`tests/test_mysql_integration.py` → `1 passed`。验证后停止并删除容器及匿名数据卷。
- 静态与打包：P4 相关源码/测试 Ruff 检查通过；`pip wheel . --no-deps` 成功；隔离安装 wheel 后 `harness doctor --json` 的包导入通过、未打包 schema 与未请求 MySQL 跳过；源码 checkout 下 doctor 的 schema 检查通过。
- 边界：全仓 Ruff 仍有 18 项既存问题；单连接复用由 DB-API 测试验证，真实 MySQL 测试另行覆盖仓储 tenant 条件；Harness 历史、工作区及结果对象的隔离存储由接入方提供，P4 仅保存不透明引用。
