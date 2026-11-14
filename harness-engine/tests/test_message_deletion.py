from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import EngineRequest, TenantContext
from harness_engine.engines.fake import FakeEngine
from harness_engine.persistence.content_store import (
    ContentNotFoundError,
    ContentObjectInfo,
    ContentRef,
)
from harness_engine.persistence.errors import AuthorizationError, ResourceNotFoundError
from harness_engine.persistence.ids import generate_id
from harness_engine.persistence.models import DeleteResult, Page
from harness_engine.persistence.mysql import (
    MySqlConfig,
    MySqlContentGarbageCollector,
    MySqlContentGcRepository,
    MySqlContentUploadRepository,
    MySqlDatabase,
    MySqlMessageRepository,
    MySqlRunRepository,
    MySqlSessionRepository,
)
from harness_engine.session_service import PersistentSessionService

TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="user-1",
    roles=("owner",),
    auth_source="oidc",
)
MEMBER = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="user-1",
    roles=("member",),
    auth_source="oidc",
)


class _SessionRepository:
    def __init__(self):
        self.deleted = []

    def delete_history(self, tenant, session_id):
        self.deleted.append((tenant, session_id))
        return DeleteResult(session_id=session_id, deleted=len(self.deleted) == 1, queued_objects=3)


class _RunRepository:
    pass


def test_delete_session_history_requires_admin_and_is_idempotent():
    sessions = _SessionRepository()
    service = PersistentSessionService(sessions, _RunRepository())

    with pytest.raises(AuthorizationError):
        service.delete_session_history(MEMBER, "session-1")

    first = service.delete_session_history(TENANT, "session-1")
    second = service.delete_session_history(TENANT, "session-1")

    assert first == DeleteResult(session_id="session-1", deleted=True, queued_objects=3)
    assert second == DeleteResult(session_id="session-1", deleted=False, queued_objects=3)
    assert len(sessions.deleted) == 2


class _FakeDatabase:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @contextmanager
    def transaction(self):
        yield _FakeConnection(self)


class _FakeConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self, **_kwargs):
        return _FakeCursor(self.database)


class _FakeCursor:
    def __init__(self, database):
        self.database = database
        self.rowcount = 0
        self.rows = []

    def execute(self, sql, params=()):
        self.database.calls.append((sql, tuple(params)))
        response = self.database.responses.pop(0) if self.database.responses else ([], 0)
        self.rows, self.rowcount = response

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


def _session_row():
    now = datetime.now(UTC)
    return {
        "session_id": "session-1",
        "harness_home_ref": "home-ref",
        "workspace_ref": "workspace-ref",
        "created_at": now,
        "updated_at": now,
    }


def test_delete_history_queues_session_roots_and_expires_uploads_atomically():
    responses = [
        ([_session_row()], 1),
        ([{"run_id": "run-1", "result_ref": "legacy-result"}], 1),
        ([{"content_ref": "manifest-ref", "run_id": "run-1"}], 1),
        ([{"content_ref": "chunk-ref"}], 1),
        ([{"object_ref": "upload-ref", "object_type": "MESSAGE_CONTENT", "status": "UPLOADING"}], 1),
        ([], 1), ([], 1), ([], 1), ([], 1), ([], 1), ([], 1), ([], 1),
    ]
    database = _FakeDatabase(responses)
    from harness_engine.persistence.mysql import MySqlSessionRepository

    result = MySqlSessionRepository(database).delete_history(TENANT, "session-1")

    assert result == DeleteResult(session_id="session-1", deleted=True, queued_objects=6)
    sql = "\n".join(call[0] for call in database.calls)
    assert "platform_id = %s AND tenant_id = %s" in sql
    assert "content_gc_queue" in sql
    assert "content_upload_intents" in sql
    assert "audit_events" in sql
    assert "DELETE FROM sessions" in sql
    assert all(call[1][:2] == TENANT.scope for call in database.calls if len(call[1]) >= 2)


def test_gc_retries_object_store_failure_without_restoring_read_access():
    class Store:
        def __init__(self):
            self.deleted = []

        def delete(self, tenant, ref):
            self.deleted.append((tenant, ref))
            raise OSError("object store unavailable")

        def list_candidates(self, tenant, older_than, cursor=None):
            return Page((ContentObjectInfo(ContentRef("orphan"), older_than - timedelta(seconds=1), 3, "text/plain"),))

    class GcRepository:
        def __init__(self):
            self.claimed = [("cleanup-1", "orphan", "MESSAGE_CONTENT", 0)]
            self.completed = []
            self.enqueued = 0

        def enqueue_orphans(self, tenant, store, *, older_than, limit):
            self.enqueued += 1
            return 1

        def claim(self, tenant, *, limit):
            return tuple(self.claimed)

        def complete(self, tenant, cleanup_id, *, success, error_code=None):
            self.completed.append((cleanup_id, success, error_code))

    store = Store()
    repository = GcRepository()
    processed = MySqlContentGarbageCollector(repository).run_once(
        TENANT,
        store,
        older_than=datetime.now(UTC),
    )

    assert processed == 1
    assert repository.completed == [("cleanup-1", False, "OBJECT_DELETE_FAILED")]
    assert store.deleted == [(TENANT, "orphan")]


def test_gc_orphan_scan_uses_grace_period_and_preserves_roots():
    class Store:
        def __init__(self):
            self.calls = []

        def list_candidates(self, tenant, older_than, cursor=None):
            self.calls.append((tenant, older_than, cursor))
            return Page(())

    class GcRepository:
        def enqueue_orphans(self, tenant, store, *, older_than, limit):
            self.args = (tenant, store, older_than, limit)
            return 0

    store = Store()
    repository = GcRepository()
    older_than = datetime.now(UTC) - timedelta(days=2)
    assert MySqlContentGarbageCollector(repository).enqueue_orphans(
        TENANT, store, older_than=older_than, limit=25
    ) == 0
    assert repository.args[2] == older_than
    assert repository.args[3] == 25


def test_mysql_gc_claim_and_complete_are_tenant_scoped():
    database = _FakeDatabase([
        ([{"cleanup_id": "cleanup-1", "object_ref": "orphan", "object_type": "MESSAGE_CONTENT", "attempt_count": 2}], 1),
        ([], 1),
        ([], 1),
    ])
    repository = MySqlContentGcRepository(database)

    claimed = repository.claim(TENANT, limit=10)
    repository.complete(TENANT, "cleanup-1", success=False, error_code="OBJECT_DELETE_FAILED")

    assert claimed[0].cleanup_id == "cleanup-1"
    assert claimed[0].attempt_count == 2
    assert database.calls[0][1][:2] == TENANT.scope
    assert database.calls[1][1][:2] == TENANT.scope
    assert database.calls[2][1][1:3] == TENANT.scope
    assert "FOR UPDATE SKIP LOCKED" in database.calls[0][0]
    assert "status = 'RETRY'" in database.calls[2][0]


def test_mysql_gc_orphan_scan_requeues_deleted_late_put():
    class Store:
        def list_candidates(self, tenant, older_than, cursor=None):
            return Page((ContentObjectInfo(ContentRef("late-put"), older_than - timedelta(seconds=1), 3, "text/plain"),))

    database = _FakeDatabase([
        ([], 0),
        ([], 1),
    ])
    repository = MySqlContentGcRepository(database)

    assert repository.enqueue_orphans(
        TENANT,
        Store(),
        older_than=datetime.now(UTC),
        limit=10,
    ) == 1

    queue_sql = "\n".join(call[0] for call in database.calls)
    assert "status = IF(status = 'DELETED', 'QUEUED', status)" in queue_sql
    assert "SELECT result_ref FROM runs" in queue_sql
    assert all(call[1][:2] == TENANT.scope for call in database.calls)


@pytest.mark.skipif(
    os.environ.get("HARNESS_MYSQL_INTEGRATION") != "1",
    reason="set HARNESS_MYSQL_INTEGRATION=1 to run real MySQL tests",
)
def test_mysql_session_delete_revokes_history_and_queues_content(tmp_path):
    database = MySqlDatabase.from_config(MySqlConfig.from_env())
    platform_id, tenant_id, user_id = generate_id(), generate_id(), generate_id()
    tenant = TenantContext(platform_id, tenant_id, user_id, ("owner",), "oidc")
    with database.transaction() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,'Delete test','ACTIVE')",
            (platform_id, platform_id),
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,'delete','Delete test','ACTIVE')",
            tenant.scope,
        )
        cursor.execute(
            "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) VALUES (%s,%s,%s,%s,%s,'Delete','ACTIVE')",
            (*tenant.scope, user_id, user_id, user_id),
        )
        cursor.close()

    sessions = MySqlSessionRepository(database)
    runs = MySqlRunRepository(database)
    uploads = MySqlContentUploadRepository(database)
    messages = MySqlMessageRepository(database)
    from harness_engine.persistence.content_store import LocalContentStore

    store = LocalContentStore(tmp_path, b"d" * 32)
    service = PersistentSessionService(
        sessions,
        runs,
        messages=messages,
        uploads=uploads,
        content_store=store,
    )
    engine = FakeEngine()
    session = service.create_session(
        tenant,
        engine,
        harness_home_ref="home-ref",
        workspace_ref="workspace-ref",
    )
    request = EngineRequest(prompt="delete me")
    submitted = service.submit_run(tenant, session.control_session_id, request, "delete-key")
    worker = TenantContext(platform_id, tenant_id, generate_id(), ("worker",), "worker")
    service.execute_run(worker, submitted.run_id, request, engine, "worker-1")
    pending_upload = uploads.reserve(
        tenant,
        "WORKSPACE",
        session_id=session.control_session_id,
        lease_seconds=300,
    )

    try:
        result = sessions.delete_history(tenant, session.control_session_id)
        assert result.deleted is True
        assert result.queued_objects >= 4
        with pytest.raises(ResourceNotFoundError):
            sessions.get(tenant, session.control_session_id)
        with pytest.raises(ResourceNotFoundError):
            messages.list(tenant, session_id=session.control_session_id, limit=10)
        with pytest.raises(ResourceNotFoundError):
            uploads.renew(tenant, pending_upload.upload_id)
        with database.transaction() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT COUNT(*) AS n FROM content_gc_queue WHERE platform_id=%s AND tenant_id=%s",
                tenant.scope,
            )
            assert cursor.fetchone()["n"] >= 4
            cursor.execute(
                "SELECT COUNT(*) AS n FROM content_upload_intents WHERE platform_id=%s AND tenant_id=%s",
                tenant.scope,
            )
            assert cursor.fetchone()["n"] == 0
            cursor.close()
        assert sessions.delete_history(tenant, session.control_session_id).deleted is False
        assert pending_upload.object_ref in {
            row["object_ref"]
            for row in _content_query(database, tenant, "SELECT object_ref FROM content_gc_queue WHERE platform_id=%s AND tenant_id=%s")
        }
        garbage_collector = MySqlContentGarbageCollector(
            MySqlContentGcRepository(database)
        )
        first_pass = garbage_collector.run_once(
            tenant,
            store,
            older_than=datetime.now(UTC) + timedelta(seconds=1),
            limit=100,
        )
        assert first_pass >= 4
        store.put(tenant, ContentRef(pending_upload.object_ref), b"late put", "text/plain")
        second_pass = garbage_collector.run_once(
            tenant,
            store,
            older_than=datetime.now(UTC) + timedelta(seconds=1),
            limit=100,
        )
        assert second_pass >= 1
        with pytest.raises(ContentNotFoundError):
            store.get(tenant, ContentRef(pending_upload.object_ref))
    finally:
        with database.transaction() as connection:
            cursor = connection.cursor()
            for table in (
                "message_chunks",
                "messages",
                "content_gc_queue",
                "content_upload_intents",
                "audit_events",
                "runs",
                "sessions",
                "users",
                "tenants",
                "platforms",
            ):
                cursor.execute(f"DELETE FROM {table} WHERE platform_id=%s", (platform_id,))
            cursor.close()


def _content_query(database, tenant, sql):
    with database.transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql, tenant.scope)
        rows = cursor.fetchall()
        cursor.close()
        return rows
