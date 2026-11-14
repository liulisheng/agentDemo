from datetime import UTC, datetime, timedelta

import pytest

from harness_engine.core import TenantContext
from harness_engine.persistence.errors import (
    IdempotencyConflictError,
    InvalidStateTransitionError,
)
from harness_engine.persistence.ids import generate_id
from harness_engine.persistence.models import (
    MessageChunkRecord,
    Page,
    RunRecord,
    RunState,
    SessionRecord,
    SessionStatus,
    canonical_request_digest,
    validate_run_transition,
    validate_session_transition,
)

TENANT = TenantContext(
    platform_id="platform-1",
    tenant_id="tenant-1",
    user_id="user-1",
    roles=("owner",),
    auth_source="oidc",
)


def test_message_chunk_record_validates_sequence_digest_and_size():
    chunk = MessageChunkRecord(
        message_id="m1",
        chunk_sequence=1,
        content_ref="opaque-chunk-ref",
        content_sha256="a" * 64,
        byte_length=10,
    )

    assert chunk.chunk_sequence == 1
    assert chunk.byte_length == 10
    with pytest.raises(ValueError, match="chunk_sequence"):
        MessageChunkRecord(
            message_id="m1",
            chunk_sequence=0,
            content_ref="opaque-chunk-ref",
            content_sha256="a" * 64,
            byte_length=10,
        )
    with pytest.raises(ValueError, match="hex digest"):
        MessageChunkRecord(
            message_id="m1",
            chunk_sequence=1,
            content_ref="opaque-chunk-ref",
            content_sha256="bad",
            byte_length=10,
        )


def test_session_record_keeps_control_and_engine_ids_separate():
    record = SessionRecord(
        control_session_id="01JSESSION000000000000000001",
        tenant=TENANT,
        user_id="user-1",
        engine_name="fake",
        engine_session_id="fake-native-1",
    )

    assert record.control_session_id != record.engine_session_id
    assert record.status is SessionStatus.ACTIVE


def test_run_record_validates_digest_and_fencing_token():
    record = RunRecord(
        run_id="01JRUN00000000000000000001",
        tenant=TENANT,
        session_id="01JSESSION000000000000000001",
        idempotency_key="request-1",
        request_digest="a" * 64,
    )

    assert record.state is RunState.QUEUED
    assert record.fencing_token == 0

    with pytest.raises(ValueError, match="request_digest"):
        RunRecord(
            run_id="run-2",
            tenant=TENANT,
            session_id="session-1",
            idempotency_key="request-2",
            request_digest="bad",
        )


def test_canonical_request_digest_is_stable_and_sensitive_to_request_changes():
    first = canonical_request_digest(
        prompt="hello",
        session_id="native-1",
        metadata={"b": "2", "a": "1"},
        parent_run_id="parent-1",
    )
    reordered = canonical_request_digest(
        prompt="hello",
        session_id="native-1",
        metadata={"a": "1", "b": "2"},
        parent_run_id="parent-1",
    )
    changed = canonical_request_digest(
        prompt="different",
        session_id="native-1",
        metadata={"a": "1", "b": "2"},
        parent_run_id="parent-1",
    )

    assert first == reordered
    assert len(first) == 64
    assert first != changed


def test_session_transition_matrix_allows_resume_and_closes_are_terminal():
    validate_session_transition(SessionStatus.ACTIVE, SessionStatus.PAUSED)
    validate_session_transition(SessionStatus.PAUSED, SessionStatus.ACTIVE)
    validate_session_transition(SessionStatus.INTERRUPTED, SessionStatus.ACTIVE)
    validate_session_transition(SessionStatus.ACTIVE, SessionStatus.CLOSED)

    with pytest.raises(InvalidStateTransitionError):
        validate_session_transition(SessionStatus.CLOSED, SessionStatus.ACTIVE)

    # Repeating the same status is an idempotent no-op.
    validate_session_transition(SessionStatus.PAUSED, SessionStatus.PAUSED)


def test_run_transition_matrix_rejects_stale_terminal_updates():
    validate_run_transition(RunState.QUEUED, RunState.STARTING)
    validate_run_transition(RunState.RUNNING, RunState.CANCELLING)
    validate_run_transition(RunState.CANCELLING, RunState.CANCELLED)
    # A late cancel may race with an engine result already confirmed as successful.
    validate_run_transition(RunState.CANCELLING, RunState.SUCCEEDED)
    validate_run_transition(RunState.RUNNING, RunState.UNKNOWN)

    with pytest.raises(InvalidStateTransitionError):
        validate_run_transition(RunState.SUCCEEDED, RunState.RUNNING)


def test_page_rejects_blank_cursor_and_expired_leases_are_representable():
    assert Page(items=("one",), next_cursor="cursor").items == ("one",)
    with pytest.raises(ValueError, match="cursor"):
        Page(items=(), next_cursor=" ")

    record = RunRecord(
        run_id="run-3",
        tenant=TENANT,
        session_id="session-1",
        idempotency_key="request-3",
        request_digest="b" * 64,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert record.lease_expires_at is not None


def test_error_types_are_public_and_carry_conflict_context():
    error = IdempotencyConflictError(
        "request-1",
        expected_digest="a" * 64,
        received_digest="b" * 64,
    )

    assert error.idempotency_key == "request-1"
    assert error.expected_digest != error.received_digest


def test_generate_id_is_a_nonblank_26_character_opaque_id():
    value = generate_id()

    assert len(value) == 26
    assert value.isalnum()
