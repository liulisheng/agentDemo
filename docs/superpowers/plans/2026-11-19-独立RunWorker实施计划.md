# 独立 Run Worker 实施计划

> For agentic workers: 本计划在当前会话内逐项执行；每项使用测试先行，完成后再运行相关验证。

**Goal:** 由独立进程领取 MySQL 中 QUEUED 的 Conversation Run，使用租约/fencing 执行固定快照，并在取消或执行结果不确定时安全收敛状态。

**Architecture:** Run Worker 通过平台级 `SKIP LOCKED` claim 领取一条租户 Run，claim 返回平台/租户/Run/Worker/fencing 元组；后续状态写入都使用该租户身份和 fencing token。Worker 从关联 USER Message 的对象引用取回 prompt，按 RunSnapshot 固定版本解析员工上下文，再复用 ControlPlane 的 Runtime/Trace/结果保存逻辑；API 保留旧同步 Run 路径，另提供提交者取消异步 Run 的状态入口。

**Tech Stack:** Python 3.12、FastAPI、MySQL 8.4 / InnoDB、mysql-connector-python、pytest、现有 RuntimeAdapter / ObjectStore、Docker Compose。

**Spec:** `openspec/changes/harness-employee-execution-workbench/design.md` 的“API 入队，Worker 执行”“Worker 管理本机 Stdio 子进程”“EngineRequest 传业务快照”；任务 3.3 与 `specs/execution-engines/spec.md`、`specs/control-plane-persistence/spec.md`。

## 全局约束

- 所有用户态 Run 只执行创建时固定的 `RunSnapshot`；不得在 Worker 中改用员工当前草稿或新发布版本。
- 每次 MySQL 租户读写显式带 `platform_id + tenant_id`；跨租户 claim 只由受信 Worker 使用平台范围队列查询，claim 后的查询和更新必须恢复租户范围。
- Run 终态/Trace/Usage 写入必须校验 Worker ID 和当前 fencing token；过期持有者不能提交。
- 已发 Runtime 调用但结果无法证明时设为 UNKNOWN，绝不重新放回 QUEUED 或自动重试。
- 同步 `/sessions/{session_id}/runs` 保持现有行为；工作台继续通过 Conversation turn API 入队。
- 新增/变更可执行 MySQL SQL 只能放在 `docs/migrations/`；历史 migration 不改写；SQL 表/字段中文 COMMENT 约定继续有效。
- 新增公共 Python 类型、Worker、适配器和 API 路由需要中文用途说明；文档与 OpenSpec 状态同步。
- 代码、测试、文档和部署配置不得含凭据值、本机密钥或本机 Docker/REA 配置。

---

### Task 1：建立 Run claim / 输入引用仓储契约

**Files:**

- Modify: `backend/src/agent_platform/ports/execution_repository.py`
- Modify: `backend/src/agent_platform/infrastructure/mysql_execution.py`
- Modify: `backend/tests/test_mcp_lease_repository_contract.py`
- Modify: `backend/tests/integration/test_mysql_execution_repository.py`
- Modify: `docs/migrations/V006__mcp_profiles_and_approvals.sql`
- Modify: `docs/migrations/V006__mcp_profiles_and_approvals.down.sql`
- Modify: `docs/mysql-schema.md`

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class RunClaim:
    platform_id: str
    tenant_id: str
    run_id: str
    worker_id: str
    fencing_token: int

@dataclass(frozen=True, slots=True)
class RunInputRef:
    message_id: str
    content_ref: str
    content_sha256: str
    byte_length: int

claim_next_queued_run(platform_id: str, worker_id: str, *, lease_seconds: int) -> RunClaim | None
start_claimed_run(identity: IdentityContext, claim: RunClaim) -> bool
get_run_input_ref(identity: IdentityContext, run_id: str) -> RunInputRef
request_run_cancellation(identity: IdentityContext, run_id: str, requester_id: str) -> str
~~~

- [ ] **Step 1: 写仓储契约测试**：验证 claim SQL 按 `created_at, run_id` 稳定排序、使用 `FOR UPDATE SKIP LOCKED`、设置 STARTING/worker/lease 并递增 fencing；验证另一 Worker 不能重复 claim；验证过期 RUNNING/STARTING 变 UNKNOWN 且不会被再次 claim；验证消息正文引用按 platform/tenant/run/role 过滤；验证 QUEUED 取消直接进入 CANCELLED、RUNNING 取消进入 CANCELLING。
- [ ] **Step 2: 运行契约测试确认失败**：`cd backend && /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_run_worker_repository_contract.py -q`；失败应指向新端口或 SQL claim 行为缺失。
- [ ] **Step 3: 最小实现仓储端口与 MySQL SQL**：用租户内索引更新 V006，为 platform/state/created_at claim 增加前向索引；claim 在一个事务里锁定并更新单行。取消和开始运行均以当前状态、worker_id、token 作 CAS；租约失效恢复只写 UNKNOWN 并递增 token。
- [ ] **Step 4: 运行聚焦测试**：再次运行契约测试；同时运行 `tests/test_conversation_activity_touch.py` 和 `tests/test_migration_design.py`。
- [ ] **Step 5: 增加 MySQL 集成用例**：在 `test_mysql_execution_repository.py` 验证并发 claim 只有一个赢家、旧 token 不能启动/续租/结束、取消状态迁移、消息对象引用只返回对应 Run。

### Task 2：让 ControlPlane 可执行已经 claim 的固定 Run

**Files:**

- Modify: `backend/src/agent_platform/application/resolvers.py`
- Modify: `backend/src/agent_platform/application/control_plane.py`
- Modify: `backend/src/agent_platform/infrastructure/mysql_execution.py`
- Modify: `backend/tests/test_control_plane_service.py`
- Create: `backend/tests/test_claimed_run_execution.py`

**Interfaces:**

~~~python
AgentResolver.resolve_snapshot(identity: IdentityContext, snapshot: RunSnapshot) -> ResolvedAgent
ControlPlaneService.execute_claimed_run(
    identity: IdentityContext,
    run: RunRecord,
    session: SessionRecord,
    prompt: str,
    *,
    worker_id: str,
    fencing_token: int,
) -> RunRecord
~~~

- [ ] **Step 1: 写失败测试**：Run 入队后发布员工新版本，执行仍解析 `run.snapshot.employee_version` 指定的不可变员工/Skill/Tool/Profile；调用 `execute_claimed_run` 不再调用 `submit_run` 或重新 claim；正确的 fencing token 才能提交完成状态。
- [ ] **Step 2: 验证红灯**：`cd backend && /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_claimed_run_execution.py -q`；确认当前解析器只接受最新 Deployment 且 ControlPlane 没有 claimed-run 入口。
- [ ] **Step 3: 实现固定快照解析与共享执行路径**：从 Catalog 精确读取 Snapshot 版本，校验员工、Skill/Tool refs 和 Profile 版本与 RunSnapshot 一致；为 `_execute_run` 增加“已 claim”入口参数，使传统同步路径仍负责 `submit_run + acquire_lease`，Worker 路径直接使用现有 token。Runtime 结果对象引用、Trace 和 Usage 继续走已有同事务提交。
- [ ] **Step 4: 运行红灯测试转绿**：再次运行 `test_claimed_run_execution.py` 和 `test_control_plane_service.py`。
- [ ] **Step 5: 加入对象引用错误边界**：worker 在 Runtime 调用前读取输入引用并校验 SHA-256/字节数；缺失/校验失败写 FAILED，不运行 Runtime。

### Task 3：实现独立 Worker 循环、续租和取消响应

**Files:**

- Create: `backend/src/agent_platform/workers/run_worker.py`
- Modify: `backend/src/agent_platform/ports/execution_repository.py`
- Modify: `backend/src/agent_platform/infrastructure/mysql_execution.py`
- Modify: `backend/src/agent_platform/application/control_plane.py`
- Create: `backend/tests/test_run_worker.py`
- Create: `backend/tests/test_run_worker_repository_contract.py`

**Interfaces:**

~~~python
class RunWorker:
    async def run_once(self) -> bool: ...
    async def run_forever(self, stop: asyncio.Event) -> None: ...
~~~

- [ ] **Step 1: 写 Worker 行为测试**：Fake 仓储和 Fake Runtime 验证成功 Run、缺失/坏摘要输入 FAILED、不存在的员工版本 FAILED、每轮续租、租约失效时调用 Runtime.cancel 并禁止旧 token 完成、CANCELLING 时取消并在确定结果后写 CANCELLED，无法证明停止时写 UNKNOWN。
- [ ] **Step 2: 验证 Worker 测试失败**：`cd backend && /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_run_worker.py -q`；失败应指向 RunWorker 缺失。
- [ ] **Step 3: 实现异步单 Run 消费**：领取后构造 `auth_source="worker"` 的租户 IdentityContext；启动 heartbeat；读取 Run/Session/输入引用；CAS STARTING→RUNNING；以 `asyncio.to_thread` 执行同步 Runtime；每次 heartbeat 同时检查 CANCELLING；结束/取消/失租时停止 heartbeat 并完成或标 UNKNOWN。
- [ ] **Step 4: 运行 Worker 测试转绿**：再次运行 `test_run_worker.py`、`test_run_worker_repository_contract.py`。
- [ ] **Step 5: 确保异常按出站边界收敛**：Runtime 调用前错误为 FAILED；Runtime 调用开始后的异常或无法确认的取消为 UNKNOWN；任何 UNKNOWN 都不被重新 claim。

### Task 4：接入取消 API 与 Worker CLI/Compose

**Files:**

- Modify: `backend/src/agent_platform/api/schemas.py`
- Modify: `backend/src/agent_platform/api/route_modules/deployments.py`
- Modify: `backend/src/agent_platform/application/control_plane.py`
- Modify: `backend/src/agent_platform/api/app.py`
- Modify: `backend/pyproject.toml`
- Modify: `deploy/control-plane/docker-compose.yml`
- Create: `backend/tests/test_run_cancel_api.py`
- Modify: `backend/tests/test_app_configuration.py`
- Modify: `docs/本地控制面闭环与运维.md`
- Modify: `docs/Harness员工执行闭环设计.md`

- [ ] **Step 1: 写 API/配置测试**：只允许当前 Run submitter 取消；QUEUED 返回 CANCELLED，运行中返回 CANCELLING；fake 模式不能将同步执行伪装成后台队列；worker 配置缺失时命令明确失败；Compose Worker 与 API 共用 MySQL 和对象卷并在 bootstrap 后启动。
- [ ] **Step 2: 验证 API 测试失败**：`cd backend && /tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/test_run_cancel_api.py tests/test_app_configuration.py -q`。
- [ ] **Step 3: 实现 API 和命令入口**：新增 `POST /api/v1/runs/{run_id}/cancel`；新增 `agent-platform-run-worker` 命令，校验 MySQL schema 已 up-to-date，按 SIGTERM 停止 claim 循环，关闭连接池；Worker 使用明确的 `AGENT_PLATFORM_ID`、稳定 `AGENT_PLATFORM_WORKER_ID`、轮询间隔与租约配置。
- [ ] **Step 4: 增加 Compose Worker 服务**：使用现有后端镜像和 MySQL/对象卷环境，以独立命令启动，不暴露 HTTP 端口；依赖 bootstrap 成功并配置 restart 策略。
- [ ] **Step 5: 更新中文部署和状态文档**：补充 API enqueue → Worker claim → STARTING/RUNNING → terminal 状态图、运行环境变量、重启/租约过期行为和 UNKNOWN 不重试限制。
- [ ] **Step 6: 运行 API/应用配置测试与 Compose 静态校验**：运行本任务的聚焦测试并使用 `docker compose -f deploy/control-plane/docker-compose.yml config --quiet`（需要可用 Docker CLI 配置环境）。

### Task 5：交叉回归与 OpenSpec 状态更新

**Files:**

- Modify: `openspec/changes/harness-employee-execution-workbench/tasks.md`
- Modify: `docs/Harness员工执行闭环设计.md`

- [ ] **Step 1: 运行相关后端测试**：Worker、claim contract、ControlPlane、API、Conversation activity、Run status、Migration design。
- [ ] **Step 2: 运行全后端测试与格式校验**：`/tmp/agentdemo-harness-employee-sdk/bin/python -m pytest -q` 和 `python -m ruff check` 本次修改文件。
- [ ] **Step 3: 运行 MySQL 集成测试**：`/tmp/agentdemo-harness-employee-sdk/bin/python -m pytest tests/integration/test_mysql_execution_repository.py tests/integration/test_mysql_conversations.py -q`；没有 MySQL 8.4 时记录跳过原因，不标记集成通过。
- [ ] **Step 4: 运行 `openspec validate harness-employee-execution-workbench --strict --no-interactive`**，并核对部署文档与代码实际状态。
- [ ] **Step 5: 仅在运行逻辑、Compose 配置和相关测试均完成后，将 OpenSpec 3.3 改为 `[x]` 并刷新任务进度。**

## 已确认的后续验收边界

- 该 Worker 初始使用当前部署配置的 RuntimeAdapter；API 入队与 Worker 处理闭环可用 Fake Runtime 验证。
- 本计划不接真实 DolphinScheduler、不配置 MCP Profile、不改变已有同步 Run API。
- Harness EngineRequest 的工具隔离方案仍由 OpenSpec 3.2 的待确认选项决定；Worker 不绕过 RuntimeAdapter 自建 Agent Loop。
- 本机 Docker daemon/MySQL 8.4 可用前，不将 Docker Compose 启动、V006 apply/down 和 MySQL 集成用例描述为已验证。
