"""Coordinate engine-neutral lifecycle calls with persistent control records."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .core import (
    CancelResult,
    EngineClosedError,
    EngineNotStartedError,
    EngineRequest,
    EngineResult,
    EngineState,
    HarnessEngine,
    RunStatus,
    SessionMismatchError,
    SessionRef,
    TenantContext,
    UnsupportedCapabilityError,
    VisibleTextDelta,
)
from .persistence.content_store import ContentRef, ContentStore
from .persistence.errors import (
    AuthorizationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    SessionCreationError,
)
from .persistence.ids import generate_id
from .persistence.models import (
    DeleteResult,
    MessageChunkRecord,
    MessageRecord,
    MessageRole,
    MessageStatus,
    Page,
    RunRecord,
    RunState,
    SessionRecord,
    SessionStatus,
    canonical_request_digest,
)
from .persistence.protocols import (
    ContentUploadRepository,
    MessageRepository,
    RunRepository,
    SessionRepository,
)

_MUTATING_ROLES = frozenset({"member", "owner", "admin"})
_READ_ROLES = frozenset({"member", "owner", "admin", "viewer"})
_ADMIN_ROLES = frozenset({"owner", "admin"})
_logger = logging.getLogger(__name__)
_STREAM_CHUNK_BYTES = 32 * 1024
_STREAM_FLUSH_INTERVAL_SECONDS = 2.0
_STREAM_HEARTBEAT_SECONDS = 10.0
_STREAM_CHUNK_CONTENT_TYPE = "text/plain; charset=utf-8"
_STREAM_MANIFEST_CONTENT_TYPE = "application/vnd.harness.message-manifest+json"


@dataclass(frozen=True, slots=True)
class DurableTextChunk:
    """A committed text checkpoint returned by the durable tail API."""

    chunk_sequence: int
    text: str
    content_sha256: str
    byte_length: int
    created_at: datetime


class _StreamCheckpointWriter:
    """Bounded text buffer that durably checkpoints by size or elapsed time."""

    def __init__(
        self,
        *,
        tenant: TenantContext,
        run: RunRecord,
        message_id: str,
        session_id: str,
        fencing_token: int,
        messages: MessageRepository,
        uploads: ContentUploadRepository,
        content_store: ContentStore,
        runs: RunRepository,
    ) -> None:
        self.tenant = tenant
        self.run = run
        self.message_id = message_id
        self.session_id = session_id
        self.fencing_token = fencing_token
        self.messages = messages
        self.uploads = uploads
        self.content_store = content_store
        self.runs = runs
        self._buffer = bytearray()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_flush = time.monotonic()
        self._last_heartbeat = self._last_flush
        self._last_delta_sequence = 0
        self._committed_sequence = 0
        self._delta_count = 0
        self._total_bytes = 0
        self._content_hash = hashlib.sha256()
        self._failure: Exception | None = None
        self._closed = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._timer_loop,
            name=f"stream-checkpoint-{self.run.run_id}",
            daemon=True,
        )
        self._thread.start()

    def accept(self, delta: VisibleTextDelta) -> None:
        with self._lock:
            self._raise_if_failed()
            if delta.session_id != self.session_id or delta.run_id != self.run.run_id:
                raise ValueError("visible text delta does not match the active run")
            if delta.sequence != self._last_delta_sequence + 1:
                raise ValueError("visible text delta sequence must be contiguous")
            data = delta.text.encode("utf-8")
            self._last_delta_sequence = delta.sequence
            self._delta_count += 1
            self._total_bytes += len(data)
            self._content_hash.update(data)
            for character in delta.text:
                encoded = character.encode("utf-8")
                if self._buffer and len(self._buffer) + len(encoded) > _STREAM_CHUNK_BYTES:
                    self._flush_locked()
                self._buffer.extend(encoded)
                if len(self._buffer) >= _STREAM_CHUNK_BYTES:
                    self._flush_locked()

    def finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            if self._closed:
                self._raise_if_failed()
                return
            self._raise_if_failed()
            self._flush_locked()
            self._closed = True

    @property
    def delta_count(self) -> int:
        with self._lock:
            return self._delta_count

    @property
    def committed_sequence(self) -> int:
        with self._lock:
            return self._committed_sequence

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def content_sha256(self) -> str:
        with self._lock:
            return self._content_hash.copy().hexdigest()

    def _raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError("stream checkpoint persistence failed") from self._failure

    def _timer_loop(self) -> None:
        while not self._stop.wait(min(_STREAM_FLUSH_INTERVAL_SECONDS, 1.0)):
            try:
                with self._lock:
                    self._raise_if_failed()
                    now = time.monotonic()
                    if now - self._last_heartbeat >= _STREAM_HEARTBEAT_SECONDS:
                        self.runs.heartbeat(
                            self.tenant,
                            self.run.run_id,
                            self.fencing_token,
                        )
                        self._last_heartbeat = now
                    if self._buffer and now - self._last_flush >= _STREAM_FLUSH_INTERVAL_SECONDS:
                        self._flush_locked()
            except Exception as exc:  # noqa: BLE001 - report a stable persistence failure to the run owner
                with self._lock:
                    self._failure = exc
                self._stop.set()
                return

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        self._raise_if_failed()
        data = bytes(self._buffer)
        intent = self.uploads.reserve(
            self.tenant,
            "STREAM_CHUNK",
            session_id=self.run.session_id,
            message_id=self.message_id,
            run_id=self.run.run_id,
        )
        self.content_store.put(
            self.tenant,
            ContentRef(intent.object_ref),
            data,
            _STREAM_CHUNK_CONTENT_TYPE,
        )
        sequence = self._committed_sequence + 1
        record = MessageChunkRecord(
            message_id=self.message_id,
            chunk_sequence=sequence,
            content_ref=intent.object_ref,
            content_sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data),
        )
        self.messages.append_chunk(
            self.tenant,
            record,
            upload_id=intent.upload_id,
            fencing_token=self.fencing_token,
        )
        self._buffer.clear()
        self._committed_sequence = sequence
        self._last_flush = time.monotonic()


def _manifest_record(
    *,
    tenant: TenantContext,
    run: RunRecord,
    message_id: str,
    status: MessageStatus,
    manifest_ref: str,
    manifest_data: bytes,
) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        tenant=tenant,
        session_id=run.session_id,
        run_id=run.run_id,
        role=MessageRole.ASSISTANT,
        status=status,
        content_ref=manifest_ref,
        content_sha256=hashlib.sha256(manifest_data).hexdigest(),
        byte_length=len(manifest_data),
        content_type=_STREAM_MANIFEST_CONTENT_TYPE,
    )


def _require_member(tenant: TenantContext) -> None:
    if not _MUTATING_ROLES.intersection(tenant.roles):
        raise AuthorizationError("tenant member role is required")


def _require_reader(tenant: TenantContext) -> None:
    if not _READ_ROLES.intersection(tenant.roles):
        raise AuthorizationError("tenant reader role is required")


def _require_worker(tenant: TenantContext) -> None:
    if tenant.auth_source != "worker":
        raise AuthorizationError("worker authentication is required")


def _require_admin(tenant: TenantContext) -> None:
    if not _ADMIN_ROLES.intersection(tenant.roles):
        raise AuthorizationError("owner or admin role is required")


def _require_active_session(session: SessionRecord) -> None:
    if session.status is not SessionStatus.ACTIVE:
        raise InvalidStateTransitionError(
            f"session is not active (current state: {session.status.value})"
        )


class PersistentSessionService:
    """Persist control-plane state without leaking database types to engines."""

    def __init__(
        self,
        sessions: SessionRepository,
        runs: RunRepository,
        *,
        id_factory: Any = generate_id,
        messages: MessageRepository | None = None,
        uploads: ContentUploadRepository | None = None,
        content_store: ContentStore | None = None,
    ) -> None:
        configured = (messages is not None, uploads is not None, content_store is not None)
        if any(configured) and not all(configured):
            raise ValueError("messages, uploads and content_store must be configured together")
        self.sessions = sessions
        self.runs = runs
        self.messages = messages
        self.uploads = uploads
        self.content_store = content_store
        self._id_factory = id_factory

    def delete_session_history(
        self,
        tenant: TenantContext,
        control_session_id: str,
    ) -> DeleteResult:
        """Immediately revoke reads and enqueue session content for deletion."""

        _require_admin(tenant)
        return self.sessions.delete_history(tenant, control_session_id)

    def _upload_message(self, tenant: TenantContext, run: RunRecord, role: MessageRole,
                        text: str, *, message_id: str | None = None) -> tuple[MessageRecord, str]:
        assert self.uploads is not None and self.content_store is not None
        message_id = message_id or self._id_factory()
        intent = self.uploads.reserve(
            tenant, "MESSAGE_CONTENT", session_id=run.session_id,
            message_id=message_id, run_id=run.run_id,
        )
        data = text.encode("utf-8")
        content_type = "text/plain; charset=utf-8"
        self.content_store.put(tenant, ContentRef(intent.object_ref), data, content_type)
        message = MessageRecord(
            message_id=message_id, tenant=tenant, session_id=run.session_id,
            run_id=run.run_id, role=role, status=MessageStatus.COMPLETE,
            content_ref=intent.object_ref, content_sha256=hashlib.sha256(data).hexdigest(),
            byte_length=len(data), content_type=content_type,
        )
        return message, intent.upload_id

    def _build_stream_manifest(
        self,
        tenant: TenantContext,
        run: RunRecord,
        message_id: str,
        *,
        status: MessageStatus,
        expected_bytes: int | None = None,
        expected_sha256: str | None = None,
    ) -> tuple[MessageRecord, str]:
        assert self.messages is not None
        assert self.uploads is not None and self.content_store is not None
        chunks: list[dict[str, object]] = []
        stream_hash = hashlib.sha256()
        stream_length = 0
        sequence = 0
        while True:
            page = self.messages.list_chunks_after(
                tenant,
                message_id=message_id,
                last_chunk_sequence=sequence,
                limit=100,
            )
            for chunk in page.items:
                if chunk.chunk_sequence != sequence + 1:
                    raise ValueError("durable stream chunk sequence contains a gap")
                data = self.content_store.get(tenant, ContentRef(chunk.content_ref))
                digest = hashlib.sha256(data).hexdigest()
                if digest != chunk.content_sha256 or len(data) != chunk.byte_length:
                    raise ValueError("durable stream chunk failed integrity validation")
                stream_hash.update(data)
                stream_length += len(data)
                chunks.append(
                    {
                        "sequence": chunk.chunk_sequence,
                        "content_ref": chunk.content_ref,
                        "sha256": chunk.content_sha256,
                        "byte_length": chunk.byte_length,
                    }
                )
                sequence = chunk.chunk_sequence
            if page.next_cursor is None:
                break
        if not chunks:
            raise ValueError("cannot create a stream manifest without committed chunks")
        digest = stream_hash.hexdigest()
        if expected_bytes is not None and stream_length != expected_bytes:
            raise ValueError("committed stream length does not match visible deltas")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("committed stream content does not match visible deltas")
        manifest_data = json.dumps(
            {
                "version": 1,
                "chunks": chunks,
                "byte_length": stream_length,
                "sha256": digest,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        intent = self.uploads.reserve(
            tenant,
            "MANIFEST",
            session_id=run.session_id,
            message_id=message_id,
            run_id=run.run_id,
        )
        self.content_store.put(
            tenant,
            ContentRef(intent.object_ref),
            manifest_data,
            _STREAM_MANIFEST_CONTENT_TYPE,
        )
        return (
            _manifest_record(
                tenant=tenant,
                run=run,
                message_id=message_id,
                status=status,
                manifest_ref=intent.object_ref,
                manifest_data=manifest_data,
            ),
            intent.upload_id,
        )

    def tail_stream(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        last_chunk_sequence: int,
        limit: int = 100,
    ) -> Page[DurableTextChunk]:
        """Read only committed chunks after a client cursor from MySQL's log."""

        _require_reader(tenant)
        if self.messages is None or self.content_store is None:
            raise UnsupportedCapabilityError("stream persistence is not configured")
        if not isinstance(last_chunk_sequence, int) or last_chunk_sequence < 0:
            raise ValueError("last_chunk_sequence must be a non-negative integer")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        page = self.messages.list_chunks_after(
            tenant,
            message_id=message_id,
            last_chunk_sequence=last_chunk_sequence,
            limit=limit,
        )
        expected = last_chunk_sequence
        items: list[DurableTextChunk] = []
        for chunk in page.items:
            if chunk.chunk_sequence != expected + 1:
                raise ValueError("durable stream tail contains a sequence gap")
            data = self.content_store.get(tenant, ContentRef(chunk.content_ref))
            digest = hashlib.sha256(data).hexdigest()
            if digest != chunk.content_sha256 or len(data) != chunk.byte_length:
                raise ValueError("durable stream chunk failed integrity validation")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("durable stream chunk is not UTF-8 text") from exc
            items.append(
                DurableTextChunk(
                    chunk_sequence=chunk.chunk_sequence,
                    text=text,
                    content_sha256=chunk.content_sha256,
                    byte_length=chunk.byte_length,
                    created_at=chunk.created_at,
                )
            )
            expected = chunk.chunk_sequence
        return Page(tuple(items), page.next_cursor)

    def create_session(
        self,
        tenant: TenantContext,
        engine: HarnessEngine,
        *,
        metadata: dict[str, Any] | None = None,
        harness_home_ref: str | None = None,
        workspace_ref: str | None = None,
    ) -> SessionRecord:
        _require_member(tenant)
        control_id = self._id_factory()
        engine.start()
        native = engine.create_session()
        record = SessionRecord(
            control_session_id=control_id,
            tenant=tenant,
            user_id=tenant.user_id,
            engine_name=native.engine_name,
            engine_session_id=native.engine_session_id,
            metadata=metadata or {},
            harness_home_ref=harness_home_ref,
            workspace_ref=workspace_ref,
        )
        try:
            return self.sessions.create(tenant, record)
        except Exception as exc:
            orphan_recorded = False
            try:
                self.sessions.update_status(
                    tenant,
                    control_id,
                    SessionStatus.INTERRUPTED,
                )
            except ResourceNotFoundError:
                try:
                    self.sessions.create(
                        tenant,
                        replace(record, status=SessionStatus.INTERRUPTED)
                    )
                except Exception as orphan_error:  # noqa: BLE001 - best-effort saga repair
                    _logger.warning(
                        "failed to persist interrupted orphan session (%s)",
                        type(orphan_error).__name__,
                    )
                else:
                    orphan_recorded = True
            except Exception as update_error:  # noqa: BLE001 - reconcile possible commit response loss
                _logger.warning(
                    "failed to mark possibly-created session interrupted (%s)",
                    type(update_error).__name__,
                )
            else:
                orphan_recorded = True
            raise SessionCreationError(
                control_id,
                native.engine_session_id,
                orphan_recorded=orphan_recorded,
            ) from exc

    def resume_session(
        self,
        tenant: TenantContext,
        control_session_id: str,
        engine: HarnessEngine,
    ) -> SessionRecord:
        _require_member(tenant)
        record = self.sessions.get(tenant, control_session_id)
        if record.status is SessionStatus.CLOSED:
            raise InvalidStateTransitionError("closed session cannot be resumed")
        if record.status is not SessionStatus.ACTIVE:
            _require_admin(tenant)
        if record.engine_name != getattr(engine, "name", record.engine_name):
            raise SessionMismatchError("session belongs to a different engine")
        engine.start()
        native = engine.resume_session(
            SessionRef(
                session_id=record.engine_session_id,
                engine_name=record.engine_name,
                metadata={"control_session_id": record.control_session_id},
            )
        )
        if (
            native.engine_name != record.engine_name
            or native.engine_session_id != record.engine_session_id
        ):
            raise SessionMismatchError(
                "engine returned a different native session while resuming"
            )
        return self.sessions.update_status(
            tenant,
            control_session_id,
            SessionStatus.ACTIVE,
        )

    def list_sessions(
        self,
        tenant: TenantContext,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ):
        _require_reader(tenant)
        return self.sessions.list(tenant, limit=limit, cursor=cursor)

    def submit_run(
        self,
        tenant: TenantContext,
        control_session_id: str,
        request: EngineRequest,
        idempotency_key: str,
    ) -> RunRecord:
        _require_member(tenant)
        session = self.sessions.get(tenant, control_session_id)
        _require_active_session(session)
        if request.session is not None:
            if request.session.engine_name != session.engine_name:
                raise SessionMismatchError("request session belongs to another engine")
            if request.session.engine_session_id != session.engine_session_id:
                raise SessionMismatchError("request session does not match control session")
        digest = canonical_request_digest(
            prompt=request.prompt,
            session_id=session.engine_session_id,
            metadata=request.metadata,
            parent_run_id=request.parent_run_id,
        )
        run = RunRecord(
                run_id=self._id_factory(),
                tenant=tenant,
                session_id=control_session_id,
                idempotency_key=idempotency_key,
                request_digest=digest,
        )
        if self.messages is None:
            return self.runs.create_or_get(tenant, run)
        message, upload_id = self._upload_message(tenant, run, MessageRole.USER, request.prompt)
        return self.messages.submit_run(tenant, run, message, upload_id=upload_id)

    def execute_run(
        self,
        tenant: TenantContext,
        run_id: str,
        request: EngineRequest,
        engine: HarnessEngine,
        worker_id: str,
    ) -> EngineResult:
        _require_worker(tenant)
        run = self.runs.get(tenant, run_id)
        session = self.sessions.get(tenant, run.session_id)
        _require_active_session(session)
        expected_digest = canonical_request_digest(
            prompt=request.prompt,
            session_id=session.engine_session_id,
            metadata=request.metadata,
            parent_run_id=request.parent_run_id,
        )
        if expected_digest != run.request_digest:
            raise IdempotencyConflictError(
                run.idempotency_key,
                expected_digest=run.request_digest,
                received_digest=expected_digest,
            )

        lease = self.runs.claim(tenant, run_id, worker_id)
        native_ref = SessionRef(
            session_id=session.engine_session_id,
            engine_name=session.engine_name,
            metadata={"control_session_id": session.control_session_id},
        )
        assistant_message_id: str | None = None
        checkpoint_writer: _StreamCheckpointWriter | None = None
        try:
            engine.start()
            self.runs.transition(tenant, run_id, lease.fencing_token, RunState.RUNNING)
            if self.messages is not None:
                assistant_message_id = self._id_factory()
                self.messages.create(
                    tenant,
                    MessageRecord(
                        message_id=assistant_message_id,
                        tenant=tenant,
                        session_id=run.session_id,
                        run_id=run_id,
                        role=MessageRole.ASSISTANT,
                        status=MessageStatus.PENDING,
                    ),
                    fencing_token=lease.fencing_token,
                )
                if engine.capabilities.streaming:
                    assert self.uploads is not None and self.content_store is not None
                    checkpoint_writer = _StreamCheckpointWriter(
                        tenant=tenant,
                        run=run,
                        message_id=assistant_message_id,
                        session_id=native_ref.session_id,
                        fencing_token=lease.fencing_token,
                        messages=self.messages,
                        uploads=self.uploads,
                        content_store=self.content_store,
                        runs=self.runs,
                    )
                    checkpoint_writer.start()

            def handle_event(event: object) -> None:
                if checkpoint_writer is not None and isinstance(event, VisibleTextDelta):
                    checkpoint_writer.accept(event)

            engine_request = EngineRequest(
                prompt=request.prompt,
                session=native_ref,
                metadata=dict(request.metadata),
                run_id=run_id,
                parent_run_id=request.parent_run_id,
            )
            if checkpoint_writer is not None:
                result = engine.run(engine_request, on_event=handle_event)
            else:
                result = engine.run(engine_request)
            if checkpoint_writer is not None:
                checkpoint_writer.finish()
            state = {
                RunStatus.SUCCEEDED: RunState.SUCCEEDED,
                RunStatus.FAILED: RunState.FAILED,
                RunStatus.CANCELLED: RunState.CANCELLED,
                RunStatus.UNKNOWN: RunState.UNKNOWN,
            }[result.status]
            if state is RunState.CANCELLED:
                current = self.runs.get(tenant, run_id)
                if current.state is not RunState.CANCELLING:
                    self.runs.transition(
                        tenant,
                        run_id,
                        lease.fencing_token,
                        RunState.CANCELLING,
                    )
            if self.messages is not None and state is RunState.SUCCEEDED:
                if checkpoint_writer is not None and checkpoint_writer.delta_count:
                    result_bytes = result.text.encode("utf-8")
                    if (
                        len(result_bytes) != checkpoint_writer.total_bytes
                        or hashlib.sha256(result_bytes).hexdigest()
                        != checkpoint_writer.content_sha256
                    ):
                        raise ValueError("engine final text does not match visible text deltas")
                    assert assistant_message_id is not None
                    message, upload_id = self._build_stream_manifest(
                        tenant,
                        run,
                        assistant_message_id,
                        status=MessageStatus.COMPLETE,
                        expected_bytes=checkpoint_writer.total_bytes,
                        expected_sha256=checkpoint_writer.content_sha256,
                    )
                else:
                    message, upload_id = self._upload_message(
                        tenant,
                        run,
                        MessageRole.ASSISTANT,
                        result.text,
                        message_id=assistant_message_id,
                    )
                completed = self.messages.complete(
                    tenant,
                    message,
                    upload_id=upload_id,
                    fencing_token=lease.fencing_token,
                )
                result = replace(result, result_ref=completed.content_ref)
            elif self.messages is not None and checkpoint_writer is not None:
                assert assistant_message_id is not None
                if self.messages.chunk_high_water(tenant, assistant_message_id) > 0:
                    message, upload_id = self._build_stream_manifest(
                        tenant,
                        run,
                        assistant_message_id,
                        status=MessageStatus.PARTIAL,
                    )
                    completed = self.messages.finalize_worker_partial(
                        tenant,
                        message,
                        upload_id=upload_id,
                        fencing_token=lease.fencing_token,
                        run_state=state,
                    )
                    result = replace(result, result_ref=completed.content_ref)
                else:
                    self.messages.fail_pending_message(
                        tenant,
                        assistant_message_id,
                        fencing_token=lease.fencing_token,
                        terminal_state=state,
                    )
            elif self.messages is not None and assistant_message_id is not None:
                self.messages.fail_pending_message(
                    tenant,
                    assistant_message_id,
                    fencing_token=lease.fencing_token,
                    terminal_state=state,
                )
            else:
                self.runs.transition(
                    tenant, run_id, lease.fencing_token, state,
                    result_ref=result.result_ref if self.messages is None else None,
                )
            return replace(result, run_id=run_id, session=native_ref)
        except Exception:
            if checkpoint_writer is not None:
                try:
                    checkpoint_writer.finish()
                    assert assistant_message_id is not None
                    if self.messages.chunk_high_water(tenant, assistant_message_id) > 0:
                        partial, upload_id = self._build_stream_manifest(
                            tenant,
                            run,
                            assistant_message_id,
                            status=MessageStatus.PARTIAL,
                        )
                        self.messages.finalize_worker_partial(
                            tenant,
                            partial,
                            upload_id=upload_id,
                            fencing_token=lease.fencing_token,
                            run_state=RunState.UNKNOWN,
                        )
                    else:
                        self.messages.fail_pending_message(
                            tenant,
                            assistant_message_id,
                            fencing_token=lease.fencing_token,
                            terminal_state=RunState.UNKNOWN,
                        )
                except Exception as persistence_error:  # noqa: BLE001 - preserve the original engine error
                    _logger.warning(
                        "failed to persist partial stream state (%s)",
                        type(persistence_error).__name__,
                        extra={"run_id": run_id, "fencing_token": lease.fencing_token},
                    )
                    try:
                        self.runs.mark_unknown(
                            tenant,
                            run_id,
                            lease.fencing_token,
                            error_code="ENGINE_RESULT_UNKNOWN",
                        )
                    except Exception as run_error:  # noqa: BLE001 - preserve the original engine error
                        _logger.warning(
                            "failed to persist unknown run state (%s)",
                            type(run_error).__name__,
                            extra={"run_id": run_id, "fencing_token": lease.fencing_token},
                        )
            elif assistant_message_id is not None and self.messages is not None:
                try:
                    self.messages.fail_pending_message(
                        tenant,
                        assistant_message_id,
                        fencing_token=lease.fencing_token,
                        terminal_state=RunState.UNKNOWN,
                        error_code="ENGINE_RESULT_UNKNOWN",
                    )
                except Exception as persistence_error:  # noqa: BLE001 - preserve the original engine error
                    _logger.warning(
                        "failed to persist unknown assistant message state (%s)",
                        type(persistence_error).__name__,
                        extra={"run_id": run_id, "fencing_token": lease.fencing_token},
                    )
                    try:
                        self.runs.mark_unknown(
                            tenant,
                            run_id,
                            lease.fencing_token,
                            error_code="ENGINE_RESULT_UNKNOWN",
                        )
                    except Exception as run_error:  # noqa: BLE001 - preserve the original engine error
                        _logger.warning(
                            "failed to persist unknown run state (%s)",
                            type(run_error).__name__,
                            extra={"run_id": run_id, "fencing_token": lease.fencing_token},
                        )
            else:
                try:
                    self.runs.mark_unknown(
                        tenant,
                        run_id,
                        lease.fencing_token,
                        error_code="ENGINE_RESULT_UNKNOWN",
                    )
                except Exception as persistence_error:  # noqa: BLE001 - record safe metadata, preserve original error
                    _logger.warning(
                        "failed to persist unknown run state after engine error (%s)",
                        type(persistence_error).__name__,
                        extra={"run_id": run_id, "fencing_token": lease.fencing_token},
                    )
            raise

    def mark_interrupted(
        self,
        tenant: TenantContext,
        control_session_id: str,
    ) -> SessionRecord:
        if tenant.auth_source not in {"worker", "system"}:
            _require_admin(tenant)
        return self.sessions.update_status(
            tenant,
            control_session_id,
            SessionStatus.INTERRUPTED,
        )

    def update_session_status(
        self,
        tenant: TenantContext,
        control_session_id: str,
        status: SessionStatus,
    ) -> SessionRecord:
        _require_admin(tenant)
        return self.sessions.update_status(tenant, control_session_id, status)

    def request_cancel(
        self,
        tenant: TenantContext,
        run_id: str,
        engine: HarnessEngine,
    ) -> CancelResult:
        _require_admin(tenant)
        if engine.state is EngineState.CLOSED:
            raise EngineClosedError("cannot cancel a run with a closed engine")
        if engine.state is not EngineState.STARTED:
            raise EngineNotStartedError("engine must be started before cancelling a run")
        if not engine.capabilities.cancellation:
            raise UnsupportedCapabilityError(
                f"engine {getattr(engine, 'name', '<unknown>')!r} does not support cancellation"
            )
        run = self.runs.get(tenant, run_id)
        session = self.sessions.get(tenant, run.session_id)
        if session.engine_name != getattr(engine, "name", session.engine_name):
            raise SessionMismatchError("run belongs to a different engine")
        self.runs.request_cancel(tenant, run_id)
        return engine.cancel(session.engine_session_id)

    def _recover_expired_stream(
        self,
        tenant: TenantContext,
        run: RunRecord,
        message: MessageRecord,
    ) -> None:
        assert self.messages is not None
        if message.status not in {MessageStatus.PENDING, MessageStatus.STREAMING}:
            return
        high_water = self.messages.chunk_high_water(tenant, message.message_id)
        if high_water == 0:
            self.messages.fail_expired_pending(
                tenant,
                message.message_id,
                fencing_token=run.fencing_token,
            )
            return
        manifest, upload_id = self._build_stream_manifest(
            tenant,
            run,
            message.message_id,
            status=MessageStatus.PARTIAL,
        )
        self.messages.recover_partial(
            tenant,
            manifest,
            upload_id=upload_id,
            fencing_token=run.fencing_token,
        )

    def reconcile_expired_runs(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[RunRecord, ...]:
        if tenant.auth_source != "system":
            raise AuthorizationError("system authentication is required")
        expired = self.runs.expire_leases(tenant, limit=limit)
        candidates: dict[str, RunRecord] = {run.run_id: run for run in expired}
        if self.messages is not None and self.content_store is not None:
            try:
                for run, _message in self.messages.list_unfinalized_streams(
                    tenant, limit=limit
                ):
                    candidates.setdefault(run.run_id, run)
            except Exception as persistence_error:  # noqa: BLE001 - retry on the next system sweep
                _logger.warning(
                    "failed to list unfinished stream recovery work (%s)",
                    type(persistence_error).__name__,
                    extra={"limit": limit},
                )
        for run in candidates.values():
            if self.messages is not None and self.content_store is not None:
                try:
                    message = self.messages.get_for_run(
                        tenant, run.run_id, MessageRole.ASSISTANT
                    )
                    if message is not None:
                        self._recover_expired_stream(tenant, run, message)
                except Exception as persistence_error:  # noqa: BLE001 - retry on the next system sweep
                    _logger.warning(
                        "failed to recover expired stream (%s)",
                        type(persistence_error).__name__,
                        extra={"run_id": run.run_id, "fencing_token": run.fencing_token},
                    )
            try:
                session = self.sessions.get(tenant, run.session_id)
                if session.status is not SessionStatus.CLOSED:
                    self.sessions.update_status(
                        tenant,
                        run.session_id,
                        SessionStatus.INTERRUPTED,
                    )
            except InvalidStateTransitionError:
                _logger.info(
                    "session state was not changed during expired run reconciliation",
                    extra={"run_id": run.run_id, "session_id": run.session_id},
                )
        return expired


__all__ = ["PersistentSessionService"]
