# Conversation Content Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist tenant-scoped chat messages and resumable streaming output while keeping message bodies out of the MySQL control-plane tables.

**Architecture:** Add a V002 MySQL message index and chunk index, plus a tenant-aware content-store protocol for encrypted message objects. Persist the user message before queuing a run; persist assistant text as one object for non-streaming runs or immutable checkpoints plus a manifest for streaming runs. Record auditable tool/run actions without storing private model reasoning or raw sensitive payloads.

**Tech Stack:** Python 3.12+, existing `mysql-connector-python` DB-API repositories, MySQL 8.4/InnoDB, existing `HarnessEngine` and lifecycle service, provider-neutral object-store protocol, pytest/Ruff.

**Spec:** `docs/superpowers/specs/2026-11-13-conversation-content-persistence-design.md`

## Global Constraints

- Every message and chunk lookup/write/delete receives trusted `TenantContext` and filters both `platform_id` and `tenant_id`.
- V001 remains immutable; add a V002 migration for messages and chunks.
- MySQL stores message metadata and opaque content references, not prompt or response bodies.
- Message bodies and session-scoped audit metadata remain until explicit deletion; no TTL deletion by default.
- Persist only user-visible response deltas; never persist raw internal reasoning or hidden reasoning tokens.
- Stream checkpoints flush at 32 KiB or 2 seconds, whichever occurs first.
- Reconnecting clients replay committed checkpoints; after worker loss show the committed partial output and mark the run `UNKNOWN`/message `PARTIAL`; do not automatically resubmit a model request.
- Reserve every object ref in MySQL before writing object bytes. Publishing a ref and expiring its upload intent lock the same row; no expired upload may publish.
- Upload intents are renewable while a write is active. An expired intent is marked `EXPIRED` and queued for deletion in one transaction before an object delete can run.
- Orphan scans include `runs.result_ref` as a live root and drain old writers before listing-based deletion.
- Stream checkpoints form a contiguous prefix; reconnect tails the same durable MySQL chunk log, with notifications used only as wake-up hints.
- Tenant-scoped orphan scans consider only objects older than 24 hours by default; deployments may configure a longer grace period.
- Keep the content-store protocol vendor-neutral; production object store and KMS are deployment choices.

## Review Focus

- Cross-platform or cross-tenant reads, writes, deletes, and chunk resumes must fail as not found; pin with repository and MySQL integration tests for every operation.
- Duplicate idempotency keys must return the original user message/run without adding duplicate messages; pin with session-service tests.
- Out-of-order or duplicate stream checkpoints must not corrupt replay order; pin with checkpoint repository tests requiring `chunk_sequence = last_committed_chunk_sequence + 1`.
- Reconnect racing with a chunk commit must not miss the committed chunk; pin with a handoff race test against the durable chunk tail.
- Object upload success followed by DB rollback must leave no readable DB reference and must be reclaimed by orphan cleanup; pin with failure-injection and upload-intent expiry race tests.
- A delayed object publisher racing with cleanup must have exactly one winner; pin with a barrier-controlled expiry/publication test.
- A PUT that finishes after its expired intent was cleaned must be rediscovered after the orphan grace period and requeued even if its prior cleanup row is `DELETED`.
- Orphan scans must preserve legacy `runs.result_ref` objects; pin with an upgrade-case test.
- Session deletion and retention must delete message indexes, chunks, manifests, and session-scoped audit rows idempotently; pin with deletion tests and retry-after-failure coverage.
- Hidden reasoning and secrets in vendor events/tool payloads must never enter transcript or audit storage; pin with adapter and audit redaction tests.

---

### Task 1: Add Message and Stream-Chunk Schema

**Files:**
- Create: `docs/migrations/V002__conversation_messages.sql`
- Modify: `docs/mysql-schema.md`
- Modify: `harness-engine/src/harness_engine/doctor.py`
- Test: `harness-engine/tests/test_mysql_integration.py`
- Test: `harness-engine/tests/test_doctor.py`

**Interfaces:**
- Consumes: V001 `sessions`, `runs`, and `audit_events` composite keys.
- Produces: `sessions.last_message_sequence` plus `messages`, `message_chunks`, `content_upload_intents`, and `content_gc_queue` with tenant-scoped composite keys and foreign keys.

- [x] **Step 1: Add schema and doctor acceptance assertions**
  - Probe representative V002 message/chunk columns, reject a zero message sequence, and reject a message/session cross-tenant FK.
  - Assert doctor recognizes schema version 2 and the V002 migration file without automatically applying a migration.
- [x] **Step 2: Run focused tests and prepare MySQL 8.4 validation**
  - RED was observed for the missing-V002 doctor check and schema-version-1 health check; focused tests passed after implementation.
  - The V002 SQL draft already existed, so validate it by executing V001 followed by V002 in a disposable MySQL 8.4 instance; do not edit V001.
- [x] **Step 3: Validate the V002 migration draft**
  - V002 adds `sessions.last_message_sequence`, `messages`, `message_chunks`, `content_upload_intents`, and `content_gc_queue` with tenant-scoped composite keys and constraints.
  - Repository-only sequence allocation, contiguous chunk advancement, and upload lease fencing are verified with their consuming repository tasks below, not as migration-only behavior.
- [x] **Step 4: Document the V002 schema and deletion rules**
  - Update `docs/mysql-schema.md` with metadata-only body storage, statuses, content refs, chunk ordering, explicit-delete retention, and migration order.
- [x] **Step 5: Run the targeted MySQL integration test**
  - Verified V001 followed by V002 on an ephemeral MySQL 8.4.13 container; schema version was 2 and the V002 representative-column, CHECK, and tenant-FK integration test passed (1 passed).

### Task 2: Define Tenant-Aware Content Storage

**Files:**
- Create: `harness-engine/src/harness_engine/persistence/content_store.py`
- Create: `harness-engine/tests/test_content_store.py`
- Modify: `harness-engine/src/harness_engine/persistence/__init__.py`

**Interfaces:**
- Consumes: opaque tenant/session/message IDs from the application layer.
- Produces: `ContentRef = NewType("ContentRef", str)`; `ContentObjectInfo(ref: ContentRef, created_at: datetime, byte_length: int, content_type: str)`; `ContentStore.put(tenant, ref: ContentRef, data: bytes, *, content_type: str) -> None`, `get(tenant, ref: ContentRef) -> bytes`, idempotent `delete(tenant, ref: ContentRef) -> None`, and paginated `list_candidates(tenant, older_than: datetime, cursor: str | None = None) -> Page[ContentObjectInfo]`.

- [x] **Step 1: Write tests for tenant scoping, integrity, and idempotent delete**
  - Verify another tenant cannot read or delete an object.
  - Verify read checks the stored SHA-256 and rejects corruption.
  - Verify deleting the same object twice succeeds.
- [x] **Step 2: Run the content-store tests and confirm they fail**
- [x] **Step 3: Implement the protocol and local development adapter**
  - Accept only refs created by `ContentUploadRepository`; reject client paths and path traversal.
  - Keep encryption behind the storage adapter/KMS integration boundary; production access must use TLS and encryption at rest.
  - Scope object enumeration to the tenant namespace; set the default orphan grace period to 24 hours.
- [x] **Step 4: Add tests for failed writes and absent objects**
  - Ensure a failed put returns no reference and a missing object is reported as a stable storage error.
- [x] **Step 5: Run the focused tests and Ruff**
  - Verified 25 ContentStore tests and 117 full-suite tests passed (2 skipped); Ruff passed on the ContentStore source, package exports, and tests. Whole-repository Ruff remains non-clean with 23 findings outside the Task 2 file set.

### Task 3: Persist User and Non-Streaming Assistant Messages

**Files:**
- Modify: `harness-engine/src/harness_engine/persistence/models.py`
- Modify: `harness-engine/src/harness_engine/persistence/protocols.py`
- Modify: `harness-engine/src/harness_engine/persistence/mysql.py`
- Modify: `harness-engine/src/harness_engine/session_service.py`
- Modify: `harness-engine/tests/test_session_service.py`
- Modify: `harness-engine/tests/test_mysql_repository.py`
- Modify: `harness-engine/tests/test_mysql_integration.py`

**Interfaces:**
- Consumes: `ContentStore` and Task 1 message repository records.
- Produces: `UploadIntent(upload_id, object_ref, object_type, session_id, message_id, run_id, status, lease_expires_at)`; tenant-scoped `ContentUploadRepository.reserve(tenant, object_type, *, session_id: str | None, message_id: str | None, run_id: str | None, lease_seconds: int) -> UploadIntent`, `renew(tenant, upload_id, *, lease_seconds: int) -> UploadIntent`, and `claim_expired(tenant, *, limit: int) -> tuple[UploadIntent, ...]`; tenant-scoped `MessageRepository.create/get/list/complete/delete` and transactionally fenced upload publication; service behavior returns persisted message IDs with each submitted run.

- [x] **Step 1: Add tests for submission and response persistence**
  - Submit one prompt and assert one `USER/COMPLETE` row and one queued run.
  - Complete one run and assert one `ASSISTANT/COMPLETE` row whose `content_ref` equals `runs.result_ref`.
  - Retry with the same idempotency key and digest; assert no duplicate message or run.
  - Retry with the same key and a different digest; assert idempotency conflict and no new message.
- [x] **Step 2: Run the focused tests and confirm they fail**
- [x] **Step 3: Implement message records, tenant-scoped SQL, and service integration**
  - Reserve user content ref before upload; publish the upload intent together with message/run rows only while the lease is active.
  - Reserve assistant output ref before upload; publish the intent together with message completion, run terminal state, result ref, and audit event in MySQL.
  - On DB failure, do not expose a reference; leave the unreferenced object for grace-period cleanup.
- [x] **Step 4: Add failure-injection tests for object/DB commit ordering**
  - Verify no row points to absent content and orphan cleanup is retryable.
- [x] **Step 5: Run the focused offline repository/service tests**
  - Focused repository/service tests: 49 passed; full offline suite: 130 passed, 2 skipped.
  - Disposable MySQL 8.4.13 integration suite: 5 passed; V002 remained unchanged.

### Task 4: Persist and Replay Streaming Checkpoints

**Files:**
- Modify: `harness-engine/src/harness_engine/core.py`
- Modify: `harness-engine/src/harness_engine/engines/deepseek.py`
- Modify: `harness-engine/src/harness_engine/session_service.py`
- Modify: `harness-engine/src/harness_engine/persistence/mysql.py`
- Create: `harness-engine/tests/test_stream_persistence.py`
- Modify: `harness-engine/tests/test_deepseek_engine.py`

**Interfaces:**
- Consumes: the committed message/chunk repository and content-store interfaces.
- Produces: an engine-neutral ordered visible-text delta event and a service durable-tail API accepting `last_chunk_sequence`.

- [x] **Step 1: Add tests for ordered deltas, batching, replay, and partial termination**
  - Flush at 32 KiB or 2 seconds, whichever comes first.
  - Reconnect from sequence N tails only the durable chunk log after N; notifications only wake the reader, which always queries MySQL again.
  - Duplicate sequence numbers and stale fencing tokens are rejected.
  - A chunk committed between an empty replay query and a notification wait is still returned by the next durable-tail query.
  - Any checkpoint whose sequence skips the previous committed sequence is rejected and leaves the cursor unchanged.
  - Worker loss leaves the last committed chunk readable and marks message `PARTIAL` and run `UNKNOWN`.
  - Reasoning-only events and unapproved vendor payload fields never become visible deltas.
- [x] **Step 2: Run the focused stream tests and confirm they fail**
- [x] **Step 3: Extend the engine-neutral event contract and expose adapter streaming only where supported**
  - Do not serialize raw vendor event objects.
  - DeepSeek's pinned SDK has no public in-run text-delta callback; its adapter now reports `streaming=False`. Live DeepSeek streaming remains unverified.
- [x] **Step 4: Implement chunk upload, chunk-index transaction, replay cursor, and final manifest**
  - Upload each immutable object before inserting its MySQL metadata.
  - Reserve the ref before object upload; lock/validate the intent and current fencing token in the same transaction that inserts the next contiguous chunk and advances `last_committed_chunk_sequence`.
  - At completion, flush the last buffer, write the manifest, and atomically complete the message/run records.
  - Checkpoints reserve upload intents before encrypted object writes; MySQL atomically fences/publishes contiguous chunks and advances the high-water mark. Completion writes a manifest; worker loss recovers the durable prefix as `PARTIAL`/`UNKNOWN`.
- [x] **Step 5: Run focused stream and engine adapter tests**
  - Offline suite: 157 passed, 2 skipped; changed-file Ruff checks passed.
  - MySQL 8.4.13 integration after V001 + V002: 7 passed, including service-level stream completion, cursor replay, and expired-worker recovery.

### Task 5: Add Explicit Deletion and Orphan Cleanup

**Files:**
- Modify: `harness-engine/src/harness_engine/persistence/protocols.py`
- Modify: `harness-engine/src/harness_engine/persistence/mysql.py`
- Modify: `harness-engine/src/harness_engine/session_service.py`
- Create: `harness-engine/tests/test_message_deletion.py`

**Interfaces:**
- Consumes: content refs and tenant-scoped session/message repositories.
- Produces: idempotent `delete_session_history(tenant, session_id) -> DeleteResult` and a cleanup operation for unreferenced tenant objects older than 24 hours by default.

- [x] **Step 1: Add tests for explicit delete, repeated delete, and interrupted cleanup**
  - Delete message rows, chunks, manifests, and session-scoped audit metadata.
  - Queue `runs.result_ref`, all session/workspace refs, and every associated upload intent ref including `UPLOADING`; expire associated intents in the same DB transaction.
  - Immediately prevent application reads when deletion is accepted.
  - Retry after object-store failure and complete cleanup without restoring access.
  - Verify a delayed publisher cannot publish an intent after the cleaner has expired it.
  - Let an object PUT finish after the first cleanup reached `DELETED`; verify enumeration requeues that object after the grace period and a second delete removes it.
- [x] **Step 2: Run the deletion tests and confirm they fail**
- [x] **Step 3: Implement deletion queue/status and retryable object cleanup**
  - Do not time-expire content or audit metadata by default.
  - Keep database/object-store backups under the configured deployment backup lifecycle.
- [x] **Step 4: Add tests for tenant mismatch and orphan grace-period cleanup**
- [x] **Step 5: Run the focused deletion tests**
  - Offline suite: 162 passed, 4 skipped; MySQL 8.4.13 deletion and GC suite: 14 passed.

### Task 6: Update Project Plan and Verification Documentation

**Files:**
- Modify: `docs/implementation-plan.md`
- Modify: `docs/verification-matrix.md`
- Modify: `docs/real-validation-runbook.md`
- Modify: `harness-engine/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the feature behavior and migration order from Tasks 1–5.
- Produces: explicit implementation status, operational steps, and a validation gate before transcript persistence is called complete.

- [x] **Step 1: Add the conversation persistence phase and blockers to implementation-plan.md**
- [x] **Step 2: Add offline, MySQL 8.4, object-store, stream-recovery, tenant-isolation, deletion, and redaction cases to verification-matrix.md**
- [x] **Step 3: Add migration, object-store configuration, durable-tail race, restore, deletion, and failure-recovery steps to real-validation-runbook.md**
- [x] **Step 4: Add concise message-history and storage-boundary usage docs to both READMEs**
- [x] **Step 5: Check every documented command, status, and migration filename against the repository**
  - Verified V001/V002 filenames, current test counts, Docker/Kubernetes preflight commands, and the `harness-engine/.artifacts` local configuration path.

### Task 7: Full Feature Verification

**Files:**
- Test: `harness-engine/tests/`
- Verify: `docs/migrations/V001__core_schema.sql` and `docs/migrations/V002__conversation_messages.sql`

**Interfaces:**
- Consumes: all tasks above.
- Produces: a recorded verification result separating offline, MySQL 8.4, object-store, and live SDK streaming evidence.

- [x] **Step 1: Run the offline pytest suite and Ruff checks**
  - Offline suite: 162 passed, 4 skipped; changed-file Ruff checks passed. Full-repository Ruff retains 11 pre-existing findings outside the Task 5 change set.
- [x] **Step 2: Run V001 + V002 and MySQL integration tests on MySQL 8.4**
  - Clean MySQL 8.4.13 instance with V001 then V002: 14 integration tests passed.
- [x] **Step 3: Run object-store failure, orphan cleanup, and deletion integration checks**
  - Included in the offline suite and the 14-test MySQL deletion/GC integration run.
- [ ] **Step 4: Run live SDK streaming, reconnect replay, and worker-loss recovery checks in the target environment**
- [x] **Step 5: Update verification-matrix.md with exact versions, commands, logs, and passed/skipped/unverified outcomes**
- [x] **Step 6: Keep the streaming capability marked unverified if upstream live delta/resume behavior cannot be demonstrated**
