# 数字员工最小闭环实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让用户可以创建并发布带 Skill/Tool 的数字员工、部署到租户、创建 Session/Run，由 Fake Runtime 执行，并按租户查询完整 Trace。

**Architecture:** 后端采用模块化单体，领域对象和应用服务留在 backend，以端口调用 Fake Runtime；内存仓储提供确定性本地闭环，FastAPI 只做协议转换和身份依赖。前端新增最小 Run Trace 页面，使用已有 React Query 客户端查询后端。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、pytest、React、TypeScript、Ant Design、TanStack Query、现有 harness-engine FakeEngine。

**Spec:** docs/superpowers/specs/2026-11-14-digital-employee-closed-loop-design.md；OpenSpec openspec/changes/agent-platform-foundation/ 的 digital-employees、skills、execution-engines、execution-observability、platform-api 规格。

## Global Constraints

- 所有领域读写显式接收 IdentityContext，只使用认证解析出的 platform_id/tenant_id。
- 已发布版本使用不可变数据类；修改必须创建新草稿/版本。
- Runtime 私有状态只以 opaque 引用保存，后端不得导入 Harness 持久化模型。
- Trace 不保存完整 prompt、工具参数或响应正文，只存摘要、哈希和引用。
- 代码注释、文档、API 错误说明和提交信息使用中文；API 字段和类名使用稳定工程命名。
- 本阶段使用内存仓储，不新增数据库迁移；必须保留可替换仓储和 RuntimeAdapter 边界。
- 每项实现先写失败测试，再写最小实现，再运行对应测试；最终执行完整后端与前端校验。
- 不把现有 .agents/、缓存、密钥或本机配置加入提交。

---

### Task 1: 建立共享标识、错误和租户引用

Files:
- Modify: backend/src/agent_platform/domain/identity.py
- Create: backend/src/agent_platform/domain/common.py
- Test: backend/tests/test_common_contracts.py

Interfaces:
- ResourceRef(resource_type: str, resource_id: str, platform_id: str, tenant_id: str)
- RequestMetadata(request_id: str, idempotency_key: str | None = None)
- DomainError(code: str, message: str, details: dict[str, object] = {})

- [ ] Step 1: Write failing tests for blank identifiers and cross-tenant ResourceRef.
- [ ] Step 2: Run pytest backend/tests/test_common_contracts.py -q; expected failure because shared types do not exist.
- [ ] Step 3: Implement frozen dataclasses with non-blank validation and same_scope(identity).
- [ ] Step 4: Run the focused test and confirm pass.
- [ ] Step 5: Update the backend package import smoke test so common exports are importable.

### Task 2: 定义目录、版本和部署领域

Files:
- Create: backend/src/agent_platform/domain/catalog.py
- Create: backend/tests/test_catalog_domain.py

Interfaces:
- Enums: LifecycleStatus(DRAFT, PUBLISHED, DEPRECATED, ACTIVE), SkillType(INSTRUCTION, TOOL_BUNDLE, WORKFLOW)
- Dataclasses: ToolVersion, SkillVersion, DigitalEmployee, DigitalEmployeeVersion, ExecutionProfile, Deployment
- Methods: publish(), deprecate(), assert_mutable(), selected_tool_names()

- [ ] Step 1: Write failing tests for draft -> published, published immutability, dependency references, and deployment only accepting published employee versions.
- [ ] Step 2: Run pytest backend/tests/test_catalog_domain.py -q; expected failure on missing module/types.
- [ ] Step 3: Implement frozen published snapshots, monotonic version numbers, and deployment scope validation.
- [ ] Step 4: Run focused tests and confirm all state transitions pass.
- [ ] Step 5: Add structured dependency error payloads with DEPENDENCY_VALIDATION_ERROR.

### Task 3: 实现内存目录仓储、依赖校验和发布服务

Files:
- Create: backend/src/agent_platform/application/catalog_service.py
- Modify: backend/src/agent_platform/domain/catalog.py
- Test: backend/tests/test_catalog_service.py

Interfaces:
- InMemoryCatalogRepository
- CatalogService.create_tool/create_skill/create_employee
- CatalogService.publish_tool/publish_skill/publish_employee_version
- CatalogService.deploy
- CatalogService.resolve_deployment(identity, deployment_id)

- [ ] Step 1: Write failing tests for tenant isolation, publish missing Skill/Tool, valid publication, and deployment rollback.
- [ ] Step 2: Run focused tests and confirm red.
- [ ] Step 3: Implement repository dictionaries keyed by (platform_id, tenant_id, id); every method accepts IdentityContext; dependency validator requires published, same-tenant versions.
- [ ] Step 4: Run focused tests and confirm green.
- [ ] Step 5: Add deterministic seed fixtures: echo Tool, instruction Skill, and employee-admin draft for tenant-a.

### Task 4: RuntimeAdapter 与 Fake Runtime

Files:
- Create: backend/src/agent_platform/ports/runtime.py
- Create: backend/src/agent_platform/adapters/runtime/fake.py
- Create: backend/src/agent_platform/domain/execution.py
- Test: backend/tests/test_runtime_adapter.py

Interfaces:
- RuntimeCapabilities(streaming: bool, resume: bool, cancellation: bool)
- RuntimeRequest(run_id, session_ref, prompt, skill_instructions, allowed_tools)
- RuntimeEvent(event_type, span_type, payload)
- RuntimeResult(text, status, runtime_session_ref, runtime_result_ref, usage, events)
- Protocol RuntimeAdapter.create_session() / run(request) / resume_session(ref)
- FakeRuntimeAdapter returns fake: {prompt}, estimated usage, and opaque refs.

- [ ] Step 1: Write failing adapter contract tests for session creation, deterministic run, resume, and unsupported engine reference.
- [ ] Step 2: Run focused tests and confirm red.
- [ ] Step 3: Implement adapter using only lifecycle protocol of existing harness_engine.engines.fake.FakeEngine; if optional package import is unavailable, use an internal deterministic fallback with the same protocol.
- [ ] Step 4: Implement tool-bundle behavior: only echo in the allowed intersection may create a Tool event; undeclared tools yield POLICY_DENIED.
- [ ] Step 5: Run focused tests and confirm green.

### Task 5: Trace、Session、Run 和端到端应用服务

Files:
- Create: backend/src/agent_platform/domain/observability.py
- Create: backend/src/agent_platform/application/control_plane.py
- Test: backend/tests/test_control_plane_service.py

Interfaces:
- SpanType(SESSION, RUN, SKILL, TOOL, RUNTIME, MODEL, RESULT)
- TraceSpan(span_id, trace_id, parent_span_id, run_id, span_type, status, attributes)
- UsageRecord(input_tokens, output_tokens, total_tokens, source, estimated)
- SessionRecord, RunRecord
- ControlPlaneService.create_session(identity, deployment_id)
- ControlPlaneService.create_run(identity, session_id, prompt, idempotency_key)
- ControlPlaneService.get_trace(identity, run_id)

- [ ] Step 1: Write failing service tests for snapshot immutability, Session/Run span parentage, Tool policy intersection, and idempotent replay/conflict.
- [ ] Step 2: Run focused tests and confirm red.
- [ ] Step 3: Implement in-memory Session/Run/Trace repositories and synchronous orchestration: resolve deployment -> copy snapshot -> create runtime session -> run -> persist spans/usage/result.
- [ ] Step 4: Implement tenant-scoped lookup returning RESOURCE_NOT_FOUND for cross-tenant IDs.
- [ ] Step 5: Run focused tests and confirm green.

### Task 6: FastAPI 管理与执行接口

Files:
- Modify: backend/src/agent_platform/api/dependencies.py
- Modify: backend/src/agent_platform/api/routes.py
- Modify: backend/src/agent_platform/api/app.py
- Create: backend/tests/test_control_plane_api.py

Interfaces:
- Pydantic request/response schemas in api/schemas.py.
- Endpoints: POST /api/v1/tools, POST /api/v1/tools/{id}/publish, POST /api/v1/skills, POST /api/v1/skills/{id}/publish, POST /api/v1/digital-employees, POST /api/v1/digital-employees/{id}/versions, POST /api/v1/digital-employees/{id}/versions/{version}/publish, POST /api/v1/deployments, GET /api/v1/deployments, POST /api/v1/sessions, POST /api/v1/sessions/{id}/runs, GET /api/v1/runs/{id}, GET /api/v1/runs/{id}/trace, GET /api/v1/sessions/{id}/trace.
- Error envelope: {code, message, request_id, details}.

- [ ] Step 1: Write failing API tests for the complete happy path, missing identity, cross-tenant access, publish dependency error and idempotency conflict.
- [ ] Step 2: Run pytest backend/tests/test_control_plane_api.py -q; expected red.
- [ ] Step 3: Implement one application service instance per app with deterministic seed; map domain errors to stable HTTP statuses.
- [ ] Step 4: Ensure request body tenant_id cannot override IdentityContext.tenant_id.
- [ ] Step 5: Run API tests and existing backend tests.

### Task 7: 前端 Trace 查询与中文闭环文档

Files:
- Modify: frontend/src/api/client.ts
- Create: frontend/src/features/runs/RunTrace.tsx
- Modify: frontend/src/App.tsx
- Create: frontend/src/features/runs/RunTrace.test.tsx
- Create: docs/数字员工最小闭环.md

Interfaces:
- getRunTrace(runId: string): Promise<RunTrace>
- RunTracePage renders status, result, snapshot and ordered spans; query param runId.
- Route: /workspace/runs/:runId/trace.

- [ ] Step 1: Write failing component/API tests for loading, error, empty spans and successful timeline render.
- [ ] Step 2: Run npm run test -- --run frontend/src/features/runs/RunTrace.test.tsx; expected red.
- [ ] Step 3: Implement typed client and Ant Design timeline with Chinese labels; preserve existing Gallery route.
- [ ] Step 4: Add Chinese architecture/data-model/sequence/state diagrams, verified/unverified list and commands to docs/数字员工最小闭环.md.
- [ ] Step 5: Run frontend test and build.

### Task 8: OpenSpec 任务同步与最终验证

Files:
- Modify: openspec/changes/agent-platform-foundation/tasks.md
- Modify: docs/接口与状态流程.md
- Modify: docs/implementation-plan.md

- [ ] Step 1: Mark only completed tasks after evidence exists.
- [ ] Step 2: Run openspec instructions apply --change agent-platform-foundation --json and confirm progress reflects checked items.
- [ ] Step 3: Run complete backend tests, frontend tests/build, package import smoke checks, and openspec validate --change agent-platform-foundation --strict.
- [ ] Step 4: Review staged files for secrets, caches, .agents/, and unrelated harness changes.
- [ ] Step 5: Commit with Chinese message and body containing changes, impact, verification, risks and follow-up.

