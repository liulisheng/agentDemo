"""Persistence interfaces consumed by the session service."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from ..core import TenantContext
from .models import (
    ContentGcItem,
    DeleteResult,
    Lease,
    MessageChunkRecord,
    MessageRecord,
    MessageRole,
    Page,
    RunRecord,
    RunState,
    SessionRecord,
    SessionStatus,
    UploadIntent,
)


class SessionRepository(Protocol):
    def create(self, tenant: TenantContext, record: SessionRecord) -> SessionRecord: ...

    def get(self, tenant: TenantContext, control_session_id: str) -> SessionRecord: ...

    def list(
        self,
        tenant: TenantContext,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Page[SessionRecord]: ...

    def update_status(
        self,
        tenant: TenantContext,
        control_session_id: str,
        status: SessionStatus,
    ) -> SessionRecord: ...

    def delete_history(
        self,
        tenant: TenantContext,
        control_session_id: str,
    ) -> DeleteResult: ...


class RunRepository(Protocol):
    def create_or_get(self, tenant: TenantContext, record: RunRecord) -> RunRecord: ...

    def get(self, tenant: TenantContext, run_id: str) -> RunRecord: ...

    def list(
        self,
        tenant: TenantContext,
        *,
        session_id: str | None = None,
        limit: int,
        cursor: str | None = None,
    ) -> Page[RunRecord]: ...

    def claim(
        self,
        tenant: TenantContext,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> Lease: ...

    def claim_next(
        self,
        tenant: TenantContext,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> Lease | None: ...

    def request_cancel(
        self,
        tenant: TenantContext,
        run_id: str,
    ) -> RunRecord: ...

    def expire_leases(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[RunRecord, ...]: ...

    def heartbeat(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        *,
        lease_seconds: int = 30,
    ) -> Lease: ...

    def transition(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        state: RunState,
        *,
        error_code: str | None = None,
        result_ref: str | None = None,
    ) -> RunRecord: ...

    def mark_unknown(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        *,
        error_code: str = "UNKNOWN_RESULT",
    ) -> RunRecord: ...


class AuditRepository(Protocol):
    def append(
        self,
        tenant: TenantContext,
        *,
        event_type: str,
        actor_type: str,
        actor_user_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        fencing_token: int | None = None,
        policy_version: str = "v1",
        details: Mapping[str, object] | None = None,
        occurred_at: datetime | None = None,
    ) -> str: ...


class DatabaseHealth(Protocol):
    def health(self) -> str: ...


class ContentGcRepository(Protocol):
    def enqueue_orphans(
        self,
        tenant: TenantContext,
        store: object,
        *,
        older_than: datetime,
        limit: int = 100,
    ) -> int: ...

    def claim(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[ContentGcItem, ...]: ...

    def complete(
        self,
        tenant: TenantContext,
        cleanup_id: str,
        *,
        success: bool,
        error_code: str | None = None,
    ) -> None: ...


class ContentUploadRepository(Protocol):
    def reserve(self, tenant: TenantContext, object_type: str, *, session_id: str | None = None,
                message_id: str | None = None, run_id: str | None = None,
                lease_seconds: int = 300) -> UploadIntent: ...

    def renew(self, tenant: TenantContext, upload_id: str, *, lease_seconds: int = 300) -> UploadIntent: ...

    def claim_expired(self, tenant: TenantContext, *, limit: int = 100) -> tuple[UploadIntent, ...]: ...


class MessageRepository(Protocol):
    def create(self, tenant: TenantContext, record: MessageRecord, *, upload_id: str | None = None,
               fencing_token: int | None = None) -> MessageRecord: ...

    def get(self, tenant: TenantContext, message_id: str) -> MessageRecord: ...

    def list(self, tenant: TenantContext, *, session_id: str, limit: int = 50,
             cursor: str | None = None) -> Page[MessageRecord]: ...

    def submit_run(self, tenant: TenantContext, run: RunRecord, message: MessageRecord,
                   *, upload_id: str) -> RunRecord:
        """Publish the queued run, complete user message and upload atomically."""
        ...

    def complete(self, tenant: TenantContext, record: MessageRecord, *, upload_id: str,
                 fencing_token: int) -> MessageRecord:
        """Publish assistant completion, run success and matching result_ref atomically."""
        ...

    def append_chunk(
        self,
        tenant: TenantContext,
        record: MessageChunkRecord,
        *,
        upload_id: str,
        fencing_token: int,
    ) -> MessageChunkRecord:
        """Publish one next-contiguous immutable stream checkpoint atomically."""
        ...

    def list_chunks_after(
        self,
        tenant: TenantContext,
        *,
        message_id: str,
        last_chunk_sequence: int,
        limit: int = 100,
    ) -> Page[MessageChunkRecord]: ...

    def chunk_high_water(self, tenant: TenantContext, message_id: str) -> int: ...

    def get_for_run(
        self,
        tenant: TenantContext,
        run_id: str,
        role: MessageRole = MessageRole.ASSISTANT,
    ) -> MessageRecord | None: ...

    def list_unfinalized_streams(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[tuple[RunRecord, MessageRecord], ...]: ...

    def finalize_worker_partial(
        self,
        tenant: TenantContext,
        record: MessageRecord,
        *,
        upload_id: str,
        fencing_token: int,
        run_state: RunState = RunState.UNKNOWN,
        error_code: str | None = None,
    ) -> MessageRecord: ...

    def recover_partial(
        self,
        tenant: TenantContext,
        record: MessageRecord,
        *,
        upload_id: str,
        fencing_token: int,
    ) -> MessageRecord: ...

    def fail_pending_message(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        fencing_token: int,
        terminal_state: RunState = RunState.FAILED,
        error_code: str = "ENGINE_FAILED",
    ) -> MessageRecord: ...

    def fail_expired_pending(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        fencing_token: int,
    ) -> MessageRecord: ...

    def delete(self, tenant: TenantContext, message_id: str) -> None: ...
