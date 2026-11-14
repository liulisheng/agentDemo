"""Tenant-scoped persistence records and state transition rules."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..core import TenantContext
from .errors import InvalidStateTransitionError


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    INTERRUPTED = "INTERRUPTED"
    CLOSED = "CLOSED"


class RunState(StrEnum):
    QUEUED = "QUEUED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.ACTIVE: frozenset(
        {SessionStatus.ACTIVE, SessionStatus.PAUSED, SessionStatus.INTERRUPTED, SessionStatus.CLOSED}
    ),
    SessionStatus.PAUSED: frozenset(
        {SessionStatus.PAUSED, SessionStatus.ACTIVE, SessionStatus.INTERRUPTED, SessionStatus.CLOSED}
    ),
    SessionStatus.INTERRUPTED: frozenset(
        {SessionStatus.INTERRUPTED, SessionStatus.ACTIVE, SessionStatus.CLOSED}
    ),
    SessionStatus.CLOSED: frozenset({SessionStatus.CLOSED}),
}

RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.QUEUED, RunState.STARTING}),
    RunState.STARTING: frozenset(
        {RunState.STARTING, RunState.RUNNING, RunState.FAILED, RunState.CANCELLING, RunState.UNKNOWN}
    ),
    RunState.RUNNING: frozenset(
        {RunState.RUNNING, RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLING, RunState.UNKNOWN}
    ),
    RunState.CANCELLING: frozenset(
        {
            RunState.CANCELLING,
            RunState.SUCCEEDED,
            RunState.CANCELLED,
            RunState.FAILED,
            RunState.UNKNOWN,
        }
    ),
    RunState.SUCCEEDED: frozenset({RunState.SUCCEEDED}),
    RunState.FAILED: frozenset({RunState.FAILED}),
    RunState.CANCELLED: frozenset({RunState.CANCELLED}),
    RunState.UNKNOWN: frozenset({RunState.UNKNOWN}),
}


def validate_session_transition(current: SessionStatus, target: SessionStatus) -> None:
    if target not in SESSION_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"session state {current.value} cannot transition to {target.value}"
        )


def validate_run_transition(current: RunState, target: RunState) -> None:
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"run state {current.value} cannot transition to {target.value}"
        )


def canonical_request_digest(
    *,
    prompt: str,
    session_id: str | None,
    metadata: Mapping[str, str] | None,
    parent_run_id: str | None,
    version: int = 1,
) -> str:
    """Hash a stable request representation without persisting its contents."""

    if version != 1:
        raise ValueError("unsupported request digest version")
    payload = {
        "metadata": dict(sorted((metadata or {}).items())),
        "parent_run_id": parent_run_id,
        "prompt": prompt,
        "session_id": session_id,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_nonblank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_digest(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("request_digest must be a lowercase SHA-256 hex digest")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Page[PageItem]:
    items: tuple[PageItem, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        if self.next_cursor is not None and not self.next_cursor.strip():
            raise ValueError("next_cursor must not be blank")


@dataclass(frozen=True, slots=True)
class Lease:
    run_id: str
    worker_id: str
    fencing_token: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        _require_nonblank(self.run_id, "run_id")
        _require_nonblank(self.worker_id, "worker_id")
        if self.fencing_token <= 0:
            raise ValueError("fencing_token must be positive for an active lease")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    control_session_id: str
    tenant: TenantContext
    user_id: str
    engine_name: str
    engine_session_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: Mapping[str, Any] = field(default_factory=dict)
    harness_home_ref: str | None = None
    workspace_ref: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    last_run_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "control_session_id",
            "user_id",
            "engine_name",
            "engine_session_id",
        ):
            _require_nonblank(getattr(self, name), name)
        if not isinstance(self.status, SessionStatus):
            object.__setattr__(self, "status", SessionStatus(self.status))


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    tenant: TenantContext
    session_id: str
    idempotency_key: str
    request_digest: str
    request_digest_version: int = 1
    state: RunState = RunState.QUEUED
    worker_id: str | None = None
    fencing_token: int = 0
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    error_code: str | None = None
    result_ref: str | None = None
    # Transient submission result; the message index is the source of truth.
    message_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("run_id", "session_id", "idempotency_key"):
            _require_nonblank(getattr(self, name), name)
        _require_digest(self.request_digest)
        if self.request_digest_version != 1:
            raise ValueError("unsupported request_digest_version")
        if self.fencing_token < 0:
            raise ValueError("fencing_token must not be negative")
        if not isinstance(self.state, RunState):
            object.__setattr__(self, "state", RunState(self.state))


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MessageStatus(StrEnum):
    PENDING = "PENDING"
    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """Content metadata only. A zero sequence requests repository allocation."""

    message_id: str
    tenant: TenantContext
    session_id: str
    role: MessageRole
    status: MessageStatus
    sequence_no: int = 0
    run_id: str | None = None
    content_ref: str | None = None
    content_sha256: str | None = None
    byte_length: int | None = None
    content_type: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("message_id", "session_id"):
            _require_nonblank(getattr(self, name), name)
        object.__setattr__(self, "role", MessageRole(self.role))
        object.__setattr__(self, "status", MessageStatus(self.status))
        if self.sequence_no < 0:
            raise ValueError("sequence_no must not be negative")
        content = (self.content_ref, self.content_sha256, self.byte_length, self.content_type)
        if any(v is not None for v in content):
            if any(v is None for v in content):
                raise ValueError("content metadata must be supplied together")
            _require_nonblank(self.content_ref, "content_ref")
            _require_digest(self.content_sha256)
            _require_nonblank(self.content_type, "content_type")
            if self.byte_length < 0:
                raise ValueError("byte_length must not be negative")
        if self.status in {MessageStatus.COMPLETE, MessageStatus.PARTIAL} and self.content_ref is None:
            raise ValueError("complete or partial message requires content")
        if self.role is MessageRole.USER and self.status is not MessageStatus.COMPLETE:
            raise ValueError("user message must be complete")


@dataclass(frozen=True, slots=True)
class MessageChunkRecord:
    """Metadata for one immutable, tenant-scoped visible-text checkpoint."""

    message_id: str
    chunk_sequence: int
    content_ref: str
    content_sha256: str
    byte_length: int
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_nonblank(self.message_id, "message_id")
        _require_nonblank(self.content_ref, "content_ref")
        _require_digest(self.content_sha256)
        if self.chunk_sequence <= 0:
            raise ValueError("chunk_sequence must be positive")
        if self.byte_length <= 0:
            raise ValueError("byte_length must be positive")


@dataclass(frozen=True, slots=True)
class UploadIntent:
    upload_id: str
    tenant: TenantContext
    object_ref: str
    object_type: str
    status: str
    lease_expires_at: datetime
    session_id: str | None = None
    message_id: str | None = None
    run_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in ("upload_id", "object_ref"):
            _require_nonblank(getattr(self, name), name)
        if self.object_type not in {"MESSAGE_CONTENT", "STREAM_CHUNK", "MANIFEST", "HARNESS_HOME", "WORKSPACE"}:
            raise ValueError("invalid upload object_type")
        if self.status not in {"UPLOADING", "PUBLISHED", "EXPIRED"}:
            raise ValueError("invalid upload status")


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Result of an idempotent session-history deletion request."""

    session_id: str
    deleted: bool
    queued_objects: int

    def __post_init__(self) -> None:
        _require_nonblank(self.session_id, "session_id")
        if self.queued_objects < 0:
            raise ValueError("queued_objects must not be negative")


@dataclass(frozen=True, slots=True)
class ContentGcItem:
    """A tenant-scoped content object queued for asynchronous deletion."""

    cleanup_id: str
    object_ref: str
    object_type: str
    attempt_count: int = 0

    def __post_init__(self) -> None:
        _require_nonblank(self.cleanup_id, "cleanup_id")
        _require_nonblank(self.object_ref, "object_ref")
        _require_nonblank(self.object_type, "object_type")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
