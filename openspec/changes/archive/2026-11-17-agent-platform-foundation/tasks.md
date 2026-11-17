# Tasks

## 1. Backend foundation

- [x] 1.1 Create the independent `backend/` Python project with `api`, `application`, `domain`, `ports`, `adapters`, `infrastructure`, and `workers` packages; verify the expected directory tree and package import smoke check.
- [x] 1.2 Add the one-way backend dependency on `harness-engine` without making `harness-engine` import platform modules; verify dependency direction with an import test or static check.
- [x] 1.3 Define shared platform identifiers, TenantContext, request metadata, error categories, and resource references; verify invalid blank identifiers and cross-scope references are rejected by unit tests.
- [x] 1.4 Document the backend directory responsibilities and dependency rules in `docs/backend-architecture.md`; verify every top-level backend package has an explicit responsibility.
- [x] 1.5 Create Chinese architecture, data-model, API, runtime-flow, state, deployment, and documentation-convention diagrams for the first backend/frontend slice; verify each document links to the implemented package and its test command.
- [x] 1.6 Add root project collaboration rules for Chinese commits, Chinese code comments, staged-file hygiene, and documentation traceability; verify the rules are readable from `AGENTS.md` and `docs/文档维护约定.md`.

## 2. Fake identity and authorization

- [x] 2.1 Define the IdentityProvider, TenantDirectory, and AuthorizationProvider ports using platform, tenant, user, role, scope, membership, and status fields aligned with Dadi; verify the ports have no Dadi Java imports.
- [x] 2.2 Add immutable Fake Platform, Tenant, User, membership, role, and Scope fixtures for at least two tenants and normal/disabled users; verify the fixture loader is deterministic.
- [x] 2.3 Implement FakeIdentityProvider resolution and authorization decisions; verify active users succeed and disabled users, unknown tenants, and cross-tenant resources fail closed.
- [x] 2.4 Add identity context and authorization API contracts; verify forged request-body or ordinary header identity fields cannot change the resolved TenantContext.

## 3. Catalogs and versioned resources

- [x] 3.1 Implement domain models and repositories for DigitalEmployee, DigitalEmployeeVersion, Deployment, SkillVersion, ToolVersion, and their lifecycle states; verify draft, publish, deprecate, and immutable-version transitions.
- [x] 3.2 Implement dependency validation for DigitalEmployeeVersion, including Skill, Tool, ExecutionProfile, ModelRoute, workspace, and policy references; verify invalid dependencies block publish with structured errors.
- [x] 3.3 Implement tenant-scoped CRUD and publish application services; verify every read/write accepts TenantContext and rejects cross-tenant access.
- [x] 3.4 Add version and deployment API request/response schemas; verify published versions can be deployed, rolled back, and resolved without changing active Run snapshots.
- [x] 3.5 Document digital employee, Skill, Tool, version, and deployment lifecycle diagrams in `docs/catalog-and-versioning.md`; verify the documented transitions match the domain tests.
- [x] 3.6 Define instruction, tool-bundle, and workflow Skill types with trigger rules, input/output schemas, tool declarations, steps, memory policy, and approval policy; verify each type has a valid published representation.
- [x] 3.7 Implement AgentResolver and SkillResolver to select a Skill from a DigitalEmployeeVersion and create an immutable Agent Run snapshot; verify a version change during a Run does not change its selected Skill or tools.
- [x] 3.8 Implement SkillExecutor and SkillExecution records for instruction, tool-bundle, and workflow skills; verify input/output validation, policy intersection, step state, and failure propagation.
- [x] 3.9 Use `docs/staffdeck-reference/domain-model.md` as a behavior fixture to implement the project-owned DigitalEmployeeVersion, CapabilityBinding, CompositionSnapshot, Session, Turn, and Trace relationships; verify no StaffDeck source or static asset is imported.
- [x] 3.10 Document the DigitalEmployee and CapabilityBinding lifecycle in Chinese with architecture, data-model, publish, deploy, and rollback diagrams; verify the diagrams match domain transitions.

## 4. Execution profiles and engine adapters

- [x] 4.1 Implement ExecutionProfile and ExecutionProfileBinding models for engine, model route, workspace, tool, approval, sandbox, and context policies; verify profile versions are immutable after publish.
- [x] 4.2 Implement PolicyResolver with User > Tenant > Platform > System precedence and resolution provenance; verify each scope override and fallback path with unit tests.
- [x] 4.3 Define the RuntimeAdapter port for capabilities, lifecycle, events, cancellation, Usage, and opaque session/thread/workspace/result references; verify unsupported capabilities return explicit errors.
- [x] 4.4 Register the existing DeepSeek and Fake runtimes through the backend RuntimeAdapter port without reimplementing their Agent Loop or private state; verify existing harness tests remain independent of backend imports.
- [x] 4.5 Define Codex app-server, Codex exec, Hermes, and Generic CLI adapter contracts with safe process arguments, timeout, stdout/stderr, and exit-state semantics; verify malformed commands and unauthorized working directories are rejected without starting a process.
- [x] 4.6 Add engine capability and integration documentation in `docs/执行引擎适配设计.md`; verify each supported engine has a lifecycle, capability, error, and recovery section.

## 5. Model routes and providers

- [x] 5.1 Implement ModelRoute, ModelProfile, SecretRef, and provider-neutral model request/response/usage types; verify secrets never appear in repr, JSON responses, audit payloads, or persisted snapshots.
- [x] 5.2 Implement DirectModelProvider with the simplest configured endpoint path; verify a fake provider can return text, usage, provider request id, and model errors.
- [x] 5.3 Define adapter contracts for future new-api and Dadi Model Gateway providers without adding a platform-owned compatibility matrix; verify provider selection changes only new Runs.
- [x] 5.4 Implement exact versus estimated Usage normalization and pricing snapshot references; verify missing upstream usage is marked `ESTIMATED` with an estimation method.
- [x] 5.5 Document direct, new-api, and Dadi model route flows in `docs/model-routes.md`; verify the document states credential, usage, error, and rollback behavior.

## 6. Conversation, channels, and routing

- [x] 6.1 Implement Conversation, Participant, AgentBinding, Message, InboundEvent, and OutboundDelivery domain models; verify a Conversation is independent from Session and can bind multiple Agent Sessions.
- [x] 6.2 Implement FakeChannelAdapter for Webhook receive, message normalization, send, and delivery status; verify repeated external message ids are idempotent.
- [x] 6.3 Implement MentionParser, CommandParser, RuleMatcher, and ResponseRouter; verify ordinary group messages create no Run and valid mention/command/rule triggers create only the intended Runs.
- [x] 6.4 Add source, routing depth, and loop-prevention fields; verify Agent-to-Agent messages do not recursively trigger Runs without an explicit rule.
- [x] 6.5 Implement outbound delivery retry records and failure states; verify retries do not create duplicate business Runs or duplicate logical messages.
- [x] 6.6 Document Conversation/Session/Run and IM ingress/egress flows in `docs/conversations-and-channels.md`; verify the diagrams match routing and delivery tests.
- [x] 6.7 Implement the project-owned Gallery, Agent Dashboard, Skill List, and Chat Workspace interaction contracts from `docs/staffdeck-reference/interaction-spec.md`; verify routes and state transitions with API contract tests.
- [x] 6.8 Document the frontend route map, page information architecture, API query flow, loading/error/empty states, and component boundaries in Chinese; verify the Gallery page links to its test and API client.

## 7. Context and memory

- [x] 7.1 Implement BusinessContextPolicy, ContextSource, ContextSnapshot, and BusinessContextAssembler ports; verify snapshots record platform-injected references and estimated input Token counts.
- [x] 7.2 Implement business-context budget enforcement, recent-message selection, Skill/tool-result references, and Runtime handoff metadata; verify platform does not duplicate Runtime-owned compaction.
- [x] 7.3 Implement EnterpriseMemoryItem, MemoryReader, MemoryWriter, and scoped MemoryStore ports for Conversation, User, Project, Tenant, and Agent memory; verify cross-tenant and unauthorized scope reads fail.
- [x] 7.4 Implement explicit enterprise-memory extraction, retention, and optional runtime_memory_ref metadata; verify ordinary conversation text is not automatically persisted as long-term memory and Runtime private memory is not parsed.
- [x] 7.5 Document context assembly and memory read/write flows in `docs/context-and-memory.md`; verify each memory scope and retention rule is represented.
- [x] 7.6 Connect SkillExecution to BusinessContextAssembler so Agent and Skill instructions, recent messages, enterprise memory, and tool-result references are assembled in a declared order; verify ContextSnapshot records all included platform references before Runtime handoff.
- [x] 7.7 Add an end-to-end example for a log-analysis Agent and a code-development Agent; verify each example resolves its Engine, Skills, Tools, Workspace, Context, and Approval policies before creating a Run.

## 8. Workspaces and sandboxes

- [x] 8.1 Define WorkspaceProfile, WorkspaceRef, SandboxProfile, SandboxSpec, SecretProvider, and RuntimeEnvironmentRef ports; verify profile validation rejects unauthorized host paths, devices, privileges, and long-lived credentials.
- [x] 8.2 Implement FakeWorkspaceProvider and FakeSandboxProvider for lifecycle, lease, policy, and opaque-reference flows; verify no-files, ephemeral-workspace, and persistent-workspace modes without implementing Runtime shell behavior.
- [x] 8.3 Add Git checkout and artifact/diff result contracts for code agents; verify a run is bound to a specific repository and commit and cannot write the main branch by default.
- [x] 8.4 Add read-only log-query and approval-gated operations capability examples; verify read actions succeed and restart/delete/configure actions require policy approval.
- [x] 8.5 Document code, log, and operations Agent environments in `docs/workspaces-and-sandboxes.md`; verify each environment mode has platform lifecycle, credential, quota, cleanup, and Runtime handoff behavior.
- [x] 8.6 Define the ObjectStore and StorageProfile ports with local, S3, MinIO, OSS, and COS provider configurations; verify provider selection is explicit and unsupported providers fail without local fallback.
- [x] 8.7 Implement LocalObjectStore for development with tenant-safe generated keys, upload intents, bounded reads, and delete/GC operations; verify clients never receive arbitrary host paths.
- [x] 8.8 Add external object-storage adapter contracts and configuration examples for S3, MinIO, OSS, and COS; verify credentials are represented only by SecretRef and signed URL TTL is enforced.
- [x] 8.9 Document media/object storage and processing flows in `docs/对象存储与媒体处理设计.md`; verify lifecycle, configuration, tenant isolation, retention, and recovery diagrams are present.
- [x] 8.10 Add MessageAttachment and RuntimeAttachmentRef contracts; verify Codex/DeepSeek Runtime uses platform MediaAsset references while Runtime Workspace remains an execution copy.
- [x] 8.11 Add EPHEMERAL, SESSION, CONVERSATION, and KNOWLEDGE_BASE retention policies; verify conversation attachments can be reused across later Runs only after tenant authorization.

## 9. Trace, audit, and usage accounting

- [x] 9.1 Define TraceId, Span, RunTimeline, AuditEvent, ModelCallRecord, ToolInvocationRecord, UsageLedger, and RuntimeReference types; verify parent/child correlation for Session, Run, BusinessContext, EnterpriseMemory, Model, Tool, Approval, Sandbox, Channel, and Runtime spans.
- [x] 9.2 Implement trace and usage repositories with platform/tenant scope and immutable completed usage records; verify cross-tenant queries are rejected and adjustment records preserve originals.
- [x] 9.3 Implement model usage normalization for input, output, cached, reasoning, total Tokens, cost, currency, pricing version, exact/estimated status, and provider request id; verify aggregations by tenant, user, Agent, Session, Run, project, and channel.
- [x] 9.4 Implement Runtime event normalization and tool call logging with protocol, version, input/output references, approval, duration, retries, and error category; verify missing private Runtime events are represented as unavailable rather than fabricated.
- [x] 9.5 Implement raw payload policy and encrypted object reference contracts; verify disabled policy stores metadata only and enabled policy stores tenant-scoped references, hashes, sizes, and retention metadata.
- [x] 9.6 Add run timeline and usage API contracts; verify a completed Run can be rendered as an ordered Session-to-Model/Tool timeline with Token and cost totals.
- [x] 9.7 Document observability, raw payload retention, and Token/cost accounting in `docs/execution-observability.md` and `docs/usage-accounting.md`; verify the storage split is explicit.

## 10. Persistence and end-to-end integration

- [x] 10.1 Add MySQL migrations or migration design for catalog, profile, route, conversation, memory, trace, usage, and workspace-reference metadata using existing platform/tenant composite boundaries; verify migrations are ordered and reversible at the deployment level.
- [x] 10.2 Connect Application Services to existing Session/Run persistence and fencing-token behavior; verify each Run stores DigitalEmployeeVersion, ExecutionProfile, ModelRoute, BusinessContextSnapshot, Runtime references, trace_id, and result references.
- [x] 10.3 Implement a Fake end-to-end flow from API request through identity, deployment resolution, business-context assembly, Fake Runtime Adapter, tool call, trace, usage, message, and delivery; verify the full Run timeline is queryable without relying on vendor-private state.
- [x] 10.4 Add API, persistence, routing, memory, engine, and observability contract test suites; verify the Fake suite runs without external credentials or production services.
- [x] 10.5 Add a staged integration checklist for real DeepSeek, Codex, new-api, Dadi, IM, object storage, and Kubernetes adapters; verify each adapter can be enabled or disabled independently.
- [x] 10.6 Keep the StaffDeck black-box evidence and behavior reference under `docs/staffdeck-reference/`; verify the reconstruction ledger records observations, inferences, unknowns, and the no-code-reuse boundary.
- [x] 10.7 Run a documentation completeness review for all implemented tasks; verify every changed module has a Chinese document, diagram, test command, status, and known-limitations section.

## Workflow follow-up

- Review the generated proposal, design, specifications, and tasks with the user.
- Use `openspec-apply-change` to implement the tracked tasks after the design review.
- Run strict OpenSpec validation before implementation handoff and archive the change only after implementation and verification are complete.
