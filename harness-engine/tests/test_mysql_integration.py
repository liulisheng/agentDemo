"""Opt-in tests against a disposable MySQL 8.4 database.

Set HARNESS_MYSQL_INTEGRATION=1 and the MYSQL_* variables after applying
docs/migrations/V001__core_schema.sql and V002__conversation_messages.sql to run these tests.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import TenantContext
from harness_engine.persistence.errors import (
    IdempotencyConflictError,
    LeaseLostError,
    ResourceNotFoundError,
)
from harness_engine.persistence.ids import generate_id
from harness_engine.persistence.models import RunRecord, RunState, SessionRecord
from harness_engine.persistence.mysql import (
    MySqlAuditRepository,
    MySqlConfig,
    MySqlDatabase,
    MySqlRunRepository,
    MySqlSessionRepository,
)

if os.environ.get("HARNESS_MYSQL_INTEGRATION") != "1":
    pytest.skip("set HARNESS_MYSQL_INTEGRATION=1 to run real MySQL tests", allow_module_level=True)


@pytest.fixture
def content_database():
    """A unique platform makes this test independent of other MySQL runs."""
    from harness_engine.engines.fake import FakeEngine
    from harness_engine.session_service import PersistentSessionService
    database = MySqlDatabase.from_config(MySqlConfig.from_env())
    platform, tenant_id, user_id = generate_id(), generate_id(), generate_id()
    tenant = TenantContext(platform, tenant_id, user_id, ("owner",), "oidc")
    with database.transaction() as connection:
        cursor = connection.cursor()
        cursor.execute("INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,'Content test','ACTIVE')", (platform, platform))
        cursor.execute("INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,'content','Content test','ACTIVE')", tenant.scope)
        cursor.execute("INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) VALUES (%s,%s,%s,%s,%s,'Test','ACTIVE')", (*tenant.scope, user_id, user_id, user_id))
        cursor.close()
    sessions, runs = MySqlSessionRepository(database), MySqlRunRepository(database)
    engine = FakeEngine()
    session = PersistentSessionService(sessions, runs).create_session(tenant, engine)
    try:
        yield database, tenant, sessions, runs, engine, session
    finally:
        with database.transaction() as connection:
            cursor = connection.cursor()
            for table in ("message_chunks", "messages", "content_gc_queue", "content_upload_intents", "audit_events", "runs", "sessions", "users", "tenants", "platforms"):
                cursor.execute(f"DELETE FROM {table} WHERE platform_id=%s", (platform,))
            cursor.close()


def test_mysql_content_publication_atomicity_tenant_scope_expiry_and_idempotency(content_database, tmp_path):
    import hashlib
    from dataclasses import replace

    from harness_engine.core import EngineRequest
    from harness_engine.persistence.content_store import LocalContentStore
    from harness_engine.persistence.models import (
        MessageRecord,
        MessageRole,
        MessageStatus,
    )
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    from harness_engine.session_service import PersistentSessionService
    database, tenant, sessions, runs, engine, session = content_database
    uploads = MySqlContentUploadRepository(database)
    messages = MySqlMessageRepository(database)
    store = LocalContentStore(tmp_path, b"k" * 32)
    service = PersistentSessionService(sessions, runs, messages=messages, uploads=uploads, content_store=store)
    request = EngineRequest(prompt="private user body 私密")
    first = service.submit_run(tenant, session.control_session_id, request, "same-key")
    retry = service.submit_run(tenant, session.control_session_id, request, "same-key")
    assert retry.run_id == first.run_id and retry.message_ids == first.message_ids
    assert len(messages.list(tenant, session_id=session.control_session_id, limit=10).items) == 1
    with pytest.raises(IdempotencyConflictError):
        service.submit_run(tenant, session.control_session_id, EngineRequest(prompt="changed secret"), "same-key")
    worker = replace(tenant, user_id=generate_id(), roles=("worker",), auth_source="worker")
    result = service.execute_run(worker, first.run_id, request, engine, "worker")
    rows = messages.list(tenant, session_id=session.control_session_id, limit=10).items
    assert [m.sequence_no for m in rows] == [1, 2]
    assert [m.role for m in rows] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert rows[1].content_ref == runs.get(tenant, first.run_id).result_ref == result.result_ref
    assert store.get(tenant, rows[0].content_ref).decode() == request.prompt
    assert store.get(tenant, rows[1].content_ref).decode() == result.text
    assert rows[1].content_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    assert all(m.status is MessageStatus.COMPLETE for m in rows)
    assert service.submit_run(tenant, session.control_session_id, request, "same-key").message_ids == first.message_ids

    for other in (replace(tenant, tenant_id=generate_id()), replace(tenant, platform_id=generate_id())):
        with pytest.raises(ResourceNotFoundError):
            messages.get(other, rows[0].message_id)
        with pytest.raises(ResourceNotFoundError):
            messages.list(other, session_id=session.control_session_id, limit=10)
        with pytest.raises(ResourceNotFoundError):
            messages.delete(other, rows[0].message_id)
        with pytest.raises(ResourceNotFoundError):
            uploads.renew(other, next(iter(_content_query(database, tenant, 'SELECT upload_id FROM content_upload_intents WHERE platform_id=%s AND tenant_id=%s')))["upload_id"])

    class FailingAudit:
        @staticmethod
        def append_in_connection(*_args, **_kwargs):
            raise RuntimeError("injected audit failure")
    broken = MySqlMessageRepository(database, audit_repository=FailingAudit())
    broken_service = PersistentSessionService(sessions, runs, messages=broken, uploads=uploads, content_store=store)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        broken_service.submit_run(tenant, session.control_session_id, request, "rollback")
    assert len(runs.list(tenant, limit=100).items) == 1
    assert len(messages.list(tenant, session_id=session.control_session_id, limit=10).items) == 2

    queued = service.submit_run(tenant, session.control_session_id, request, "assistant-rollback")
    with pytest.raises(RuntimeError, match="injected audit failure"):
        broken_service.execute_run(worker, queued.run_id, request, engine, "worker")
    assert runs.get(tenant, queued.run_id).result_ref is None
    assert runs.get(tenant, queued.run_id).state is RunState.UNKNOWN
    assert messages.get_for_run(tenant, queued.run_id).status is MessageStatus.PENDING
    assert len(messages.list(tenant, session_id=session.control_session_id, limit=10).items) == 4

    intent = uploads.reserve(tenant, "MESSAGE_CONTENT", session_id=session.control_session_id, lease_seconds=30)
    renewed = uploads.renew(tenant, intent.upload_id, lease_seconds=120)
    assert renewed.lease_expires_at > intent.lease_expires_at
    store.put(tenant, intent.object_ref, b"expired content", "text/plain")
    with database.transaction() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE content_upload_intents SET lease_expires_at=CURRENT_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE platform_id=%s AND tenant_id=%s AND status='UPLOADING'", tenant.scope)
        cursor.close()
    expired = uploads.claim_expired(tenant, limit=100)
    assert intent.upload_id in {i.upload_id for i in expired}
    assert all(i.status == "EXPIRED" for i in expired)
    assert uploads.claim_expired(tenant) == ()
    with pytest.raises(LeaseLostError):
        uploads.renew(tenant, intent.upload_id)
    invalid_message = MessageRecord(message_id=generate_id(), tenant=tenant, session_id=session.control_session_id,
        run_id=first.run_id, sequence_no=0, role=MessageRole.ASSISTANT, status=MessageStatus.COMPLETE,
        content_ref=intent.object_ref, content_sha256=hashlib.sha256(b"expired content").hexdigest(),
        byte_length=15, content_type="text/plain")
    with pytest.raises(LeaseLostError):
        messages.complete(worker, invalid_message, upload_id=intent.upload_id, fencing_token=0)

    # Deletion queues the object before removing its only index and legacy result root.
    messages.delete(tenant, rows[1].message_id)
    with pytest.raises(ResourceNotFoundError):
        messages.get(tenant, rows[1].message_id)
    assert runs.get(tenant, first.run_id).result_ref is None
    queued_refs = {r["object_ref"] for r in _content_query(database, tenant, "SELECT object_ref FROM content_gc_queue WHERE platform_id=%s AND tenant_id=%s")}
    assert rows[1].content_ref in queued_refs
    assert {i.object_ref for i in expired} <= queued_refs
    for table in ("runs", "messages", "audit_events", "content_upload_intents", "content_gc_queue"):
        metadata = repr(_content_query(database, tenant, f"SELECT * FROM {table} WHERE platform_id=%s AND tenant_id=%s"))
        assert request.prompt not in metadata and result.text not in metadata


def _content_query(database, tenant, sql):
    with database.transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, tenant.scope)
        result = cursor.fetchall()
        cursor.close()
        return result


def test_mysql_repeated_atomic_submission_reuses_published_intent(content_database, tmp_path):
    import hashlib

    from harness_engine.persistence.content_store import LocalContentStore
    from harness_engine.persistence.models import MessageRecord
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    database, tenant, _, _, _, session = content_database
    uploads, messages = MySqlContentUploadRepository(database), MySqlMessageRepository(database)
    run = RunRecord(run_id=generate_id(), tenant=tenant, session_id=session.control_session_id,
                    idempotency_key=generate_id(), request_digest="a" * 64)
    message_id = generate_id()
    intent = uploads.reserve(tenant, "MESSAGE_CONTENT", session_id=run.session_id,
                             message_id=message_id, run_id=run.run_id)
    LocalContentStore(tmp_path, b"k" * 32).put(tenant, intent.object_ref, b"prompt", "text/plain")
    message = MessageRecord(message_id=message_id, tenant=tenant, session_id=run.session_id,
                            run_id=run.run_id, role="USER", status="COMPLETE",
                            content_ref=intent.object_ref, content_sha256=hashlib.sha256(b"prompt").hexdigest(),
                            byte_length=6, content_type="text/plain")
    first = messages.submit_run(tenant, run, message, upload_id=intent.upload_id)
    assert messages.submit_run(tenant, run, message, upload_id=intent.upload_id).message_ids == first.message_ids


def test_mysql_message_publication_rolls_back_after_message_insert(content_database, tmp_path):
    from harness_engine.core import EngineRequest
    from harness_engine.persistence.content_store import LocalContentStore
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    from harness_engine.session_service import PersistentSessionService
    database, tenant, sessions, runs, _, session = content_database
    class FailMessageAudit:
        @staticmethod
        def append_in_connection(connection, tenant, **event):
            if event["event_type"] == "message.created":
                raise RuntimeError("failure after message insert")
            MySqlAuditRepository.append_in_connection(connection, tenant, **event)
    uploads = MySqlContentUploadRepository(database)
    store = LocalContentStore(tmp_path, b"k" * 32)
    messages = MySqlMessageRepository(database, audit_repository=FailMessageAudit())
    service = PersistentSessionService(sessions, runs, messages=messages, uploads=uploads, content_store=store)
    with pytest.raises(RuntimeError, match="failure after message insert"):
        service.submit_run(tenant, session.control_session_id, EngineRequest(prompt="unpublished secret"), "rollback")
    assert not runs.list(tenant, limit=100).items
    assert not messages.list(tenant, session_id=session.control_session_id, limit=100).items
    sequence = _content_query(database, tenant, "SELECT last_message_sequence FROM sessions WHERE platform_id=%s AND tenant_id=%s")
    assert sequence[0]["last_message_sequence"] == 0
    events = _content_query(database, tenant, "SELECT event_type FROM audit_events WHERE platform_id=%s AND tenant_id=%s")
    assert [event["event_type"] for event in events] == ["session.created"]
    intents = _content_query(database, tenant, "SELECT * FROM content_upload_intents WHERE platform_id=%s AND tenant_id=%s")
    assert len(intents) == 1 and intents[0]["status"] == "UPLOADING"
    assert store.get(tenant, intents[0]["object_ref"]) == b"unpublished secret"
    assert not _content_query(database, tenant, "SELECT * FROM runs WHERE platform_id=%s AND tenant_id=%s")


def test_mysql_pending_assistant_expired_upload_and_stale_fencing(content_database, tmp_path):
    import hashlib
    from dataclasses import replace

    from harness_engine.core import EngineRequest
    from harness_engine.persistence.content_store import LocalContentStore
    from harness_engine.persistence.models import MessageRecord, MessageStatus
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    from harness_engine.session_service import PersistentSessionService
    database, tenant, sessions, runs, _, session = content_database
    uploads, messages = MySqlContentUploadRepository(database), MySqlMessageRepository(database)
    store = LocalContentStore(tmp_path, b"k" * 32)
    service = PersistentSessionService(sessions, runs, messages=messages, uploads=uploads, content_store=store)
    run = service.submit_run(tenant, session.control_session_id, EngineRequest(prompt="prompt"), "pending")
    lease = runs.claim(tenant, run.run_id, "worker")
    runs.transition(tenant, run.run_id, lease.fencing_token, RunState.RUNNING)
    pending = MessageRecord(message_id=generate_id(), tenant=tenant, session_id=session.control_session_id,
                             run_id=run.run_id, role="ASSISTANT", status="PENDING")
    for other in (replace(tenant, tenant_id=generate_id()), replace(tenant, platform_id=generate_id())):
        with pytest.raises(ResourceNotFoundError):
            messages.create(other, replace(pending, tenant=other), fencing_token=lease.fencing_token)
    pending = messages.create(tenant, pending, fencing_token=lease.fencing_token)
    assert pending.sequence_no == 2
    intent = uploads.reserve(tenant, "MESSAGE_CONTENT", session_id=run.session_id,
                             run_id=run.run_id, message_id=pending.message_id)
    store.put(tenant, intent.object_ref, b"answer", "text/plain")
    completed = replace(pending, status=MessageStatus.COMPLETE, content_ref=intent.object_ref,
                         content_sha256=hashlib.sha256(b"answer").hexdigest(), byte_length=6, content_type="text/plain")
    with pytest.raises(LeaseLostError):
        messages.complete(tenant, completed, upload_id=intent.upload_id, fencing_token=lease.fencing_token + 1)
    with database.transaction() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE content_upload_intents SET lease_expires_at=CURRENT_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE platform_id=%s AND tenant_id=%s AND upload_id=%s", (*tenant.scope, intent.upload_id))
        cursor.close()
    with pytest.raises(LeaseLostError):
        messages.complete(tenant, completed, upload_id=intent.upload_id, fencing_token=lease.fencing_token)
    assert messages.get(tenant, pending.message_id).status is MessageStatus.PENDING
    assert runs.get(tenant, run.run_id).state is RunState.RUNNING
    assert runs.get(tenant, run.run_id).result_ref is None
    assert uploads.claim_expired(tenant)[0].upload_id == intent.upload_id
    replacement = uploads.reserve(tenant, "MESSAGE_CONTENT", session_id=run.session_id,
                                  run_id=run.run_id, message_id=pending.message_id)
    store.put(tenant, replacement.object_ref, b"answer", "text/plain")
    completed = replace(completed, content_ref=replacement.object_ref)
    original = messages.complete(tenant, completed, upload_id=replacement.upload_id, fencing_token=lease.fencing_token)
    assert messages.complete(tenant, completed, upload_id=replacement.upload_id, fencing_token=lease.fencing_token) == original
    assert runs.get(tenant, run.run_id).result_ref == original.content_ref
    page = messages.list(tenant, session_id=run.session_id, limit=1)
    assert len(page.items) == 1 and page.next_cursor == "1"
    tail = messages.list(tenant, session_id=run.session_id, limit=1, cursor=page.next_cursor)
    assert tail.items[0].message_id == pending.message_id and tail.next_cursor is None


def test_mysql_stream_chunk_append_replay_and_worker_partial_finalization(content_database, tmp_path):
    import hashlib
    import json
    from dataclasses import replace

    from harness_engine.core import EngineRequest
    from harness_engine.persistence.content_store import LocalContentStore
    from harness_engine.persistence.models import MessageChunkRecord, MessageRecord
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    from harness_engine.session_service import PersistentSessionService

    database, tenant, sessions, runs, _engine, session = content_database
    worker = replace(tenant, user_id=generate_id(), roles=("worker",), auth_source="worker")
    uploads = MySqlContentUploadRepository(database)
    messages = MySqlMessageRepository(database)
    store = LocalContentStore(tmp_path, b"k" * 32)
    service = PersistentSessionService(
        sessions,
        runs,
        messages=messages,
        uploads=uploads,
        content_store=store,
    )
    submitted = service.submit_run(
        tenant,
        session.control_session_id,
        EngineRequest(prompt="stream prompt"),
        "stream-run",
    )
    lease = runs.claim(tenant, submitted.run_id, "stream-worker")
    runs.transition(tenant, submitted.run_id, lease.fencing_token, RunState.RUNNING)
    assistant = messages.create(
        worker,
        MessageRecord(
            message_id=generate_id(),
            tenant=tenant,
            session_id=submitted.session_id,
            run_id=submitted.run_id,
            role="ASSISTANT",
            status="PENDING",
        ),
        fencing_token=lease.fencing_token,
    )
    chunk_bytes = b"durable visible text"
    chunk_upload = uploads.reserve(
        tenant,
        "STREAM_CHUNK",
        session_id=submitted.session_id,
        message_id=assistant.message_id,
        run_id=submitted.run_id,
    )
    store.put(tenant, chunk_upload.object_ref, chunk_bytes, "text/plain; charset=utf-8")
    chunk = MessageChunkRecord(
        message_id=assistant.message_id,
        chunk_sequence=1,
        content_ref=chunk_upload.object_ref,
        content_sha256=hashlib.sha256(chunk_bytes).hexdigest(),
        byte_length=len(chunk_bytes),
    )
    messages.append_chunk(
        worker,
        chunk,
        upload_id=chunk_upload.upload_id,
        fencing_token=lease.fencing_token,
    )
    assert messages.chunk_high_water(tenant, assistant.message_id) == 1
    replay = messages.list_chunks_after(
        tenant, message_id=assistant.message_id, last_chunk_sequence=0
    )
    assert [item.content_ref for item in replay.items] == [chunk_upload.object_ref]
    assert store.get(tenant, replay.items[0].content_ref) == chunk_bytes
    with pytest.raises(ResourceNotFoundError):
        messages.list_chunks_after(
            replace(tenant, tenant_id=generate_id()),
            message_id=assistant.message_id,
            last_chunk_sequence=0,
        )

    manifest_bytes = json.dumps(
        {
            "version": 1,
            "chunks": [
                {
                    "sequence": item.chunk_sequence,
                    "content_ref": item.content_ref,
                    "content_sha256": item.content_sha256,
                    "byte_length": item.byte_length,
                }
                for item in replay.items
            ],
        },
        separators=(",", ":"),
    ).encode()
    manifest_upload = uploads.reserve(
        tenant,
        "MANIFEST",
        session_id=submitted.session_id,
        message_id=assistant.message_id,
        run_id=submitted.run_id,
    )
    store.put(
        tenant,
        manifest_upload.object_ref,
        manifest_bytes,
        "application/vnd.harness.manifest+json",
    )
    partial = replace(
        assistant,
        status="PARTIAL",
        content_ref=manifest_upload.object_ref,
        content_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        byte_length=len(manifest_bytes),
        content_type="application/vnd.harness.manifest+json",
    )
    finalized = messages.finalize_worker_partial(
        worker,
        partial,
        upload_id=manifest_upload.upload_id,
        fencing_token=lease.fencing_token,
        run_state=RunState.UNKNOWN,
    )
    assert finalized.status.value == "PARTIAL"
    assert finalized.content_ref == manifest_upload.object_ref
    assert runs.get(tenant, submitted.run_id).state is RunState.UNKNOWN
    assert runs.get(tenant, submitted.run_id).result_ref == manifest_upload.object_ref
    assert store.get(tenant, finalized.content_ref) == manifest_bytes
    assert messages.chunk_high_water(tenant, assistant.message_id) == 1


def test_mysql_service_stream_and_expired_worker_recovery(content_database, tmp_path):
    import hashlib
    import json
    from dataclasses import replace

    from harness_engine.core import (
        EngineCapabilities,
        EngineRequest,
        EngineResult,
        LifecycleEngine,
        RunStatus,
        SessionRef,
        VisibleTextDelta,
    )
    from harness_engine.persistence.content_store import ContentRef, LocalContentStore
    from harness_engine.persistence.models import (
        MessageChunkRecord,
        MessageRecord,
        MessageRole,
        MessageStatus,
        RunState,
    )
    from harness_engine.persistence.mysql import (
        MySqlContentUploadRepository,
        MySqlMessageRepository,
    )
    from harness_engine.session_service import PersistentSessionService

    database, tenant, sessions, runs, _engine, _existing_session = content_database
    worker = replace(tenant, user_id=generate_id(), roles=("worker",), auth_source="worker")
    system = replace(tenant, roles=("system",), auth_source="system")
    uploads = MySqlContentUploadRepository(database)
    messages = MySqlMessageRepository(database)
    store = LocalContentStore(tmp_path, b"s" * 32)

    class StreamingEngine(LifecycleEngine):
        capabilities = EngineCapabilities(streaming=True)

        def __init__(self):
            super().__init__()
            self.session = SessionRef("stream-native-session", "stream-test")

        def create_session(self):
            return self.session

        def resume_session(self, ref):
            return ref

        def run(self, request, on_event=None):
            text = ("x" * (32 * 1024), "assistant")
            for sequence, fragment in enumerate(text, start=1):
                on_event(VisibleTextDelta(
                    self.session.session_id, request.run_id, sequence, fragment
                ))
            result_text = "".join(text)
            return EngineResult(
                result_text,
                "completed",
                datetime.now(UTC),
                self.capabilities,
                request.run_id,
                self.session,
                RunStatus.SUCCEEDED,
            )

    engine = StreamingEngine()
    service = PersistentSessionService(
        sessions,
        runs,
        messages=messages,
        uploads=uploads,
        content_store=store,
    )
    session = service.create_session(tenant, engine)
    request = EngineRequest(prompt="stream integration")
    run = service.submit_run(tenant, session.control_session_id, request, "stream-service")
    result = service.execute_run(worker, run.run_id, request, engine, "stream-worker")
    assistant = messages.get_for_run(tenant, run.run_id)
    assert assistant is not None and assistant.status is MessageStatus.COMPLETE
    assert runs.get(tenant, run.run_id).result_ref == result.result_ref == assistant.content_ref
    manifest = json.loads(store.get(tenant, ContentRef(assistant.content_ref)))
    assert manifest["sha256"] == hashlib.sha256(result.text.encode()).hexdigest()
    assert "".join(
        chunk.text
        for chunk in service.tail_stream(
            tenant, assistant.message_id, last_chunk_sequence=0
        ).items
    ) == result.text
    resumed = service.tail_stream(
        tenant, assistant.message_id, last_chunk_sequence=1
    )
    assert [chunk.text for chunk in resumed.items] == ["assistant"]

    lost_run = service.submit_run(
        tenant,
        session.control_session_id,
        EngineRequest(prompt="worker loss"),
        "stream-worker-loss",
    )
    lease = runs.claim(tenant, lost_run.run_id, "lost-worker")
    runs.transition(tenant, lost_run.run_id, lease.fencing_token, RunState.RUNNING)
    pending = messages.create(
        worker,
        MessageRecord(
            message_id=generate_id(),
            tenant=tenant,
            session_id=session.control_session_id,
            run_id=lost_run.run_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.PENDING,
        ),
        fencing_token=lease.fencing_token,
    )
    chunk_data = b"last committed response"
    chunk_upload = uploads.reserve(
        tenant,
        "STREAM_CHUNK",
        session_id=session.control_session_id,
        message_id=pending.message_id,
        run_id=lost_run.run_id,
    )
    store.put(
        tenant,
        ContentRef(chunk_upload.object_ref),
        chunk_data,
        "text/plain; charset=utf-8",
    )
    messages.append_chunk(
        worker,
        MessageChunkRecord(
            message_id=pending.message_id,
            chunk_sequence=1,
            content_ref=chunk_upload.object_ref,
            content_sha256=hashlib.sha256(chunk_data).hexdigest(),
            byte_length=len(chunk_data),
        ),
        upload_id=chunk_upload.upload_id,
        fencing_token=lease.fencing_token,
    )
    with database.transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """UPDATE runs SET lease_expires_at=CURRENT_TIMESTAMP(6)-INTERVAL 1 SECOND
               WHERE platform_id=%s AND tenant_id=%s AND run_id=%s""",
            (*tenant.scope, lost_run.run_id),
        )
        cursor.close()

    expired = service.reconcile_expired_runs(system)

    assert any(item.run_id == lost_run.run_id for item in expired)
    recovered = messages.get_for_run(tenant, lost_run.run_id)
    assert recovered is not None and recovered.status is MessageStatus.PARTIAL
    assert runs.get(tenant, lost_run.run_id).state is RunState.UNKNOWN
    assert runs.get(tenant, lost_run.run_id).result_ref == recovered.content_ref
    assert service.tail_stream(
        tenant, recovered.message_id, last_chunk_sequence=0
    ).items[0].text == chunk_data.decode()



def test_real_mysql_tenant_idempotency_leases_fencing_and_rollback():
    from importlib import import_module

    mysql_errors = import_module("mysql.connector.errors")
    database = MySqlDatabase.from_config(MySqlConfig.from_env())
    assert int(database.health().split("=")[1]) >= 2
    platform_id = generate_id()
    tenant_a_id, tenant_b_id = generate_id(), generate_id()
    user_a_id, user_b_id = generate_id(), generate_id()
    session_id = generate_id()
    native_session_id = f"native-{generate_id()}"
    run_id, second_run_id = generate_id(), generate_id()
    idem, second_idem = generate_id(), generate_id()
    tenant_a = TenantContext(platform_id, tenant_a_id, user_a_id, ("owner",), "oidc")
    tenant_b = TenantContext(platform_id, tenant_b_id, user_b_id, ("owner",), "oidc")
    sessions = MySqlSessionRepository(database)
    audit = MySqlAuditRepository(database)
    runs = MySqlRunRepository(database, audit_repository=audit)

    try:
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s, %s, %s, 'ACTIVE')",
                    (platform_id, f"p-{platform_id}", "P4 integration"),
                )
                cursor.execute(
                    "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s, %s, %s, %s, 'ACTIVE'), (%s, %s, %s, %s, 'ACTIVE')",
                    (
                        platform_id,
                        tenant_a_id,
                        "tenant-a",
                        "Tenant A",
                        platform_id,
                        tenant_b_id,
                        "tenant-b",
                        "Tenant B",
                    ),
                )
                cursor.execute(
                    "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE'), (%s, %s, %s, %s, %s, %s, 'ACTIVE')",
                    (
                        platform_id,
                        tenant_a_id,
                        user_a_id,
                        f"subject-{user_a_id}",
                        f"user-{user_a_id}",
                        "User A",
                        platform_id,
                        tenant_b_id,
                        user_b_id,
                        f"subject-{user_b_id}",
                        f"user-{user_b_id}",
                        "User B",
                    ),
                )
            finally:
                cursor.close()

        session = SessionRecord(
            control_session_id=session_id,
            tenant=tenant_a,
            user_id=user_a_id,
            engine_name="fake",
            engine_session_id=native_session_id,
        )
        sessions.create(tenant_a, session)
        assert sessions.get(tenant_a, session_id).engine_session_id == native_session_id
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT event_type FROM audit_events WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                    (platform_id, tenant_a_id, session_id),
                )
                assert cursor.fetchone()[0] == "session.created"
            finally:
                cursor.close()
        with pytest.raises(ResourceNotFoundError):
            sessions.get(tenant_b, session_id)
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT last_message_sequence FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s", (platform_id, tenant_a_id, session_id))
                assert cursor.fetchone()[0] == 0
                cursor.execute("SELECT message_id, sequence_no, content_ref, last_committed_chunk_sequence FROM messages LIMIT 0")
                assert cursor.fetchall() == []
                cursor.execute("SELECT message_id, chunk_sequence, content_ref FROM message_chunks LIMIT 0")
                assert cursor.fetchall() == []
            finally:
                cursor.close()
        with pytest.raises(mysql_errors.IntegrityError), database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("INSERT INTO messages (platform_id, tenant_id, message_id, session_id, sequence_no, role, status) VALUES (%s, %s, %s, %s, 1, 'ASSISTANT', 'PENDING')", (platform_id, tenant_b_id, generate_id(), session_id))
            finally:
                cursor.close()
        with pytest.raises(mysql_errors.DatabaseError), database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("INSERT INTO messages (platform_id, tenant_id, message_id, session_id, sequence_no, role, status) VALUES (%s, %s, %s, %s, 0, 'ASSISTANT', 'PENDING')", (platform_id, tenant_a_id, generate_id(), session_id))
            finally:
                cursor.close()
        with pytest.raises(mysql_errors.IntegrityError), database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO sessions (platform_id, tenant_id, session_id, user_id, engine_name, engine_session_id, status) VALUES (%s, %s, %s, %s, 'fake', %s, 'ACTIVE')",
                    (
                        platform_id,
                        tenant_b_id,
                        generate_id(),
                        user_a_id,
                        f"native-{generate_id()}",
                    ),
                )
            finally:
                cursor.close()

        first = RunRecord(
            run_id=run_id,
            tenant=tenant_a,
            session_id=session_id,
            idempotency_key=idem,
            request_digest="a" * 64,
        )
        assert runs.create_or_get(tenant_a, first).run_id == run_id
        assert runs.create_or_get(tenant_a, first).run_id == run_id
        with pytest.raises(mysql_errors.DatabaseError), database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE runs SET state='NOT_A_STATE' WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
                    (platform_id, tenant_a_id, run_id),
                )
            finally:
                cursor.close()
        with pytest.raises(IdempotencyConflictError):
            runs.create_or_get(
                tenant_a,
                RunRecord(
                    run_id=generate_id(),
                    tenant=tenant_a,
                    session_id=session_id,
                    idempotency_key=idem,
                    request_digest="b" * 64,
                )
            )

        second = RunRecord(
            run_id=second_run_id,
            tenant=tenant_a,
            session_id=session_id,
            idempotency_key=second_idem,
            request_digest="c" * 64,
        )
        runs.create_or_get(tenant_a, second)

        # Lock the first queued row on a separate connection; claim_next must
        # skip it and claim the next queued row rather than block.
        lock_context = database.transaction()
        lock_connection = lock_context.__enter__()
        lock_cursor = lock_connection.cursor(dictionary=True)
        lock_cursor.execute(
            "SELECT run_id FROM runs WHERE platform_id=%s AND tenant_id=%s AND state='QUEUED' ORDER BY created_at, run_id LIMIT 1 FOR UPDATE",
            tenant_a.scope,
        )
        locked_id = lock_cursor.fetchone()["run_id"]
        lease = runs.claim_next(
            TenantContext(platform_id, tenant_a_id, generate_id(), ("worker",), "worker"),
            "worker-a",
        )
        lock_cursor.close()
        lock_context.__exit__(None, None, None)
        assert lease is not None
        assert lease.run_id != locked_id

        worker = TenantContext(platform_id, tenant_a_id, generate_id(), ("worker",), "worker")

        class FailingAudit:
            @staticmethod
            def append_in_connection(*_args, **_kwargs):
                raise RuntimeError("audit insert failed")

        broken = MySqlRunRepository(database, audit_repository=FailingAudit())
        with pytest.raises(RuntimeError, match="audit insert failed"):
            broken.transition(tenant_a, lease.run_id, lease.fencing_token, RunState.RUNNING)
        assert runs.get(tenant_a, lease.run_id).state is RunState.STARTING

        running = runs.transition(tenant_a, lease.run_id, lease.fencing_token, RunState.RUNNING)
        assert running.state is RunState.RUNNING
        with pytest.raises(LeaseLostError):
            runs.transition(tenant_a, lease.run_id, lease.fencing_token - 1, RunState.SUCCEEDED)
        with pytest.raises(LeaseLostError):
            audit.append(
                worker,
                event_type="run.state_changed",
                actor_type="worker",
                run_id=lease.run_id,
                fencing_token=lease.fencing_token - 1,
                details={"state": "SUCCEEDED"},
            )
        assert runs.request_cancel(tenant_a, lease.run_id).state is RunState.CANCELLING
        completed = runs.transition(
            tenant_a,
            lease.run_id,
            lease.fencing_token,
            RunState.SUCCEEDED,
            result_ref="object://tenant-a/result-1",
        )
        assert completed.state is RunState.SUCCEEDED
        assert completed.started_at is not None
        assert completed.finished_at is not None
        assert completed.result_ref == "object://tenant-a/result-1"
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT last_run_at FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                    (platform_id, tenant_a_id, session_id),
                )
                assert cursor.fetchone()[0] is not None
            finally:
                cursor.close()

        expiring_lease = runs.claim(tenant_a, locked_id, "worker-expiring")
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPDATE runs SET lease_expires_at=%s WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
                    (
                        datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
                        platform_id,
                        tenant_a_id,
                        locked_id,
                    ),
                )
            finally:
                cursor.close()
        expired = runs.expire_leases(tenant_a)
        assert len(expired) == 1
        assert expired[0].run_id == locked_id
        assert expired[0].state is RunState.UNKNOWN
        assert expired[0].fencing_token == expiring_lease.fencing_token + 1
        assert expired[0].finished_at is not None
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT last_run_at FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                    (platform_id, tenant_a_id, session_id),
                )
                assert cursor.fetchone()[0] is not None
            finally:
                cursor.close()
        with pytest.raises(LeaseLostError):
            runs.heartbeat(tenant_a, locked_id, expiring_lease.fencing_token)
    finally:
        with database.transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("DELETE FROM audit_events WHERE platform_id=%s AND tenant_id IN (%s, %s)", (platform_id, tenant_a_id, tenant_b_id))
                cursor.execute("DELETE FROM runs WHERE platform_id=%s AND tenant_id=%s", (platform_id, tenant_a_id))
                cursor.execute("DELETE FROM sessions WHERE platform_id=%s AND tenant_id IN (%s, %s)", (platform_id, tenant_a_id, tenant_b_id))
                cursor.execute("DELETE FROM users WHERE platform_id=%s AND tenant_id IN (%s, %s)", (platform_id, tenant_a_id, tenant_b_id))
                cursor.execute("DELETE FROM tenants WHERE platform_id=%s AND tenant_id IN (%s, %s)", (platform_id, tenant_a_id, tenant_b_id))
                cursor.execute("DELETE FROM platforms WHERE platform_id=%s", (platform_id,))
            finally:
                cursor.close()
