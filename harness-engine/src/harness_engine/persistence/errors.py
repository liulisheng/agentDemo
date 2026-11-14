"""Stable errors for the persistence and control-plane boundaries."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base class for persistence failures."""


class ResourceNotFoundError(PersistenceError):
    """Raised when a resource is not visible in the supplied tenant."""


class TenantAccessError(PersistenceError):
    """Raised when an operation violates an explicit tenant boundary."""


class InvalidStateTransitionError(PersistenceError):
    """Raised when a state machine transition is not allowed."""


class LeaseLostError(PersistenceError):
    """Raised when a worker's fencing token no longer owns a run."""


class IdempotencyConflictError(PersistenceError):
    """Raised when an idempotency key is reused for a different request."""

    def __init__(
        self,
        idempotency_key: str,
        *,
        expected_digest: str,
        received_digest: str,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.expected_digest = expected_digest
        self.received_digest = received_digest
        super().__init__(
            f"idempotency key {idempotency_key!r} conflicts with the original request"
        )


class DatabaseUnavailableError(PersistenceError):
    """Raised when the configured MySQL dependency or server is unavailable."""


class MigrationVersionError(PersistenceError):
    """Raised when the database schema version is not supported."""


class AuthorizationError(PersistenceError):
    """Raised when a user role cannot perform a control-plane mutation."""


class SessionCreationError(PersistenceError):
    """Raised when an engine session exists but its control record was not saved."""

    def __init__(
        self,
        control_session_id: str,
        engine_session_id: str,
        *,
        orphan_recorded: bool,
    ) -> None:
        self.control_session_id = control_session_id
        self.engine_session_id = engine_session_id
        self.orphan_recorded = orphan_recorded
        super().__init__(
            "engine session was created but its control-plane record could not be saved; "
            f"orphan_recorded={orphan_recorded}"
        )
