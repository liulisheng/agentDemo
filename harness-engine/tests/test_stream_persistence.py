import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import (
    EngineCapabilities,
    EngineRequest,
    EngineResult,
    HarnessEvent,
    LifecycleEngine,
    RunStatus,
    SessionRef,
    TenantContext,
    VisibleTextDelta,
)
from harness_engine.persistence.content_store import ContentRef, LocalContentStore
from harness_engine.persistence.errors import ResourceNotFoundError
from harness_engine.persistence.ids import generate_id
from harness_engine.persistence.models import (
    Lease,
    MessageChunkRecord,
    MessageRecord,
    MessageRole,
    MessageStatus,
    Page,
    RunState,
    UploadIntent,
)
from harness_engine.session_service import PersistentSessionService

USER = TenantContext("platform-1", "tenant-1", "user-1", ("member",), "oidc")
WORKER = TenantContext("platform-1", "tenant-1", "worker-1", ("worker",), "worker")
SYSTEM = TenantContext("platform-1", "tenant-1", "system-1", ("system",), "system")


class MemorySessions:
    def __init__(self):
        self.records = {}

    def create(self, tenant, record):
        self.records[record.control_session_id] = record
        return record

    def get(self, tenant, session_id):
        record = self.records.get(session_id)
        if record is None or record.tenant.scope != tenant.scope:
            raise ResourceNotFoundError("session not found")
        return replace(record, tenant=tenant)

    def list(self, tenant, *, limit, cursor=None):
        return Page(tuple(self.records.values())[:limit])

    def update_status(self, tenant, session_id, status):
        record = self.get(tenant, session_id)
        self.records[session_id] = replace(record, status=status)
        return self.records[session_id]


class MemoryRuns:
    def __init__(self):
        self.records = {}

    def create_or_get(self, tenant, record):
        self.records[record.run_id] = record
        return record

    def get(self, tenant, run_id):
        record = self.records.get(run_id)
        if record is None or record.tenant.scope != tenant.scope:
            raise ResourceNotFoundError("run not found")
        return replace(record, tenant=tenant)

    def claim(self, tenant, run_id, worker_id, *, lease_seconds=30):
        record = self.get(tenant, run_id)
        token = record.fencing_token + 1
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.records[run_id] = replace(
            record,
            state=RunState.STARTING,
            worker_id=worker_id,
            fencing_token=token,
            lease_expires_at=expires,
        )
        return Lease(run_id, worker_id, token, expires)

    def transition(self, tenant, run_id, fencing_token, state, *, result_ref=None, error_code=None):
        record = self.get(tenant, run_id)
        if record.fencing_token != fencing_token:
            raise RuntimeError("stale fencing token")
        self.records[run_id] = replace(
            record,
            state=state,
            result_ref=result_ref,
            error_code=error_code,
        )
        return self.records[run_id]

    def mark_unknown(self, tenant, run_id, fencing_token, *, error_code="UNKNOWN_RESULT"):
        return self.transition(tenant, run_id, fencing_token, RunState.UNKNOWN, error_code=error_code)

    def heartbeat(self, tenant, run_id, fencing_token, *, lease_seconds=30):
        record = self.get(tenant, run_id)
        if record.fencing_token != fencing_token:
            raise RuntimeError("stale fencing token")
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        self.records[run_id] = replace(record, lease_expires_at=expires)
        return Lease(run_id, record.worker_id or "worker", fencing_token, expires)

    def expire_leases(self, tenant, *, limit=100):
        expired = []
        for run_id, record in tuple(self.records.items()):
            if (record.tenant.scope == tenant.scope
                    and record.state in {RunState.STARTING, RunState.RUNNING, RunState.CANCELLING}
                    and record.lease_expires_at is not None
                    and record.lease_expires_at <= datetime.now(UTC)):
                updated = replace(
                    record,
                    state=RunState.UNKNOWN,
                    fencing_token=record.fencing_token + 1,
                    lease_expires_at=None,
                    error_code="RUN_LEASE_EXPIRED",
                )
                self.records[run_id] = updated
                expired.append(updated)
                if len(expired) >= limit:
                    break
        return tuple(expired)


class MemoryContent:
    def __init__(self, store):
        self.store = store
        self.runs = None
        self.messages = {}
        self.chunks = {}
        self.intents = {}
        self._sequences = {}

    def reserve(self, tenant, object_type, *, session_id=None, message_id=None,
                run_id=None, lease_seconds=300):
        intent = UploadIntent(
            generate_id(), tenant, generate_id(), object_type, "UPLOADING",
            datetime.now(UTC) + timedelta(seconds=lease_seconds),
            session_id=session_id, message_id=message_id, run_id=run_id,
        )
        self.intents[intent.upload_id] = intent
        return intent

    def submit_run(self, tenant, run, message, *, upload_id):
        self.store.get(tenant, ContentRef(message.content_ref))
        stored = self.runs.create_or_get(tenant, run)
        self.messages[message.message_id] = replace(
            message, sequence_no=self._next_sequence(message.session_id)
        )
        self.intents[upload_id] = replace(self.intents[upload_id], status="PUBLISHED")
        return replace(stored, message_ids=(message.message_id,))

    def create(self, tenant, record, *, upload_id=None, fencing_token=None):
        message = replace(record, sequence_no=self._next_sequence(record.session_id))
        self.messages[message.message_id] = message
        return message

    def append_chunk(self, tenant, record, *, upload_id, fencing_token):
        intent = self.intents[upload_id]
        assert intent.object_type == "STREAM_CHUNK"
        assert intent.object_ref == record.content_ref
        assert intent.status == "UPLOADING"
        run = self.runs.get(tenant, intent.run_id)
        assert run.fencing_token == fencing_token
        current = self.chunks.setdefault(record.message_id, [])
        expected = len(current) + 1
        if record.chunk_sequence != expected:
            raise ValueError("chunk sequence must be contiguous")
        data = self.store.get(tenant, ContentRef(record.content_ref))
        assert hashlib.sha256(data).hexdigest() == record.content_sha256
        current.append(record)
        message = self.messages[record.message_id]
        self.messages[record.message_id] = replace(
            message, status=MessageStatus.STREAMING,
            updated_at=datetime.now(UTC),
        )
        self.intents[upload_id] = replace(intent, status="PUBLISHED")
        return record

    def chunk_high_water(self, tenant, message_id):
        self._message(tenant, message_id)
        return len(self.chunks.get(message_id, ()))

    def list_chunks_after(self, tenant, *, message_id, last_chunk_sequence, limit=100):
        self._message(tenant, message_id)
        high_water = self.chunk_high_water(tenant, message_id)
        if last_chunk_sequence > high_water:
            raise ValueError("cursor is ahead of committed chunk sequence")
        chunks = self.chunks.get(message_id, [])
        items = tuple(chunks[last_chunk_sequence:last_chunk_sequence + limit])
        return Page(items, str(items[-1].chunk_sequence) if last_chunk_sequence + len(items) < high_water else None)

    def complete(self, tenant, message, *, upload_id, fencing_token):
        intent = self.intents[upload_id]
        assert intent.object_type in {"MESSAGE_CONTENT", "MANIFEST"}
        assert intent.object_ref == message.content_ref
        self.runs.transition(tenant, message.run_id, fencing_token, RunState.SUCCEEDED,
                             result_ref=message.content_ref)
        current = self.messages.get(message.message_id)
        self.messages[message.message_id] = replace(
            message,
            sequence_no=current.sequence_no if current else self._next_sequence(message.session_id),
            completed_at=datetime.now(UTC),
        )
        self.intents[upload_id] = replace(intent, status="PUBLISHED")
        return self.messages[message.message_id]

    def finalize_worker_partial(self, tenant, record, *, upload_id, fencing_token,
                                run_state=RunState.UNKNOWN, error_code=None):
        run = self.runs.get(tenant, record.run_id)
        assert run.fencing_token == fencing_token
        assert self.chunk_high_water(tenant, record.message_id) > 0
        intent = self.intents[upload_id]
        assert intent.object_type == "MANIFEST" and intent.object_ref == record.content_ref
        self.runs.transition(tenant, record.run_id, fencing_token, run_state,
                             result_ref=record.content_ref, error_code=error_code)
        current = self.messages[record.message_id]
        self.messages[record.message_id] = replace(
            record, sequence_no=current.sequence_no, completed_at=datetime.now(UTC)
        )
        self.intents[upload_id] = replace(intent, status="PUBLISHED")
        return self.messages[record.message_id]

    def recover_partial(self, tenant, record, *, upload_id, fencing_token):
        run = self.runs.get(tenant, record.run_id)
        assert run.state is RunState.UNKNOWN and run.fencing_token == fencing_token
        assert self.chunk_high_water(tenant, record.message_id) > 0
        intent = self.intents[upload_id]
        assert intent.object_type == "MANIFEST" and intent.object_ref == record.content_ref
        self.runs.records[run.run_id] = replace(run, result_ref=record.content_ref)
        current = self.messages[record.message_id]
        self.messages[record.message_id] = replace(
            record, sequence_no=current.sequence_no, completed_at=datetime.now(UTC)
        )
        self.intents[upload_id] = replace(intent, status="PUBLISHED")
        return self.messages[record.message_id]

    def fail_pending_message(self, tenant, message_id, *, fencing_token,
                             terminal_state=RunState.FAILED, error_code="ENGINE_FAILED"):
        message = self._message(tenant, message_id)
        assert message.status is MessageStatus.PENDING
        assert self.chunk_high_water(tenant, message_id) == 0
        self.runs.transition(tenant, message.run_id, fencing_token, terminal_state,
                             error_code=error_code)
        self.messages[message_id] = replace(message, status=MessageStatus.FAILED)
        return self.messages[message_id]

    def fail_expired_pending(self, tenant, message_id, *, fencing_token):
        message = self._message(tenant, message_id)
        run = self.runs.get(tenant, message.run_id)
        assert run.state is RunState.UNKNOWN and run.fencing_token == fencing_token
        assert self.chunk_high_water(tenant, message_id) == 0
        self.messages[message_id] = replace(message, status=MessageStatus.FAILED)
        return self.messages[message_id]

    def get_for_run(self, tenant, run_id, role=MessageRole.ASSISTANT):
        return next((m for m in self.messages.values()
                     if m.tenant.scope == tenant.scope and m.run_id == run_id and m.role is role), None)

    def list_unfinalized_streams(self, tenant, *, limit=100):
        values = []
        for message in self.messages.values():
            if message.tenant.scope != tenant.scope or message.status not in {
                MessageStatus.PENDING, MessageStatus.STREAMING,
            }:
                continue
            run = self.runs.get(tenant, message.run_id)
            if run.state is RunState.UNKNOWN:
                values.append((run, message))
        return tuple(values[:limit])

    def _message(self, tenant, message_id):
        message = self.messages.get(message_id)
        if message is None or message.tenant.scope != tenant.scope:
            raise ResourceNotFoundError("message not found")
        return message

    def _next_sequence(self, session_id):
        sequence = self._sequences.get(session_id, 0) + 1
        self._sequences[session_id] = sequence
        return sequence


class StreamingEngine(LifecycleEngine):
    capabilities = EngineCapabilities(streaming=True)

    def __init__(self, store, repository, pieces):
        super().__init__()
        self.store = store
        self.repository = repository
        self.pieces = pieces
        self.session = SessionRef("engine-session-1", "stream-test")

    def create_session(self):
        return self.session

    def resume_session(self, ref):
        return ref

    def run(self, request, on_event=None):
        self._require_started()
        pending = self.repository.get_for_run(WORKER, request.run_id)
        assert pending is not None and pending.status is MessageStatus.PENDING
        visible = []
        for sequence, piece in enumerate(self.pieces, 1):
            visible.append(piece)
            if on_event is not None:
                on_event(VisibleTextDelta(self.session.session_id, request.run_id, sequence, piece))
        if on_event is not None:
            on_event(HarnessEvent(
                "vendor_event", session_id=self.session.session_id,
                run_id=request.run_id,
                payload={"reasoning": "must never be persisted", "tool_args": {"secret": "x"}},
            ))
        text = "".join(visible)
        return EngineResult(text, "completed", datetime.now(UTC), self.capabilities,
                            request.run_id, self.session, RunStatus.SUCCEEDED)


def make_service(tmp_path, pieces):
    store = LocalContentStore(tmp_path, b"s" * 32)
    runs = MemoryRuns()
    repo = MemoryContent(store)
    repo.runs = runs
    sessions = MemorySessions()
    service = PersistentSessionService(
        sessions, runs, id_factory=generate_id,
        messages=repo, uploads=repo, content_store=store,
    )
    engine = StreamingEngine(store, repo, pieces)
    session = service.create_session(USER, engine)
    return service, engine, session, repo, store


def submit_and_execute(service, engine, session, prompt="prompt"):
    request = EngineRequest(prompt=prompt)
    run = service.submit_run(USER, session.control_session_id, request, generate_id())
    result = service.execute_run(WORKER, run.run_id, request, engine, "worker-1")
    assistant = next(m for m in service.messages.messages.values()
                     if m.run_id == run.run_id and m.role is MessageRole.ASSISTANT)
    return run, result, assistant


def test_stream_checkpoints_manifest_and_tail_return_only_visible_committed_text(tmp_path, monkeypatch):
    import harness_engine.session_service as service_module

    monkeypatch.setattr(service_module, "_STREAM_CHUNK_BYTES", 5)
    service, engine, session, repo, store = make_service(tmp_path, ["hello", " world"])
    run, result, assistant = submit_and_execute(service, engine, session)

    assert assistant.status is MessageStatus.COMPLETE
    assert [c.chunk_sequence for c in repo.chunks[assistant.message_id]] == [1, 2, 3]
    manifest = json.loads(store.get(USER, ContentRef(assistant.content_ref)))
    assert manifest["sha256"] == hashlib.sha256(result.text.encode()).hexdigest()
    assert manifest["byte_length"] == len(result.text.encode())
    assert result.result_ref == assistant.content_ref == repo.runs.get(USER, run.run_id).result_ref

    tail = service.tail_stream(USER, assistant.message_id, last_chunk_sequence=1)
    assert [(item.chunk_sequence, item.text) for item in tail.items] == [
        (2, " worl"), (3, "d")
    ]
    with pytest.raises(ValueError, match="ahead"):
        service.tail_stream(USER, assistant.message_id, last_chunk_sequence=4)
    assert "must never be persisted" not in repr(manifest)
    assert "tool_args" not in repr(manifest)


def test_stream_flushes_small_buffer_after_two_seconds(tmp_path, monkeypatch):
    import harness_engine.session_service as service_module

    monkeypatch.setattr(service_module, "_STREAM_CHUNK_BYTES", 1024)
    monkeypatch.setattr(service_module, "_STREAM_FLUSH_INTERVAL_SECONDS", 0.05)
    service, _, session, repo, _ = make_service(tmp_path, ["small"])

    class WaitingEngine(StreamingEngine):
        def run(self, request, on_event=None):
            self._require_started()
            if on_event is not None:
                on_event(VisibleTextDelta(self.session.session_id, request.run_id, 1, "small"))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not repo.chunks:
                time.sleep(0.005)
            assert repo.chunks, "timer did not commit the small buffer"
            return EngineResult("small", "completed", datetime.now(UTC), self.capabilities,
                                request.run_id, self.session, RunStatus.SUCCEEDED)

    engine = WaitingEngine(service.content_store, repo, ["small"])
    # Reuse the already persisted session identity for the streaming engine.
    engine.session = SessionRef(session.engine_session_id, session.engine_name)
    service.submit_run(USER, session.control_session_id,
                       EngineRequest(prompt="prompt"), generate_id())
    queued = next(iter(repo.runs.records.values()))
    service.execute_run(WORKER, queued.run_id, EngineRequest(prompt="prompt"), engine, "worker-1")
    assert repo.chunks


def test_stream_splits_large_delta_at_32_kib_checkpoint_boundary(tmp_path):
    service, engine, session, repo, _store = make_service(
        tmp_path, ["a" * (32 * 1024 + 1)]
    )
    _, _, assistant = submit_and_execute(service, engine, session)

    chunks = repo.chunks[assistant.message_id]
    assert [chunk.byte_length for chunk in chunks] == [32 * 1024, 1]
    assert all(chunk.byte_length <= 32 * 1024 for chunk in chunks)


def test_stream_checkpoint_boundaries_preserve_utf8_text(tmp_path):
    text = "a" * (32 * 1024 - 1) + "界"
    service, engine, session, repo, _store = make_service(tmp_path, [text])
    _, _, assistant = submit_and_execute(service, engine, session)

    chunks = repo.chunks[assistant.message_id]
    assert [chunk.byte_length for chunk in chunks] == [32 * 1024 - 1, 3]
    tail = service.tail_stream(USER, assistant.message_id, last_chunk_sequence=0)
    assert "".join(chunk.text for chunk in tail.items) == text


def test_engine_failure_keeps_committed_chunks_as_partial_unknown(tmp_path, monkeypatch):
    import harness_engine.session_service as service_module

    monkeypatch.setattr(service_module, "_STREAM_CHUNK_BYTES", 1)
    service, engine, session, repo, _ = make_service(tmp_path, [])
    request = EngineRequest(prompt="prompt")
    run = service.submit_run(USER, session.control_session_id, request, generate_id())

    def fail_after_delta(engine_request, on_event=None):
        on_event(VisibleTextDelta(
            session.engine_session_id, engine_request.run_id, 1, "partial answer"
        ))
        raise RuntimeError("engine disconnected")

    engine.run = fail_after_delta
    with pytest.raises(RuntimeError, match="engine disconnected"):
        service.execute_run(WORKER, run.run_id, request, engine, "worker-1")

    assistant = repo.get_for_run(USER, run.run_id)
    assert assistant.status is MessageStatus.PARTIAL
    assert repo.runs.get(USER, run.run_id).state is RunState.UNKNOWN
    tail = service.tail_stream(USER, assistant.message_id, last_chunk_sequence=0)
    assert "".join(chunk.text for chunk in tail.items) == "partial answer"


def test_reaper_turns_committed_prefix_into_partial_manifest(tmp_path):
    service, _engine, session, repo, store = make_service(tmp_path, [])
    request = EngineRequest(prompt="prompt")
    queued = service.submit_run(USER, session.control_session_id, request, generate_id())
    lease = repo.runs.claim(WORKER, queued.run_id, "worker-1")
    repo.runs.transition(WORKER, queued.run_id, lease.fencing_token, RunState.RUNNING)
    message = repo.create(
        WORKER,
        MessageRecord(
            message_id=generate_id(),
            tenant=WORKER,
            session_id=session.control_session_id,
            run_id=queued.run_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.PENDING,
        ),
        fencing_token=lease.fencing_token,
    )
    assert service.tail_stream(
        USER, message.message_id, last_chunk_sequence=0
    ).items == ()
    intent = repo.reserve(
        WORKER,
        "STREAM_CHUNK",
        session_id=session.control_session_id,
        message_id=message.message_id,
        run_id=queued.run_id,
    )
    data = b"committed before worker loss"
    store.put(WORKER, ContentRef(intent.object_ref), data, "text/plain; charset=utf-8")
    repo.append_chunk(
        WORKER,
        MessageChunkRecord(
            message_id=message.message_id,
            chunk_sequence=1,
            content_ref=intent.object_ref,
            content_sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
        ),
        upload_id=intent.upload_id,
        fencing_token=lease.fencing_token,
    )
    repo.runs.records[queued.run_id] = replace(
        repo.runs.records[queued.run_id],
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    expired = service.reconcile_expired_runs(SYSTEM)

    assert expired[0].state is RunState.UNKNOWN
    partial = repo.get_for_run(USER, queued.run_id)
    assert partial.status is MessageStatus.PARTIAL
    manifest = json.loads(store.get(USER, ContentRef(partial.content_ref)))
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
    assert repo.runs.get(USER, queued.run_id).result_ref == partial.content_ref
    tail = service.tail_stream(USER, partial.message_id, last_chunk_sequence=0)
    assert tail.items[0].text == data.decode()
