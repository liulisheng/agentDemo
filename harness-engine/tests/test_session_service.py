from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import (
    CancelStatus,
    EngineCapabilities,
    EngineRequest,
    SessionMismatchError,
    SessionRef,
    TenantContext,
    UnsupportedCapabilityError,
)
from harness_engine.engines.fake import FakeEngine
from harness_engine.persistence.errors import (
    AuthorizationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    LeaseLostError,
    ResourceNotFoundError,
    SessionCreationError,
)
from harness_engine.persistence.models import (
    Lease,
    MessageStatus,
    Page,
    RunState,
    SessionStatus,
    canonical_request_digest,
)
from harness_engine.session_service import PersistentSessionService

USER = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="user-1",
    roles=("member",),
    auth_source="oidc",
)
WORKER = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="worker-1",
    roles=("worker",),
    auth_source="worker",
)
SYSTEM = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="system-reconciler",
    roles=("system",),
    auth_source="system",
)
VIEWER = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="viewer-1",
    roles=("viewer",),
    auth_source="oidc",
)
OWNER = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="owner-1",
    roles=("owner",),
    auth_source="oidc",
)


class MemorySessionRepository:
    def __init__(self):
        self.records = {}

    def create(self, tenant, record):
        if record.tenant.scope != tenant.scope:
            raise ValueError("session record tenant mismatch")
        self.records[record.control_session_id] = record
        return record

    def get(self, tenant, control_session_id):
        record = self.records.get(control_session_id)
        if record is None or record.tenant.platform_id != tenant.platform_id or record.tenant.tenant_id != tenant.tenant_id:
            raise ResourceNotFoundError("session not found")
        return replace(record, tenant=tenant)

    def list(self, tenant, *, limit, cursor=None):
        records = tuple(
            replace(record, tenant=tenant)
            for record in self.records.values()
            if record.tenant.scope == tenant.scope
        )
        return Page(items=records[:limit])

    def update_status(self, tenant, control_session_id, status):
        record = self.get(tenant, control_session_id)
        updated = replace(record, status=status, tenant=tenant, updated_at=datetime.now(UTC))
        self.records[control_session_id] = updated
        return updated


class MemoryRunRepository:
    def __init__(self):
        self.records = {}
        self.claims = {}

    def create_or_get(self, tenant, record):
        if record.tenant.scope != tenant.scope:
            raise ValueError("run record tenant mismatch")
        existing = next(
            (
                value
                for value in self.records.values()
                if value.tenant.scope == record.tenant.scope
                and value.idempotency_key == record.idempotency_key
            ),
            None,
        )
        if existing is not None:
            if existing.request_digest != record.request_digest:
                raise IdempotencyConflictError(
                    record.idempotency_key,
                    expected_digest=existing.request_digest,
                    received_digest=record.request_digest,
                )
            return existing
        self.records[record.run_id] = record
        return record

    def get(self, tenant, run_id):
        record = self.records.get(run_id)
        if record is None or record.tenant.scope != tenant.scope:
            raise ResourceNotFoundError("run not found")
        return replace(record, tenant=tenant)

    def list(self, tenant, *, session_id=None, limit, cursor=None):
        records = tuple(
            replace(record, tenant=tenant)
            for record in self.records.values()
            if record.tenant.scope == tenant.scope
            and (session_id is None or record.session_id == session_id)
        )
        return Page(items=records[:limit])

    def claim(self, tenant, run_id, worker_id, *, lease_seconds=30):
        record = self.get(tenant, run_id)
        if record.state is not RunState.QUEUED:
            raise LeaseLostError("run is not queued")
        token = record.fencing_token + 1
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.records[run_id] = replace(
            record,
            state=RunState.STARTING,
            worker_id=worker_id,
            fencing_token=token,
            lease_expires_at=expires,
        )
        self.claims[run_id] = token
        return Lease(run_id, worker_id, token, expires)

    def claim_next(self, tenant, worker_id, *, lease_seconds=30):
        for record in self.records.values():
            if record.tenant.scope == tenant.scope and record.state is RunState.QUEUED:
                return self.claim(tenant, record.run_id, worker_id, lease_seconds=lease_seconds)
        return None

    def request_cancel(self, tenant, run_id):
        record = self.get(tenant, run_id)
        if record.state not in {RunState.STARTING, RunState.RUNNING}:
            raise InvalidStateTransitionError("run cannot be cancelled")
        updated = replace(record, state=RunState.CANCELLING)
        self.records[run_id] = updated
        return updated

    def heartbeat(self, tenant, run_id, fencing_token, *, lease_seconds=30):
        record = self.get(tenant, run_id)
        if record.fencing_token != fencing_token:
            raise LeaseLostError("stale token")
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.records[run_id] = replace(record, lease_expires_at=expires)
        return Lease(run_id, record.worker_id or "unknown", fencing_token, expires)

    def transition(self, tenant, run_id, fencing_token, state, *, error_code=None, result_ref=None):
        record = self.get(tenant, run_id)
        if record.fencing_token != fencing_token:
            raise LeaseLostError("stale token")
        updated = replace(
            record,
            state=state,
            error_code=error_code,
            result_ref=result_ref,
            finished_at=datetime.now(UTC) if state in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.UNKNOWN} else record.finished_at,
            tenant=tenant,
        )
        self.records[run_id] = updated
        return updated

    def mark_unknown(self, tenant, run_id, fencing_token, *, error_code="UNKNOWN_RESULT"):
        return self.transition(tenant, run_id, fencing_token, RunState.UNKNOWN, error_code=error_code)

    def expire_leases(self, tenant, *, limit=100):
        expired = []
        for record in tuple(self.records.values()):
            if record.tenant.scope == tenant.scope and record.state in {
                RunState.STARTING,
                RunState.RUNNING,
                RunState.CANCELLING,
            } and record.lease_expires_at is not None and record.lease_expires_at <= datetime.now(UTC):
                updated = replace(
                    record,
                    state=RunState.UNKNOWN,
                    fencing_token=record.fencing_token + 1,
                    lease_expires_at=None,
                    finished_at=datetime.now(UTC),
                    error_code="RUN_LEASE_EXPIRED",
                    tenant=tenant,
                )
                self.records[record.run_id] = updated
                expired.append(updated)
                if len(expired) >= limit:
                    break
        return tuple(expired)


def test_create_and_resume_session_keep_control_and_native_ids_separate():
    service = PersistentSessionService(MemorySessionRepository(), MemoryRunRepository())
    engine = FakeEngine()
    engine.start()

    record = service.create_session(USER, engine)
    resumed = service.resume_session(USER, record.control_session_id, engine)

    assert record.control_session_id != record.engine_session_id
    assert resumed.engine_session_id == record.engine_session_id
    assert engine.state.value == "started"


@pytest.mark.parametrize("configured", ["messages", "uploads", "content_store"])
def test_content_persistence_requires_all_dependencies(configured):
    with pytest.raises(ValueError, match="together"):
        PersistentSessionService(
            MemorySessionRepository(), MemoryRunRepository(), **{configured: object()}
        )


class MemoryContentRepositories:
    """Offline service boundary; SQL atomicity is exercised against real MySQL."""

    def __init__(self, runs, store):
        self.runs, self.store = runs, store
        self.messages, self.intents, self.abandoned = {}, {}, []
        self.fail_publish = False

    def reserve(self, tenant, object_type, **source):
        from harness_engine.persistence.ids import generate_id
        from harness_engine.persistence.models import UploadIntent
        upload_id = generate_id()
        intent = UploadIntent(
            upload_id=upload_id, tenant=tenant, object_ref=generate_id(),
            object_type=object_type, status="UPLOADING",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            **{k: v for k, v in source.items() if k != "lease_seconds"},
        )
        self.intents[upload_id] = intent
        return intent

    def submit_run(self, tenant, run, message, *, upload_id):
        assert self.store.get(tenant, message.content_ref)
        if self.fail_publish:
            raise RuntimeError("database unavailable")
        stored = self.runs.create_or_get(tenant, run)
        if stored.run_id != run.run_id:
            self.abandoned.append(upload_id)
            return stored
        self.messages[message.message_id] = replace(message, sequence_no=1)
        self.intents[upload_id] = replace(self.intents[upload_id], status="PUBLISHED")
        stored = replace(stored, message_ids=(message.message_id,))
        self.runs.records[run.run_id] = stored
        return stored

    def create(self, tenant, message, *, upload_id=None, fencing_token=None):
        stored = replace(message, sequence_no=2)
        self.messages[message.message_id] = stored
        return stored

    def complete(self, tenant, message, *, upload_id, fencing_token):
        assert self.store.get(tenant, message.content_ref) is not None
        if self.fail_publish:
            raise RuntimeError("database unavailable")
        self.runs.transition(tenant, message.run_id, fencing_token, RunState.SUCCEEDED,
                             result_ref=message.content_ref)
        self.messages[message.message_id] = replace(message, sequence_no=2)
        self.intents[upload_id] = replace(self.intents[upload_id], status="PUBLISHED")
        return self.messages[message.message_id]

    def fail_pending_message(self, tenant, message_id, *, fencing_token,
                             terminal_state=RunState.FAILED, error_code="ENGINE_FAILED"):
        message = self.messages[message_id]
        self.runs.transition(
            tenant, message.run_id, fencing_token, terminal_state, error_code=error_code
        )
        self.messages[message_id] = replace(message, status=MessageStatus.FAILED)
        return self.messages[message_id]


def content_service(tmp_path):
    from harness_engine.persistence.content_store import LocalContentStore
    store = LocalContentStore(tmp_path, b"k" * 32)
    runs = MemoryRunRepository()
    repositories = MemoryContentRepositories(runs, store)
    service = PersistentSessionService(
        MemorySessionRepository(), runs, messages=repositories,
        uploads=repositories, content_store=store,
    )
    engine = FakeEngine()
    session = service.create_session(USER, engine)
    return service, engine, session, repositories, store


def test_user_submission_persists_encrypted_content_and_returns_message_id(tmp_path):
    service, _, session, repo, store = content_service(tmp_path)
    run = service.submit_run(USER, session.control_session_id, EngineRequest(prompt="私密 prompt"), "key")
    message = repo.messages[run.message_ids[0]]
    assert message.role.value == "USER" and message.status.value == "COMPLETE"
    assert run.state is RunState.QUEUED
    assert store.get(USER, message.content_ref).decode() == "私密 prompt"
    assert message.byte_length == len("私密 prompt".encode())
    assert "私密 prompt" not in repr(message)
    assert all(b"prompt" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_content_submission_idempotency_keeps_original_message_and_run(tmp_path):
    service, _, session, repo, _ = content_service(tmp_path)
    request = EngineRequest(prompt="private")
    first = service.submit_run(USER, session.control_session_id, request, "key")
    second = service.submit_run(USER, session.control_session_id, request, "key")
    assert first.run_id == second.run_id and first.message_ids == second.message_ids
    assert len(repo.messages) == len(repo.runs.records) == 1
    assert len(repo.abandoned) == 1
    with pytest.raises(IdempotencyConflictError):
        service.submit_run(USER, session.control_session_id, EngineRequest(prompt="changed"), "key")
    assert len(repo.messages) == len(repo.runs.records) == 1


def test_assistant_completion_persists_visible_text_and_matching_result_ref(tmp_path):
    service, engine, session, repo, store = content_service(tmp_path)
    request = EngineRequest(prompt="private")
    run = service.submit_run(USER, session.control_session_id, request, "key")
    result = service.execute_run(WORKER, run.run_id, request, engine, "worker")
    assistant = next(m for m in repo.messages.values() if m.role.value == "ASSISTANT")
    assert assistant.status.value == "COMPLETE"
    assert store.get(USER, assistant.content_ref).decode() == result.text
    assert repo.runs.get(USER, run.run_id).state is RunState.SUCCEEDED
    assert result.result_ref == repo.runs.get(USER, run.run_id).result_ref == assistant.content_ref
    service.submit_run(USER, session.control_session_id, request, "key")
    assert len(repo.messages) == 2
    with pytest.raises(LeaseLostError):
        service.execute_run(WORKER, run.run_id, request, engine, "worker")
    assert len(repo.messages) == 2


def test_upload_before_database_failure_leaves_only_unpublished_reservation(tmp_path):
    service, _, session, repo, store = content_service(tmp_path)
    repo.fail_publish = True
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.submit_run(USER, session.control_session_id, EngineRequest(prompt="private"), "key")
    assert not repo.messages and not repo.runs.records
    intent = next(iter(repo.intents.values()))
    assert intent.status == "UPLOADING"
    assert store.get(USER, intent.object_ref) == b"private"


def test_failed_object_write_cannot_publish_a_run(tmp_path):
    service, _, session, repo, _ = content_service(tmp_path)
    class FailedStore:
        def put(self, *_args, **_kwargs):
            raise OSError("object write failed")
    service.content_store = FailedStore()
    with pytest.raises(OSError, match="object write failed"):
        service.submit_run(USER, session.control_session_id, EngineRequest(prompt="private"), "key")
    assert not repo.messages and not repo.runs.records


def test_engine_and_reconciliation_errors_do_not_log_private_text(tmp_path, caplog):
    service, engine, session, repo, _ = content_service(tmp_path)
    request = EngineRequest(prompt="private prompt")
    run = service.submit_run(USER, session.control_session_id, request, "key")
    def fail_engine(_request):
        raise RuntimeError("private prompt and response in vendor exception")
    def fail_reconciliation(*_args, **_kwargs):
        raise RuntimeError("private prompt in database exception")
    engine.run = fail_engine
    repo.fail_pending_message = fail_reconciliation
    repo.runs.mark_unknown = fail_reconciliation
    with pytest.raises(RuntimeError, match="vendor exception"):
        service.execute_run(WORKER, run.run_id, request, engine, "worker")
    assert "private prompt" not in caplog.text
    assert caplog.records and all(record.exc_info is None for record in caplog.records)


def test_create_session_allocates_control_id_before_starting_engine():
    calls = []

    class OrderCheckingEngine(FakeEngine):
        def start(self):
            assert calls == ["control-id"]
            super().start()

    service = PersistentSessionService(
        MemorySessionRepository(),
        MemoryRunRepository(),
        id_factory=lambda: calls.append("control-id") or "control-id",
    )

    record = service.create_session(USER, OrderCheckingEngine())

    assert record.control_session_id == "control-id"


@pytest.mark.parametrize(
    "status",
    [SessionStatus.PAUSED, SessionStatus.INTERRUPTED, SessionStatus.CLOSED],
)
def test_inactive_session_cannot_accept_or_execute_runs(status):
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = FakeEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="must not run")
    queued = service.submit_run(USER, session.control_session_id, request, "inactive-run")
    service.update_session_status(OWNER, session.control_session_id, status)

    with pytest.raises(InvalidStateTransitionError, match="session is not active"):
        service.submit_run(USER, session.control_session_id, request, "inactive-new")
    with pytest.raises(InvalidStateTransitionError, match="session is not active"):
        service.execute_run(WORKER, queued.run_id, request, engine, "worker-1")

    assert runs.get(WORKER, queued.run_id).state is RunState.QUEUED
    assert queued.run_id not in runs.claims


def test_session_create_failure_persists_interrupted_orphan_record():
    class FailOnceSessionRepository(MemorySessionRepository):
        create_calls = 0

        def create(self, tenant, record):
            type(self).create_calls += 1
            if type(self).create_calls == 1:
                raise RuntimeError("simulated response failure")
            return super().create(tenant, record)

    FailOnceSessionRepository.create_calls = 0
    sessions = FailOnceSessionRepository()
    service = PersistentSessionService(sessions, MemoryRunRepository())
    engine = FakeEngine()
    engine.start()

    with pytest.raises(SessionCreationError) as raised:
        service.create_session(USER, engine)

    assert raised.value.orphan_recorded is True
    orphan = sessions.records[raised.value.control_session_id]
    assert orphan.status is SessionStatus.INTERRUPTED


def test_resume_starts_engine_before_loading_saved_native_session():
    class NeedsStartedEngine(FakeEngine):
        def resume_session(self, ref):
            assert self.state.value == "started"
            return ref

    service = PersistentSessionService(MemorySessionRepository(), MemoryRunRepository())
    creator = NeedsStartedEngine()
    record = service.create_session(USER, creator)
    engine = NeedsStartedEngine()

    service.resume_session(USER, record.control_session_id, engine)

    assert engine.state.value == "started"


def test_resume_rejects_engine_replacing_native_session_id():
    class ReplacingEngine(FakeEngine):
        def resume_session(self, ref):
            return SessionRef("replacement-native-id", ref.engine_name)

    sessions = MemorySessionRepository()
    service = PersistentSessionService(sessions, MemoryRunRepository())
    creator = FakeEngine()
    creator.start()
    record = service.create_session(OWNER, creator)
    sessions.update_status(OWNER, record.control_session_id, SessionStatus.INTERRUPTED)

    with pytest.raises(SessionMismatchError, match="different native session"):
        service.resume_session(OWNER, record.control_session_id, ReplacingEngine())

    persisted = sessions.get(OWNER, record.control_session_id)
    assert persisted.engine_session_id == record.engine_session_id
    assert persisted.status is SessionStatus.INTERRUPTED


def test_submit_run_is_idempotent_and_uses_canonical_digest():
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = FakeEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="hello", metadata={"b": "2", "a": "1"})

    first = service.submit_run(USER, session.control_session_id, request, "request-1")
    second = service.submit_run(USER, session.control_session_id, request, "request-1")

    assert first.run_id == second.run_id
    assert first.request_digest == canonical_request_digest(
        prompt="hello",
        session_id=session.engine_session_id,
        metadata={"b": "2", "a": "1"},
        parent_run_id=None,
    )

    with pytest.raises(IdempotencyConflictError):
        service.submit_run(
            USER,
            session.control_session_id,
            EngineRequest(prompt="different"),
            "request-1",
        )


def test_execute_run_claims_worker_and_records_success():
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = FakeEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="hello")
    submitted = service.submit_run(USER, session.control_session_id, request, "request-2")

    result = service.execute_run(WORKER, submitted.run_id, request, engine, "worker-1")

    assert result.text == "fake: hello"
    assert result.run_id == submitted.run_id
    assert runs.get(WORKER, submitted.run_id).state is RunState.SUCCEEDED


def test_execute_run_persists_engine_result_reference():
    class ResultReferenceEngine(FakeEngine):
        def run(self, request):
            return replace(
                super().run(request),
                result_ref="object://tenant-1/session-1/result-1",
            )

    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = ResultReferenceEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="save reference")
    submitted = service.submit_run(USER, session.control_session_id, request, "request-ref")

    service.execute_run(WORKER, submitted.run_id, request, engine, "worker-1")

    assert runs.get(WORKER, submitted.run_id).result_ref == (
        "object://tenant-1/session-1/result-1"
    )


def test_request_cancel_calls_active_engine_and_worker_records_confirmation():
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    cancel_results = []

    class CancelOnStartEngine(FakeEngine):
        def run(self, request, on_event=None):
            def handle_event(event):
                if event.event_type == "run_started":
                    cancel_results.append(
                        service.request_cancel(OWNER, request.run_id, self)
                    )
                if on_event is not None:
                    on_event(event)

            return super().run(request, on_event=handle_event)

    engine = CancelOnStartEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="cancel me")
    submitted = service.submit_run(USER, session.control_session_id, request, "cancel-run")

    result = service.execute_run(WORKER, submitted.run_id, request, engine, "worker-1")

    assert cancel_results[0].status is CancelStatus.CANCELLING
    assert result.status.value == "cancelled"
    assert runs.get(WORKER, submitted.run_id).state is RunState.CANCELLED


def test_member_cannot_request_run_cancellation():
    service = PersistentSessionService(MemorySessionRepository(), MemoryRunRepository())
    with pytest.raises(AuthorizationError):
        service.request_cancel(USER, "run-1", FakeEngine())


def test_unsupported_engine_cancellation_does_not_change_run_state():
    class NoCancellationEngine(FakeEngine):
        capabilities = EngineCapabilities(resume=True, cancellation=False)

    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = NoCancellationEngine()
    engine.start()
    session = service.create_session(USER, engine)
    request = EngineRequest(prompt="still running")
    submitted = service.submit_run(USER, session.control_session_id, request, "no-cancel")
    lease = runs.claim(WORKER, submitted.run_id, "worker-1")
    runs.transition(WORKER, submitted.run_id, lease.fencing_token, RunState.RUNNING)

    with pytest.raises(UnsupportedCapabilityError, match="does not support cancellation"):
        service.request_cancel(OWNER, submitted.run_id, engine)

    assert runs.get(WORKER, submitted.run_id).state is RunState.RUNNING


def test_session_create_requires_authenticated_member():
    service = PersistentSessionService(MemorySessionRepository(), MemoryRunRepository())
    engine = FakeEngine()
    engine.start()

    with pytest.raises(AuthorizationError):
        service.create_session(
            TenantContext("platform-1", "tenant-1", "user-1", roles=(), auth_source="oidc"),
            engine,
        )


def test_member_cannot_change_session_state():
    service = PersistentSessionService(MemorySessionRepository(), MemoryRunRepository())
    engine = FakeEngine()
    engine.start()
    record = service.create_session(USER, engine)

    with pytest.raises(AuthorizationError):
        service.update_session_status(USER, record.control_session_id, SessionStatus.PAUSED)


def test_viewer_can_list_but_cannot_create_or_submit():
    sessions = MemorySessionRepository()
    service = PersistentSessionService(sessions, MemoryRunRepository())
    engine = FakeEngine()
    engine.start()
    record = service.create_session(USER, engine)

    assert service.list_sessions(VIEWER).items[0].control_session_id == record.control_session_id
    with pytest.raises(AuthorizationError):
        service.create_session(VIEWER, engine)


def test_system_reconciler_marks_expired_runs_unknown_and_sessions_interrupted():
    sessions = MemorySessionRepository()
    runs = MemoryRunRepository()
    service = PersistentSessionService(sessions, runs)
    engine = FakeEngine()
    engine.start()
    session = service.create_session(USER, engine)
    submitted = service.submit_run(
        USER,
        session.control_session_id,
        EngineRequest(prompt="uncertain"),
        "request-expired",
    )
    lease = runs.claim(WORKER, submitted.run_id, "worker-1")
    runs.transition(WORKER, submitted.run_id, lease.fencing_token, RunState.RUNNING)
    runs.records[submitted.run_id] = replace(
        runs.records[submitted.run_id],
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    expired = service.reconcile_expired_runs(SYSTEM)

    assert len(expired) == 1
    assert expired[0].state is RunState.UNKNOWN
    assert expired[0].fencing_token == lease.fencing_token + 1
    assert sessions.get(USER, session.control_session_id).status is SessionStatus.INTERRUPTED

    with pytest.raises(AuthorizationError):
        service.reconcile_expired_runs(USER)
