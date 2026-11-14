from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from harness_engine.core import TenantContext
from harness_engine.persistence.errors import (
    AuthorizationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    LeaseLostError,
    ResourceNotFoundError,
)
from harness_engine.persistence.models import (
    MessageChunkRecord,
    RunRecord,
    RunState,
    SessionRecord,
)
from harness_engine.persistence.mysql import (
    MySqlAuditRepository,
    MySqlConfig,
    MySqlRunRepository,
    MySqlSessionRepository,
)

TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="user-1",
    roles=("owner",),
    auth_source="oidc",
)
WORKER_TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="worker-1",
    roles=("worker",),
    auth_source="worker",
)
SYSTEM_TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="system-worker",
    roles=("system",),
    auth_source="system",
)
OTHER_TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-2",
    user_id="user-2",
    roles=("owner",),
    auth_source="oidc",
)


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=()):
        self.connection.calls.append((sql, tuple(params)))
        self._rows, self.rowcount = self.connection.next_response()

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        return None


class FakeConnection:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **_kwargs):
        return FakeCursor(self)

    def next_response(self):
        if not self.responses:
            return [], 0
        response = self.responses.pop(0)
        if len(response) == 2:
            return response
        raise AssertionError(f"invalid fake response: {response!r}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeDatabase:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


def _session_row(**overrides):
    row = {
        "session_id": "01JSESSION000000000000000001",
        "user_id": "user-1",
        "engine_name": "fake",
        "engine_session_id": "fake-native-1",
        "status": "ACTIVE",
        "metadata_json": None,
        "harness_home_ref": None,
        "workspace_ref": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "last_run_at": None,
    }
    row.update(overrides)
    return row


def _run_row(**overrides):
    row = {
        "run_id": "01JRUN00000000000000000001",
        "session_id": "01JSESSION000000000000000001",
        "idempotency_key": "request-1",
        "request_digest": "a" * 64,
        "request_digest_version": 1,
        "state": "QUEUED",
        "worker_id": None,
        "fencing_token": 0,
        "lease_expires_at": None,
        "started_at": None,
        "finished_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "error_code": None,
        "result_ref": None,
    }
    row.update(overrides)
    return row


def test_mysql_config_reads_explicit_environment(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "db.example")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_DATABASE", "harness")
    monkeypatch.setenv("MYSQL_USER", "app")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")

    config = MySqlConfig.from_env()

    assert config.host == "db.example"
    assert config.port == 3307
    assert config.database == "harness"
    assert config.user == "app"


def test_session_get_scopes_query_to_platform_and_tenant():
    connection = FakeConnection(responses=[([_session_row()], 1)])
    repository = MySqlSessionRepository(FakeDatabase(connection))

    record = repository.get(TENANT, "01JSESSION000000000000000001")

    assert isinstance(record, SessionRecord)
    assert record.control_session_id == "01JSESSION000000000000000001"
    assert connection.calls[0][1] == (
        "platform-1",
        "tenant-1",
        "01JSESSION000000000000000001",
    )


def test_session_get_hides_cross_tenant_resource_as_not_found():
    connection = FakeConnection(responses=[([], 0)])
    repository = MySqlSessionRepository(FakeDatabase(connection))

    with pytest.raises(ResourceNotFoundError):
        repository.get(TENANT, "missing")


def test_reused_database_connection_does_not_reuse_previous_tenant_scope():
    connection = FakeConnection(responses=[([_session_row()], 1), ([], 0)])
    repository = MySqlSessionRepository(FakeDatabase(connection))
    session_id = "01JSESSION000000000000000001"

    assert repository.get(TENANT, session_id).control_session_id == session_id
    with pytest.raises(ResourceNotFoundError):
        repository.get(OTHER_TENANT, session_id)

    assert connection.calls[0][1][:2] == TENANT.scope
    assert connection.calls[1][1][:2] == OTHER_TENANT.scope


def test_create_operations_require_matching_explicit_tenant_context():
    session_connection = FakeConnection()
    session_repository = MySqlSessionRepository(FakeDatabase(session_connection))
    session = SessionRecord(
        control_session_id="01JSESSION000000000000000002",
        tenant=OTHER_TENANT,
        user_id="user-2",
        engine_name="fake",
        engine_session_id="fake-native-2",
    )
    run_connection = FakeConnection()
    run_repository = MySqlRunRepository(FakeDatabase(run_connection))
    run = RunRecord(
        run_id="01JRUN00000000000000000002",
        tenant=OTHER_TENANT,
        session_id="01JSESSION000000000000000002",
        idempotency_key="request-2",
        request_digest="b" * 64,
    )

    with pytest.raises(ValueError, match="explicit tenant context"):
        session_repository.create(TENANT, session)
    with pytest.raises(ValueError, match="explicit tenant context"):
        run_repository.create_or_get(TENANT, run)

    wrong_user_connection = FakeConnection()
    wrong_user_repository = MySqlSessionRepository(FakeDatabase(wrong_user_connection))
    same_tenant_other_user = SessionRecord(
        control_session_id="01JSESSION000000000000000003",
        tenant=TENANT,
        user_id="other-user",
        engine_name="fake",
        engine_session_id="fake-native-3",
    )
    with pytest.raises(ValueError, match="user does not match"):
        wrong_user_repository.create(TENANT, same_tenant_other_user)

    assert session_connection.calls == []
    assert run_connection.calls == []
    assert wrong_user_connection.calls == []


def test_run_create_or_get_returns_existing_record_for_same_digest():
    existing = _run_row()
    connection = FakeConnection(responses=[([], 0), ([existing], 1)])
    repository = MySqlRunRepository(FakeDatabase(connection))
    requested = RunRecord(
        run_id=existing["run_id"],
        tenant=TENANT,
        session_id=existing["session_id"],
        idempotency_key=existing["idempotency_key"],
        request_digest=existing["request_digest"],
    )

    record = repository.create_or_get(TENANT, requested)

    assert record.run_id == requested.run_id
    assert connection.commits == 1
    assert len(connection.calls) == 2


def test_run_create_or_get_inserts_once_and_audits_inside_transaction():
    requested = RunRecord(
        run_id="01JRUN00000000000000000002",
        tenant=TENANT,
        session_id="01JSESSION000000000000000001",
        idempotency_key="request-new",
        request_digest="b" * 64,
    )
    connection = FakeConnection(
        responses=[([], 1), ([_run_row(
            run_id=requested.run_id,
            idempotency_key=requested.idempotency_key,
            request_digest=requested.request_digest,
        )], 1), ([], 1)]
    )
    repository = MySqlRunRepository(FakeDatabase(connection))

    record = repository.create_or_get(TENANT, requested)

    assert record.run_id == requested.run_id
    assert "ON DUPLICATE KEY UPDATE" in connection.calls[0][0]
    assert "INSERT INTO audit_events" in connection.calls[2][0]
    assert connection.commits == 1


def test_run_create_or_get_rejects_same_key_with_different_digest():
    existing = _run_row(request_digest="a" * 64)
    connection = FakeConnection(responses=[([], 0), ([existing], 1)])
    repository = MySqlRunRepository(FakeDatabase(connection))
    requested = RunRecord(
        run_id="01JRUN00000000000000000002",
        tenant=TENANT,
        session_id=existing["session_id"],
        idempotency_key=existing["idempotency_key"],
        request_digest="b" * 64,
    )

    with pytest.raises(IdempotencyConflictError):
        repository.create_or_get(TENANT, requested)

    assert connection.rollbacks == 1


def test_claim_next_uses_skip_locked_and_returns_positive_fencing_token():
    queued = _run_row(state="QUEUED", fencing_token=3)
    connection = FakeConnection(
        responses=[([queued], 1), ([], 1), ([{"fencing_token": 4}], 1), ([], 1)]
    )
    repository = MySqlRunRepository(FakeDatabase(connection))

    lease = repository.claim_next(TENANT, "worker-1")

    assert lease is not None
    assert lease.worker_id == "worker-1"
    assert lease.fencing_token == 4
    assert "SKIP LOCKED" in connection.calls[0][0]


def test_stale_fencing_token_rolls_back_and_does_not_update_state():
    connection = FakeConnection(responses=[([_run_row(fencing_token=3)], 1)])
    repository = MySqlRunRepository(FakeDatabase(connection))

    with pytest.raises(LeaseLostError):
        repository.transition(
            TENANT,
            "01JRUN00000000000000000001",
            fencing_token=2,
            state=RunState.SUCCEEDED,
        )

    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_request_cancel_is_tenant_scoped_and_audited_atomically():
    running = _run_row(state="RUNNING", fencing_token=6, worker_id="worker-1")
    connection = FakeConnection(
        responses=[([running], 1), ([], 1), ([], 1)]
    )
    repository = MySqlRunRepository(FakeDatabase(connection))

    record = repository.request_cancel(TENANT, running["run_id"])

    assert record.state is RunState.CANCELLING
    update_sql, update_params = connection.calls[1]
    assert "UPDATE runs SET state = 'CANCELLING'" in update_sql
    assert update_params[1:4] == (
        "platform-1",
        "tenant-1",
        running["run_id"],
    )
    audit_sql = connection.calls[2][0]
    assert "INSERT INTO audit_events" in audit_sql
    assert connection.commits == 1


def test_expire_leases_marks_unknown_increments_fencing_and_audits():
    expired = _run_row(
        state="RUNNING",
        worker_id="worker-old",
        fencing_token=9,
        lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    connection = FakeConnection(
        responses=[([expired], 1), ([], 1), ([], 1)]
    )
    repository = MySqlRunRepository(FakeDatabase(connection))

    records = repository.expire_leases(TENANT, limit=10)

    assert len(records) == 1
    assert records[0].state is RunState.UNKNOWN
    assert records[0].fencing_token == 10
    assert records[0].error_code == "RUN_LEASE_EXPIRED"
    assert "SKIP LOCKED" in connection.calls[0][0]
    assert "INSERT INTO audit_events" in connection.calls[2][0]


def test_audit_append_uses_explicit_actor_and_does_not_store_prompt():
    connection = FakeConnection(responses=[([{"fencing_token": 4}], 1), ([], 1)])
    repository = MySqlAuditRepository(FakeDatabase(connection))

    event_id = repository.append(
        TENANT,
        event_type="run.finished",
        actor_type="worker",
        run_id="01JRUN00000000000000000001",
        fencing_token=4,
        details={"finish_reason": "completed"},
    )

    assert len(event_id) == 26
    sql, params = connection.calls[1]
    assert "INSERT INTO audit_events" in sql
    assert "prompt" not in str(params).lower()
    assert params[0:2] == ("platform-1", "tenant-1")


@pytest.mark.parametrize(
    "details",
    [
        {"extra": {"prompt": "secret"}},
        {"tool_arguments": "secret"},
        {"metadata": {"access_token": "secret"}},
        {"finish_reason": "prompt=secret"},
    ],
)
def test_audit_append_rejects_nested_unknown_and_sensitive_details(details):
    connection = FakeConnection()
    repository = MySqlAuditRepository(FakeDatabase(connection))

    with pytest.raises(ValueError, match="audit details"):
        repository.append(
            TENANT,
            event_type="run.finished",
            actor_type="SYSTEM",
            details=details,
        )

    assert connection.calls == []
    assert connection.rollbacks == 1


def test_stale_worker_cannot_append_fenced_audit_event():
    connection = FakeConnection(responses=[([{"fencing_token": 5}], 1)])
    repository = MySqlAuditRepository(FakeDatabase(connection))

    with pytest.raises(LeaseLostError):
        repository.append(
            WORKER_TENANT,
            event_type="run.state_changed",
            actor_type="worker",
            run_id="01JRUN00000000000000000001",
            fencing_token=4,
            details={"state": "SUCCEEDED"},
        )

    assert connection.rollbacks == 1
    assert len(connection.calls) == 1


def _message_row(**overrides):
    row = {"message_id": "m1", "session_id": "s1", "run_id": "r1", "sequence_no": 1,
               "role": "USER", "status": "COMPLETE", "content_ref": "opaque-ref",
               "content_sha256": "a" * 64, "byte_length": 6, "content_type": "text/plain",
               "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
               "completed_at": datetime.now(UTC)}
    row.update(overrides)
    return row


def test_message_reads_scope_both_tenant_keys_and_hide_missing_resources():
    from harness_engine.persistence.mysql import MySqlMessageRepository
    connection = FakeConnection([([_message_row()], 1), ([], 0), ([], 0)])
    repo = MySqlMessageRepository(FakeDatabase(connection))
    assert repo.get(TENANT, "m1").message_id == "m1"
    with pytest.raises(ResourceNotFoundError):
        repo.get(OTHER_TENANT, "m1")
    with pytest.raises(ResourceNotFoundError):
        repo.list(OTHER_TENANT, session_id="s1", limit=10)
    assert connection.calls[0][1] == (*TENANT.scope, "m1")
    assert all("platform_id = %s AND tenant_id = %s" in sql for sql, _ in connection.calls)


def test_expiring_upload_locks_with_database_time_and_queues_before_commit():
    from harness_engine.persistence.mysql import MySqlContentUploadRepository
    row = {"upload_id": "u1", "object_ref": "opaque-ref", "object_type": "MESSAGE_CONTENT",
               "session_id": "s1", "message_id": "m1", "run_id": "r1", "status": "UPLOADING",
               "lease_expires_at": datetime(2000, 1, 1, tzinfo=UTC),
               "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}
    connection = FakeConnection([([row], 1), ([], 1), ([], 1)])
    repo = MySqlContentUploadRepository(FakeDatabase(connection))
    expired = repo.claim_expired(TENANT, limit=10)
    assert expired[0].status == "EXPIRED"
    assert "CURRENT_TIMESTAMP(6)" in connection.calls[0][0]
    assert "SKIP LOCKED" in connection.calls[0][0]
    assert any("INSERT INTO content_gc_queue" in sql for sql, _ in connection.calls)
    assert connection.commits == 1
    assert all(params[:2] == TENANT.scope for _, params in connection.calls)


def test_expiring_upload_rolls_back_when_gc_queue_fails():
    from harness_engine.persistence.mysql import MySqlContentUploadRepository
    class QueueFailure(FakeConnection):
        def next_response(self):
            if self.calls and "INSERT INTO content_gc_queue" in self.calls[-1][0]:
                raise RuntimeError("queue unavailable")
            return super().next_response()
    row = {"upload_id": "u1", "object_ref": "opaque-ref", "object_type": "MESSAGE_CONTENT",
               "status": "UPLOADING", "lease_expires_at": datetime(2000, 1, 1, tzinfo=UTC)}
    connection = QueueFailure([([row], 1), ([], 1)])
    with pytest.raises(RuntimeError, match="queue unavailable"):
        MySqlContentUploadRepository(FakeDatabase(connection)).claim_expired(TENANT)
    assert connection.rollbacks == 1 and connection.commits == 0


def test_assistant_completion_rejects_stale_fence_before_message_publication():
    from harness_engine.persistence.models import MessageRecord
    from harness_engine.persistence.mysql import MySqlMessageRepository
    connection = FakeConnection([([_run_row(state="RUNNING", fencing_token=3)], 1)])
    repo = MySqlMessageRepository(FakeDatabase(connection))
    message = MessageRecord(tenant=TENANT, **_message_row(role="ASSISTANT", sequence_no=0))
    with pytest.raises(LeaseLostError):
        repo.complete(TENANT, message, upload_id="u1", fencing_token=2)
    assert connection.rollbacks == 1 and connection.commits == 0
    assert len(connection.calls) == 1


def _assistant_stream_row(**overrides):
    row = _message_row(
        role="ASSISTANT",
        status="PENDING",
        content_ref=None,
        content_sha256=None,
        byte_length=None,
        content_type=None,
        completed_at=None,
        last_committed_chunk_sequence=0,
    )
    row.update(overrides)
    return row


def _chunk_row(**overrides):
    row = {
        "message_id": "m1",
        "chunk_sequence": 1,
        "content_ref": "chunk-ref-1",
        "content_sha256": "b" * 64,
        "byte_length": 5,
        "created_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


def test_append_chunk_publishes_upload_and_advances_contiguous_cursor_atomically():
    from datetime import timedelta

    from harness_engine.persistence.mysql import MySqlMessageRepository

    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=5,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        lease_active=True,
    )
    assistant = _assistant_stream_row()
    upload = {
        "upload_id": "upload-1",
        "object_ref": "chunk-ref-1",
        "object_type": "STREAM_CHUNK",
        "session_id": "s1",
        "message_id": "m1",
        "run_id": "r1",
        "status": "UPLOADING",
        "lease_expires_at": datetime.now(UTC) + timedelta(minutes=2),
        "lease_active": True,
    }
    connection = FakeConnection(
        [
            ([identity], 1),
            ([run], 1),
            ([assistant], 1),
            ([], 1),
            ([], 1),
            ([upload], 1),
            ([], 1),
        ]
    )
    repo = MySqlMessageRepository(FakeDatabase(connection))
    chunk = MessageChunkRecord(
        message_id="m1",
        chunk_sequence=1,
        content_ref="chunk-ref-1",
        content_sha256="b" * 64,
        byte_length=5,
    )

    stored = repo.append_chunk(
        WORKER_TENANT, chunk, upload_id="upload-1", fencing_token=5
    )

    assert stored == chunk
    assert connection.commits == 1 and connection.rollbacks == 0
    sql = [statement for statement, _ in connection.calls]
    assert any("INSERT INTO message_chunks" in statement for statement in sql)
    assert any("last_committed_chunk_sequence" in statement for statement in sql)
    assert any("status = 'PUBLISHED'" in statement for statement in sql)
    assert all(
        any(params[index : index + 2] == WORKER_TENANT.scope for index in range(len(params) - 1))
        for _, params in connection.calls
    )


@pytest.mark.parametrize("current, requested", [(2, 2), (2, 4)])
def test_append_chunk_rejects_duplicate_or_gapped_sequence_without_publication(
    current, requested
):
    from datetime import timedelta

    from harness_engine.persistence.mysql import MySqlMessageRepository

    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=5,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        lease_active=True,
    )
    assistant = _assistant_stream_row(last_committed_chunk_sequence=current)
    connection = FakeConnection([([identity], 1), ([run], 1), ([assistant], 1)])
    repo = MySqlMessageRepository(FakeDatabase(connection))
    chunk = MessageChunkRecord(
        message_id="m1",
        chunk_sequence=requested,
        content_ref="chunk-ref-new",
        content_sha256="c" * 64,
        byte_length=3,
    )

    with pytest.raises(InvalidStateTransitionError, match="next contiguous"):
        repo.append_chunk(WORKER_TENANT, chunk, upload_id="upload-1", fencing_token=5)

    assert connection.rollbacks == 1 and connection.commits == 0
    assert all("INSERT INTO message_chunks" not in sql for sql, _ in connection.calls)


def test_append_chunk_rejects_stale_fencing_token_before_locking_message():
    from datetime import timedelta

    from harness_engine.persistence.mysql import MySqlMessageRepository

    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=6,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        lease_active=True,
    )
    connection = FakeConnection([([identity], 1), ([run], 1)])
    repo = MySqlMessageRepository(FakeDatabase(connection))
    chunk = MessageChunkRecord(
        message_id="m1",
        chunk_sequence=1,
        content_ref="chunk-ref-1",
        content_sha256="b" * 64,
        byte_length=5,
    )

    with pytest.raises(LeaseLostError, match="stale"):
        repo.append_chunk(WORKER_TENANT, chunk, upload_id="upload-1", fencing_token=5)

    assert connection.rollbacks == 1 and len(connection.calls) == 2


def test_list_chunks_after_returns_only_tenant_scoped_contiguous_rows():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    assistant = _assistant_stream_row(last_committed_chunk_sequence=3)
    connection = FakeConnection(
        [
            ([assistant], 1),
            ([_chunk_row(chunk_sequence=2), _chunk_row(chunk_sequence=3)], 2),
        ]
    )
    repo = MySqlMessageRepository(FakeDatabase(connection))

    page = repo.list_chunks_after(
        TENANT, message_id="m1", last_chunk_sequence=1, limit=10
    )

    assert [chunk.chunk_sequence for chunk in page.items] == [2, 3]
    assert page.next_cursor is None
    assert all(params[:2] == TENANT.scope for _, params in connection.calls)
    assert "chunk_sequence > %s" in connection.calls[1][0]
    assert "ORDER BY chunk_sequence" in connection.calls[1][0]


def test_list_chunks_after_rejects_cursor_ahead_of_durable_high_water():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection([([_assistant_stream_row(last_committed_chunk_sequence=1)], 1)])
    repo = MySqlMessageRepository(FakeDatabase(connection))

    with pytest.raises(ValueError, match="ahead of committed stream"):
        repo.list_chunks_after(TENANT, message_id="m1", last_chunk_sequence=2)

    assert len(connection.calls) == 1


def test_chunk_high_water_is_read_from_tenant_scoped_message_row():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection([([_assistant_stream_row(last_committed_chunk_sequence=7)], 1)])
    repo = MySqlMessageRepository(FakeDatabase(connection))

    assert repo.chunk_high_water(TENANT, "m1") == 7
    assert connection.calls[0][1] == (*TENANT.scope, "m1")


def test_append_chunk_rolls_back_when_upload_intent_is_expired():
    from datetime import timedelta

    from harness_engine.persistence.mysql import MySqlMessageRepository

    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=5,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        lease_active=True,
    )
    assistant = _assistant_stream_row()
    expired_upload = {
        "upload_id": "upload-1",
        "object_ref": "chunk-ref-1",
        "object_type": "STREAM_CHUNK",
        "session_id": "s1",
        "message_id": "m1",
        "run_id": "r1",
        "status": "EXPIRED",
        "lease_expires_at": datetime.now(UTC) - timedelta(minutes=2),
        "lease_active": False,
    }
    connection = FakeConnection(
        [
            ([identity], 1),
            ([run], 1),
            ([assistant], 1),
            ([], 1),
            ([], 1),
            ([expired_upload], 1),
        ]
    )
    repo = MySqlMessageRepository(FakeDatabase(connection))
    chunk = MessageChunkRecord(
        message_id="m1",
        chunk_sequence=1,
        content_ref="chunk-ref-1",
        content_sha256="b" * 64,
        byte_length=5,
    )

    with pytest.raises(LeaseLostError, match="expired"):
        repo.append_chunk(WORKER_TENANT, chunk, upload_id="upload-1", fencing_token=5)

    assert connection.rollbacks == 1 and connection.commits == 0
    assert any("INSERT INTO message_chunks" in sql for sql, _ in connection.calls)


def test_list_chunks_after_rejects_a_gap_in_durable_chunk_rows():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection(
        [
            ([_assistant_stream_row(last_committed_chunk_sequence=3)], 1),
            ([_chunk_row(chunk_sequence=1), _chunk_row(chunk_sequence=3)], 2),
        ]
    )
    repo = MySqlMessageRepository(FakeDatabase(connection))

    with pytest.raises(InvalidStateTransitionError, match="prefix has a gap"):
        repo.list_chunks_after(TENANT, message_id="m1", last_chunk_sequence=0)


def test_list_chunks_after_rejects_a_trailing_gap_below_high_water():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection(
        [
            ([_assistant_stream_row(last_committed_chunk_sequence=3)], 1),
            ([_chunk_row(chunk_sequence=1), _chunk_row(chunk_sequence=2)], 2),
        ]
    )
    repo = MySqlMessageRepository(FakeDatabase(connection))

    with pytest.raises(InvalidStateTransitionError, match="trailing gap"):
        repo.list_chunks_after(TENANT, message_id="m1", last_chunk_sequence=0)


def test_get_for_run_returns_only_requested_tenant_message():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection([([_assistant_stream_row()], 1), ([], 0)])
    repo = MySqlMessageRepository(FakeDatabase(connection))

    assert repo.get_for_run(TENANT, "r1").message_id == "m1"
    assert repo.get_for_run(OTHER_TENANT, "r1") is None
    assert connection.calls[0][1] == (*TENANT.scope, "r1", "ASSISTANT")
    assert connection.calls[1][1] == (*OTHER_TENANT.scope, "r1", "ASSISTANT")


def test_assistant_message_publisher_accepts_manifest_intent():
    from harness_engine.persistence.models import (
        MessageRecord,
        MessageRole,
        MessageStatus,
    )
    from harness_engine.persistence.mysql import MySqlContentUploadRepository

    intent = {
        "object_ref": "manifest-ref",
        "object_type": "MANIFEST",
        "session_id": "s1",
        "message_id": "m1",
        "run_id": "r1",
        "status": "UPLOADING",
        "lease_active": True,
    }
    connection = FakeConnection([([intent], 1), ([], 1)])
    message = MessageRecord(
        message_id="m1",
        tenant=TENANT,
        session_id="s1",
        run_id="r1",
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETE,
        content_ref="manifest-ref",
        content_sha256="d" * 64,
        byte_length=120,
        content_type="application/vnd.harness.manifest+json",
    )

    MySqlContentUploadRepository.publish(connection, TENANT, "upload-1", message)

    assert connection.calls[1][1] == (*TENANT.scope, "upload-1")
    assert "status = 'PUBLISHED'" in connection.calls[1][0]


def _partial_manifest(tenant, **overrides):
    from harness_engine.persistence.models import (
        MessageRecord,
        MessageRole,
        MessageStatus,
    )

    values = {
        "message_id": "m1",
        "tenant": tenant,
        "session_id": "s1",
        "role": MessageRole.ASSISTANT,
        "status": MessageStatus.PARTIAL,
        "sequence_no": 2,
        "run_id": "r1",
        "content_ref": "manifest-ref",
        "content_sha256": "d" * 64,
        "byte_length": 120,
        "content_type": "application/vnd.harness.manifest+json",
    }
    values.update(overrides)
    return MessageRecord(**values)


def test_finalize_worker_partial_publishes_manifest_and_terminal_run_atomically():
    from datetime import timedelta

    from harness_engine.persistence.models import RunState
    from harness_engine.persistence.mysql import MySqlMessageRepository

    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=5,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=2),
        lease_active=True,
    )
    message = _assistant_stream_row(status="STREAMING", last_committed_chunk_sequence=2)
    manifest_intent = {
        "upload_id": "upload-manifest",
        "object_ref": "manifest-ref",
        "object_type": "MANIFEST",
        "session_id": "s1",
        "message_id": "m1",
        "run_id": "r1",
        "status": "UPLOADING",
        "lease_active": True,
    }
    partial_row = _message_row(
        role="ASSISTANT",
        status="PARTIAL",
        content_ref="manifest-ref",
        content_sha256="d" * 64,
        byte_length=120,
        content_type="application/vnd.harness.manifest+json",
        last_committed_chunk_sequence=2,
    )
    connection = FakeConnection(
        [
            ([run], 1),
            ([message], 1),
            ([manifest_intent], 1),
            ([], 1),
            ([], 1),
            ([run], 1),
            ([], 1),
            ([], 1),
            ([{"fencing_token": 5}], 1),
            ([], 1),
            ([{"fencing_token": 5}], 1),
            ([], 1),
            ([partial_row], 1),
        ]
    )
    repository = MySqlMessageRepository(FakeDatabase(connection))

    result = repository.finalize_worker_partial(
        WORKER_TENANT,
        _partial_manifest(WORKER_TENANT),
        upload_id="upload-manifest",
        fencing_token=5,
        run_state=RunState.FAILED,
    )

    assert result.status.value == "PARTIAL"
    assert result.content_ref == "manifest-ref"
    assert connection.commits == 1 and connection.rollbacks == 0
    statements = [sql for sql, _ in connection.calls]
    assert any("SET status = 'PARTIAL'" in sql for sql in statements)
    assert any("SET state = %s" in sql and "UPDATE runs" in sql for sql in statements)


def test_recover_partial_is_system_only_and_preserves_committed_chunk_cursor():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="UNKNOWN",
        fencing_token=9,
        lease_expires_at=None,
        lease_active=None,
    )
    message = _assistant_stream_row(status="STREAMING", last_committed_chunk_sequence=4)
    manifest_intent = {
        "upload_id": "upload-manifest",
        "object_ref": "manifest-ref",
        "object_type": "MANIFEST",
        "session_id": "s1",
        "message_id": "m1",
        "run_id": "r1",
        "status": "UPLOADING",
        "lease_active": True,
    }
    partial_row = _message_row(
        role="ASSISTANT",
        status="PARTIAL",
        content_ref="manifest-ref",
        content_sha256="d" * 64,
        byte_length=120,
        content_type="application/vnd.harness.manifest+json",
        last_committed_chunk_sequence=4,
    )
    connection = FakeConnection(
        [
            ([run], 1),
            ([message], 1),
            ([manifest_intent], 1),
            ([], 1),
            ([], 1),
            ([], 1),
            ([], 1),
            ([partial_row], 1),
        ]
    )
    repository = MySqlMessageRepository(FakeDatabase(connection))

    result = repository.recover_partial(
        SYSTEM_TENANT,
        _partial_manifest(SYSTEM_TENANT),
        upload_id="upload-manifest",
        fencing_token=9,
    )

    assert result.status.value == "PARTIAL"
    assert result.content_ref == "manifest-ref"
    update_sql = next(sql for sql, _ in connection.calls if "SET status = 'PARTIAL'" in sql)
    assert "last_committed_chunk_sequence" not in update_sql
    assert connection.calls[0][1] == (*SYSTEM_TENANT.scope, "r1")
    assert connection.commits == 1


def test_recover_partial_rejects_non_system_tenant_before_database_access():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection()
    repository = MySqlMessageRepository(FakeDatabase(connection))

    with pytest.raises(AuthorizationError, match="system authentication"):
        repository.recover_partial(
            TENANT,
            _partial_manifest(TENANT),
            upload_id="upload-manifest",
            fencing_token=9,
        )

    assert connection.calls == []


def test_list_unfinalized_streams_returns_scoped_unknown_run_and_pending_message():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    now = datetime.now(UTC)
    row = {
        "run_id": "r1",
        "run_session_id": "s1",
        "idempotency_key": "request-1",
        "request_digest": "a" * 64,
        "request_digest_version": 1,
        "run_state": "UNKNOWN",
        "worker_id": "worker-1",
        "fencing_token": 9,
        "lease_expires_at": None,
        "started_at": now,
        "finished_at": now,
        "run_created_at": now,
        "run_updated_at": now,
        "error_code": "RUN_LEASE_EXPIRED",
        "result_ref": None,
        "message_id": "m1",
        "message_session_id": "s1",
        "message_run_id": "r1",
        "sequence_no": 2,
        "role": "ASSISTANT",
        "status": "PENDING",
        "content_ref": None,
        "content_sha256": None,
        "byte_length": None,
        "content_type": None,
        "message_created_at": now,
        "message_updated_at": now,
        "completed_at": None,
    }
    connection = FakeConnection([([row], 1)])
    repository = MySqlMessageRepository(FakeDatabase(connection))

    pending = repository.list_unfinalized_streams(SYSTEM_TENANT)

    assert len(pending) == 1
    run, message = pending[0]
    assert run.state.value == "UNKNOWN" and run.fencing_token == 9
    assert message.message_id == "m1" and message.status.value == "PENDING"
    assert connection.calls[0][1] == (*SYSTEM_TENANT.scope, 100)
    assert "m.status IN ('PENDING', 'STREAMING')" in connection.calls[0][0]


def test_list_unfinalized_streams_requires_system_tenant():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    connection = FakeConnection()
    repository = MySqlMessageRepository(FakeDatabase(connection))

    with pytest.raises(AuthorizationError, match="system authentication"):
        repository.list_unfinalized_streams(TENANT)

    assert connection.calls == []


def test_fail_pending_message_atomically_fails_the_empty_assistant_and_run():
    from datetime import timedelta

    from harness_engine.persistence.models import RunState
    from harness_engine.persistence.mysql import MySqlMessageRepository

    now = datetime.now(UTC)
    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="RUNNING",
        fencing_token=5,
        lease_expires_at=now + timedelta(minutes=2),
        lease_active=True,
    )
    assistant = _assistant_stream_row()
    failed_row = _message_row(
        role="ASSISTANT",
        status="FAILED",
        content_ref=None,
        content_sha256=None,
        byte_length=None,
        content_type=None,
        completed_at=now,
    )
    connection = FakeConnection(
        [
            ([identity], 1),
            ([run], 1),
            ([assistant], 1),
            ([], 1),
            ([run], 1),
            ([], 1),
            ([], 1),
            ([{"fencing_token": 5}], 1),
            ([], 1),
            ([failed_row], 1),
        ]
    )
    repository = MySqlMessageRepository(FakeDatabase(connection))

    result = repository.fail_pending_message(
        WORKER_TENANT,
        "m1",
        fencing_token=5,
        terminal_state=RunState.FAILED,
    )

    assert result.status.value == "FAILED" and result.content_ref is None
    assert connection.commits == 1
    assert any("UPDATE runs" in sql for sql, _ in connection.calls)


def test_fail_expired_pending_preserves_unknown_run_state_and_requires_system():
    from harness_engine.persistence.mysql import MySqlMessageRepository

    now = datetime.now(UTC)
    identity = {"run_id": "r1", "session_id": "s1"}
    run = _run_row(
        run_id="r1",
        session_id="s1",
        state="UNKNOWN",
        fencing_token=9,
        lease_expires_at=None,
        lease_active=None,
    )
    assistant = _assistant_stream_row()
    failed_row = _message_row(
        role="ASSISTANT",
        status="FAILED",
        content_ref=None,
        content_sha256=None,
        byte_length=None,
        content_type=None,
        completed_at=now,
    )
    connection = FakeConnection(
        [([identity], 1), ([run], 1), ([assistant], 1), ([], 1), ([], 1), ([failed_row], 1)]
    )
    repository = MySqlMessageRepository(FakeDatabase(connection))

    result = repository.fail_expired_pending(SYSTEM_TENANT, "m1", fencing_token=9)

    assert result.status.value == "FAILED"
    assert not any("UPDATE runs" in sql for sql, _ in connection.calls)
    assert connection.commits == 1
