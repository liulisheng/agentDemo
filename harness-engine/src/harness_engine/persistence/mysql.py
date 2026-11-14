"""Parameterised MySQL repositories for the control plane.

The module keeps the MySQL connector optional.  Importing the package and
running offline tests does not require a database driver; constructing a real
database from configuration does.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..core import TenantContext
from .content_store import ContentRef
from .errors import (
    AuthorizationError,
    DatabaseUnavailableError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    LeaseLostError,
    ResourceNotFoundError,
)
from .ids import generate_id
from .models import (
    ContentGcItem,
    DeleteResult,
    Lease,
    MessageChunkRecord,
    MessageRecord,
    MessageRole,
    MessageStatus,
    Page,
    RunRecord,
    RunState,
    SessionRecord,
    SessionStatus,
    UploadIntent,
    validate_run_transition,
    validate_session_transition,
)

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class MySqlConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    database: str = "harness"
    user: str = "harness"
    password: str = ""
    pool_size: int = 5
    connect_timeout: int = 5

    @classmethod
    def from_env(cls) -> MySqlConfig:
        return cls(
            host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            database=os.environ.get("MYSQL_DATABASE", "harness"),
            user=os.environ.get("MYSQL_USER", "harness"),
            password=os.environ.get("MYSQL_PASSWORD", ""),
            pool_size=int(os.environ.get("MYSQL_POOL_SIZE", "5")),
            connect_timeout=int(
                os.environ.get("MYSQL_CONNECT_TIMEOUT", "5")
            ),
        )

    def __post_init__(self) -> None:
        for name in ("host", "database", "user"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.port <= 0 or self.pool_size <= 0 or self.connect_timeout <= 0:
            raise ValueError("MySQL port, pool_size and connect_timeout must be positive")


class MySqlDatabase:
    """Small transaction wrapper around a pooled DB-API connection factory."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_config(cls, config: MySqlConfig) -> MySqlDatabase:
        try:
            from mysql.connector.pooling import MySQLConnectionPool
        except ImportError as exc:
            raise DatabaseUnavailableError(
                "mysql-connector-python is not installed; install harness-engine[mysql]"
            ) from exc

        pool = MySQLConnectionPool(
            pool_name=f"harness_engine_{uuid4().hex}",
            pool_size=config.pool_size,
            host=config.host,
            port=config.port,
            database=config.database,
            user=config.user,
            password=config.password,
            connection_timeout=config.connect_timeout,
        )
        return cls(pool.get_connection)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        connection = self._connection_factory()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def health(self) -> str:
        with self.transaction() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        if not row or int(row.get("version", 0)) < 2:
            raise DatabaseUnavailableError("MySQL schema version 2 (V002) is required")
        return f"schema_version={int(row['version'])}"


def _cursor(connection: Any) -> Any:
    return connection.cursor(dictionary=True)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json_load(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise TypeError("JSON column must contain an object")
    return dict(decoded)


def _encode_cursor(updated_at: datetime, record_id: str) -> str:
    payload = {"updated_at": updated_at.isoformat(), "id": record_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(payload["updated_at"]), str(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc


def _session_from_row(tenant: TenantContext, row: Mapping[str, Any]) -> SessionRecord:
    return SessionRecord(
        control_session_id=str(row["session_id"]),
        tenant=tenant,
        user_id=str(row["user_id"]),
        engine_name=str(row["engine_name"]),
        engine_session_id=str(row["engine_session_id"]),
        status=SessionStatus(row["status"]),
        metadata=_json_load(row.get("metadata_json")),
        harness_home_ref=row.get("harness_home_ref"),
        workspace_ref=row.get("workspace_ref"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_run_at=row.get("last_run_at"),
    )


def _run_from_row(tenant: TenantContext, row: Mapping[str, Any]) -> RunRecord:
    return RunRecord(
        run_id=str(row["run_id"]),
        tenant=tenant,
        session_id=str(row["session_id"]),
        idempotency_key=str(row["idempotency_key"]),
        request_digest=str(row["request_digest"]),
        request_digest_version=int(row.get("request_digest_version", 1)),
        state=RunState(row["state"]),
        worker_id=row.get("worker_id"),
        fencing_token=int(row.get("fencing_token", 0)),
        lease_expires_at=row.get("lease_expires_at"),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error_code=row.get("error_code"),
        result_ref=row.get("result_ref"),
    )


class MySqlSessionRepository:
    def __init__(
        self,
        database: MySqlDatabase,
        audit_repository: MySqlAuditRepository | None = None,
    ) -> None:
        self.database = database
        self.audit_repository = audit_repository or MySqlAuditRepository(database)

    def create(self, tenant: TenantContext, record: SessionRecord) -> SessionRecord:
        if record.tenant.scope != tenant.scope:
            raise ValueError("session record tenant does not match explicit tenant context")
        if record.user_id != tenant.user_id:
            raise ValueError("session record user does not match explicit tenant context")
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    INSERT INTO sessions (
                        platform_id, tenant_id, session_id, user_id,
                        engine_name, engine_session_id, status, metadata_json,
                        harness_home_ref, workspace_ref, created_at, updated_at,
                        last_run_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant.platform_id,
                        tenant.tenant_id,
                        record.control_session_id,
                        record.user_id,
                        record.engine_name,
                        record.engine_session_id,
                        record.status.value,
                        json.dumps(record.metadata, ensure_ascii=False),
                        record.harness_home_ref,
                        record.workspace_ref,
                        record.created_at,
                        record.updated_at,
                        record.last_run_at,
                    ),
                )
            finally:
                cursor.close()
            self.audit_repository.append_in_connection(
                connection,
                tenant,
                event_id=generate_id(),
                event_type="session.created",
                actor_type=(
                    "SYSTEM"
                    if tenant.auth_source == "system"
                    else "WORKER"
                    if tenant.auth_source == "worker"
                    else "USER"
                ),
                actor_user_id=(
                    tenant.user_id
                    if tenant.auth_source not in {"system", "worker"}
                    else None
                ),
                session_id=record.control_session_id,
                details={"engine_name": record.engine_name, "status": record.status.value},
            )
        return replace(record, tenant=tenant)

    def get(self, tenant: TenantContext, control_session_id: str) -> SessionRecord:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    SELECT session_id, user_id, engine_name, engine_session_id,
                           status, metadata_json, harness_home_ref, workspace_ref,
                           created_at, updated_at, last_run_at
                      FROM sessions
                     WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                    """,
                    (tenant.platform_id, tenant.tenant_id, control_session_id),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        if row is None:
            raise ResourceNotFoundError(f"session {control_session_id!r} was not found")
        return _session_from_row(tenant, row)

    def list(
        self,
        tenant: TenantContext,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Page[SessionRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params: list[Any] = [tenant.platform_id, tenant.tenant_id]
        where_cursor = ""
        if cursor is not None:
            updated_at, record_id = _decode_cursor(cursor)
            where_cursor = " AND (updated_at < %s OR (updated_at = %s AND session_id < %s))"
            params.extend((updated_at, updated_at, record_id))
        params.append(limit + 1)
        with self.database.transaction() as connection:
            db_cursor = _cursor(connection)
            try:
                db_cursor.execute(
                    f"""
                    SELECT session_id, user_id, engine_name, engine_session_id,
                           status, metadata_json, harness_home_ref, workspace_ref,
                           created_at, updated_at, last_run_at
                      FROM sessions
                     WHERE platform_id = %s AND tenant_id = %s{where_cursor}
                     ORDER BY updated_at DESC, session_id DESC
                     LIMIT %s
                    """,
                    tuple(params),
                )
                rows = db_cursor.fetchall()
            finally:
                db_cursor.close()
        records = [_session_from_row(tenant, row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = _encode_cursor(last.updated_at, last.control_session_id)
        return Page(items=tuple(records), next_cursor=next_cursor)

    def update_status(
        self,
        tenant: TenantContext,
        control_session_id: str,
        status: SessionStatus,
    ) -> SessionRecord:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    SELECT session_id, user_id, engine_name, engine_session_id,
                           status, metadata_json, harness_home_ref, workspace_ref,
                           created_at, updated_at, last_run_at
                      FROM sessions
                     WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                     FOR UPDATE
                    """,
                    (tenant.platform_id, tenant.tenant_id, control_session_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ResourceNotFoundError(
                        f"session {control_session_id!r} was not found"
                    )
                current = SessionStatus(row["status"])
                validate_session_transition(current, status)
                if current is status:
                    return _session_from_row(tenant, row)
                now = _now()
                cursor.execute(
                    """
                    UPDATE sessions
                       SET status = %s, updated_at = %s
                     WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                    """,
                    (
                        status.value,
                        now,
                        tenant.platform_id,
                        tenant.tenant_id,
                        control_session_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ResourceNotFoundError(
                        f"session {control_session_id!r} was not found"
                    )
                self.audit_repository.append_in_connection(
                    connection,
                    tenant,
                    event_id=generate_id(),
                    event_type="session.state_changed",
                    actor_type={"worker": "WORKER", "system": "SYSTEM"}.get(
                        tenant.auth_source, "USER"
                    ),
                    actor_user_id=(
                        tenant.user_id
                        if tenant.auth_source not in {"worker", "system"}
                        else None
                    ),
                    session_id=control_session_id,
                    details={"from": current.value, "to": status.value},
                )
                return _session_from_row(
                    tenant,
                    {**row, "status": status.value, "updated_at": now},
                )
            finally:
                cursor.close()

    def delete_history(self, tenant: TenantContext, control_session_id: str) -> DeleteResult:
        """Delete a session's indexes atomically and queue all content roots."""

        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT session_id, harness_home_ref, workspace_ref
                       FROM sessions
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, control_session_id),
                )
                session = cursor.fetchone()
                if session is None:
                    return DeleteResult(control_session_id, False, 0)

                refs: list[tuple[str, str]] = []
                for key, object_type in (
                    ("harness_home_ref", "HARNESS_HOME"),
                    ("workspace_ref", "WORKSPACE"),
                ):
                    if session.get(key):
                        refs.append((str(session[key]), object_type))

                cursor.execute(
                    """SELECT run_id, result_ref FROM runs
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, control_session_id),
                )
                run_rows = cursor.fetchall()
                run_ids = [str(row["run_id"]) for row in run_rows]
                for row in run_rows:
                    if row.get("result_ref"):
                        refs.append((str(row["result_ref"]), "MESSAGE_CONTENT"))

                cursor.execute(
                    """SELECT message_id, role, content_ref FROM messages
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, control_session_id),
                )
                messages = cursor.fetchall()
                for row in messages:
                    if row.get("content_ref"):
                        object_type = (
                            "MANIFEST"
                            if row.get("role") == MessageRole.ASSISTANT.value
                            else "MESSAGE_CONTENT"
                        )
                        refs.append((str(row["content_ref"]), object_type))

                cursor.execute(
                    """SELECT mc.content_ref FROM message_chunks mc
                       INNER JOIN messages m
                         ON m.platform_id = mc.platform_id
                        AND m.tenant_id = mc.tenant_id
                        AND m.message_id = mc.message_id
                       WHERE mc.platform_id = %s AND mc.tenant_id = %s
                         AND m.session_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, control_session_id),
                )
                refs.extend((str(row["content_ref"]), "STREAM_CHUNK") for row in cursor.fetchall())

                cursor.execute(
                    """SELECT object_ref, object_type FROM content_upload_intents
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, control_session_id),
                )
                upload_rows = cursor.fetchall()
                refs.extend(
                    (str(row["object_ref"]), str(row["object_type"]))
                    for row in upload_rows
                )

                unique_ref_map: dict[str, str] = {}
                for object_ref, object_type in refs:
                    unique_ref_map.setdefault(object_ref, object_type)
                unique_refs = list(unique_ref_map.items())
                for object_ref, object_type in unique_refs:
                    _queue_content(connection, tenant, object_ref, object_type)

                if run_ids:
                    placeholders = ",".join("%s" for _ in run_ids)
                    cursor.execute(
                        f"""DELETE FROM audit_events
                           WHERE platform_id = %s AND tenant_id = %s
                             AND (session_id = %s OR run_id IN ({placeholders}))""",
                        (*tenant.scope, control_session_id, *run_ids),
                    )
                else:
                    cursor.execute(
                        """DELETE FROM audit_events
                           WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                        (*tenant.scope, control_session_id),
                    )
                cursor.execute(
                    """UPDATE content_upload_intents SET status = 'EXPIRED'
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                    (*tenant.scope, control_session_id),
                )
                cursor.execute(
                    """DELETE FROM messages
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                    (*tenant.scope, control_session_id),
                )
                cursor.execute(
                    """DELETE FROM runs
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                    (*tenant.scope, control_session_id),
                )
                cursor.execute(
                    """DELETE FROM content_upload_intents
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                    (*tenant.scope, control_session_id),
                )
                cursor.execute(
                    """DELETE FROM sessions
                       WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                    (*tenant.scope, control_session_id),
                )
                return DeleteResult(control_session_id, True, len(unique_refs))
            finally:
                cursor.close()


class MySqlRunRepository:
    def __init__(
        self,
        database: MySqlDatabase,
        audit_repository: MySqlAuditRepository | None = None,
    ) -> None:
        self.database = database
        self.audit_repository = audit_repository or MySqlAuditRepository(database)

    def create_or_get(self, tenant: TenantContext, record: RunRecord) -> RunRecord:
        if record.tenant.scope != tenant.scope:
            raise ValueError("run record tenant does not match explicit tenant context")
        with self.database.transaction() as connection:
            return self.create_or_get_in_connection(connection, tenant, record)

    def create_or_get_in_connection(self, connection: Any, tenant: TenantContext, record: RunRecord) -> RunRecord:
        if record.tenant.scope != tenant.scope:
            raise ValueError("run record tenant does not match explicit tenant context")
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """
                INSERT INTO runs (
                    platform_id, tenant_id, run_id, session_id,
                    idempotency_key, request_digest, request_digest_version,
                    state, worker_id, fencing_token, lease_expires_at,
                    started_at, finished_at, created_at, updated_at,
                    error_code, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE run_id = run_id
                """,
                (
                    tenant.platform_id,
                    tenant.tenant_id,
                    record.run_id,
                    record.session_id,
                    record.idempotency_key,
                    record.request_digest,
                    record.request_digest_version,
                    record.state.value,
                    record.worker_id,
                    record.fencing_token,
                    record.lease_expires_at,
                    record.started_at,
                    record.finished_at,
                    record.created_at,
                    record.updated_at,
                    record.error_code,
                    record.result_ref,
                ),
            )
            inserted = cursor.rowcount == 1
            cursor.execute(
                """
                SELECT run_id, session_id, idempotency_key, request_digest,
                       request_digest_version, state, worker_id, fencing_token,
                       lease_expires_at, started_at, finished_at, created_at,
                       updated_at, error_code, result_ref
                  FROM runs
                 WHERE platform_id = %s AND tenant_id = %s AND idempotency_key = %s
                 FOR UPDATE
                """,
                (
                    tenant.platform_id,
                    tenant.tenant_id,
                    record.idempotency_key,
                ),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise ValueError("run id already exists for a different request")
            if existing["request_digest"] != record.request_digest:
                raise IdempotencyConflictError(
                    record.idempotency_key,
                    expected_digest=existing["request_digest"],
                    received_digest=record.request_digest,
                )
            if inserted:
                self.audit_repository.append_in_connection(
                    connection,
                    tenant,
                    event_id=generate_id(),
                    event_type="run.created",
                    actor_type="USER",
                    actor_user_id=tenant.user_id,
                    session_id=record.session_id,
                    run_id=record.run_id,
                    details={"state": record.state.value},
                )
            return _run_from_row(tenant, existing)
        finally:
            cursor.close()

    def get(self, tenant: TenantContext, run_id: str) -> RunRecord:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    SELECT run_id, session_id, idempotency_key, request_digest,
                           request_digest_version, state, worker_id, fencing_token,
                           lease_expires_at, started_at, finished_at, created_at,
                           updated_at, error_code, result_ref
                      FROM runs
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                    """,
                    (tenant.platform_id, tenant.tenant_id, run_id),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        if row is None:
            raise ResourceNotFoundError(f"run {run_id!r} was not found")
        return _run_from_row(tenant, row)

    def list(
        self,
        tenant: TenantContext,
        *,
        session_id: str | None = None,
        limit: int,
        cursor: str | None = None,
    ) -> Page[RunRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        params: list[Any] = [tenant.platform_id, tenant.tenant_id]
        where = ""
        if session_id is not None:
            where += " AND session_id = %s"
            params.append(session_id)
        if cursor is not None:
            updated_at, record_id = _decode_cursor(cursor)
            where += " AND (updated_at < %s OR (updated_at = %s AND run_id < %s))"
            params.extend((updated_at, updated_at, record_id))
        params.append(limit + 1)
        with self.database.transaction() as connection:
            db_cursor = _cursor(connection)
            try:
                db_cursor.execute(
                    f"""
                    SELECT run_id, session_id, idempotency_key, request_digest,
                           request_digest_version, state, worker_id, fencing_token,
                           lease_expires_at, started_at, finished_at, created_at,
                           updated_at, error_code, result_ref
                      FROM runs
                     WHERE platform_id = %s AND tenant_id = %s{where}
                     ORDER BY updated_at DESC, run_id DESC
                     LIMIT %s
                    """,
                    tuple(params),
                )
                rows = db_cursor.fetchall()
            finally:
                db_cursor.close()
        records = [_run_from_row(tenant, row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and records:
            last = records[-1]
            next_cursor = _encode_cursor(last.updated_at, last.run_id)
        return Page(items=tuple(records), next_cursor=next_cursor)

    def claim(
        self,
        tenant: TenantContext,
        run_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> Lease:
        return self._claim(tenant, worker_id, lease_seconds=lease_seconds, run_id=run_id)

    def claim_next(
        self,
        tenant: TenantContext,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> Lease | None:
        return self._claim(tenant, worker_id, lease_seconds=lease_seconds, run_id=None)

    def _claim(
        self,
        tenant: TenantContext,
        worker_id: str,
        *,
        lease_seconds: int,
        run_id: str | None,
    ) -> Lease | None:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id must be nonblank and lease_seconds positive")
        where_run = " AND run_id = %s" if run_id is not None else ""
        select_params_list: list[Any] = [tenant.platform_id, tenant.tenant_id]
        if run_id is not None:
            select_params_list.append(run_id)
        select_params = tuple(select_params_list)
        lock_clause = " FOR UPDATE SKIP LOCKED"
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    f"""
                    SELECT run_id, fencing_token
                      FROM runs
                     WHERE platform_id = %s AND tenant_id = %s
                       AND state = 'QUEUED'{where_run}
                     ORDER BY created_at, run_id
                     LIMIT 1{lock_clause}
                    """,
                    select_params,
                )
                row = cursor.fetchone()
                if row is None:
                    if run_id is not None:
                        raise ResourceNotFoundError(f"run {run_id!r} is not queued")
                    return None
                token = int(row["fencing_token"]) + 1
                expires = _now() + timedelta(seconds=lease_seconds)
                cursor.execute(
                    """
                    UPDATE runs
                       SET state = 'STARTING', worker_id = %s,
                           fencing_token = %s, lease_expires_at = %s,
                           started_at = COALESCE(started_at, %s), updated_at = %s
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                       AND state = 'QUEUED' AND fencing_token = %s
                    """,
                    (
                        worker_id,
                        token,
                        expires,
                        _now(),
                        _now(),
                        tenant.platform_id,
                        tenant.tenant_id,
                        row["run_id"],
                        token - 1,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLostError(f"run {row['run_id']!r} was claimed by another worker")
                self.audit_repository.append_in_connection(
                    connection,
                    tenant,
                    event_id=generate_id(),
                    event_type="run.claimed",
                    actor_type="WORKER",
                    run_id=str(row["run_id"]),
                    fencing_token=token,
                    details={"worker_id": worker_id, "state": "STARTING"},
                )
                return Lease(
                    run_id=str(row["run_id"]),
                    worker_id=worker_id,
                    fencing_token=token,
                    lease_expires_at=expires,
                )
            finally:
                cursor.close()

    def heartbeat(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        *,
        lease_seconds: int = 30,
    ) -> Lease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = _now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    UPDATE runs
                       SET lease_expires_at = %s, updated_at = %s
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                       AND fencing_token = %s
                       AND state IN ('STARTING', 'RUNNING', 'CANCELLING')
                       AND lease_expires_at > %s
                    """,
                    (
                        expires,
                        _now(),
                        tenant.platform_id,
                        tenant.tenant_id,
                        run_id,
                        fencing_token,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLostError(f"run {run_id!r} lease is no longer valid")
                cursor.execute(
                    """
                    SELECT worker_id FROM runs
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                    """,
                    (tenant.platform_id, tenant.tenant_id, run_id),
                )
                row = cursor.fetchone() or {}
                return Lease(
                    run_id=run_id,
                    worker_id=str(row.get("worker_id") or "unknown"),
                    fencing_token=fencing_token,
                    lease_expires_at=expires,
                )
            finally:
                cursor.close()

    def expire_leases(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[RunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = _now()
        expired_records: list[RunRecord] = []
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    SELECT run_id, session_id, idempotency_key, request_digest,
                           request_digest_version, state, worker_id, fencing_token,
                           lease_expires_at, started_at, finished_at, created_at,
                           updated_at, error_code, result_ref
                      FROM runs
                     WHERE platform_id = %s AND tenant_id = %s
                       AND state IN ('STARTING', 'RUNNING', 'CANCELLING')
                       AND lease_expires_at <= %s
                     ORDER BY lease_expires_at, run_id
                     LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (tenant.platform_id, tenant.tenant_id, now, limit),
                )
                rows = cursor.fetchall()
                for row in rows:
                    old_token = int(row["fencing_token"])
                    new_token = old_token + 1
                    cursor.execute(
                        """
                        UPDATE runs
                           SET state = 'UNKNOWN', fencing_token = %s,
                               lease_expires_at = NULL, finished_at = %s,
                               error_code = 'RUN_LEASE_EXPIRED', updated_at = %s
                         WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                           AND fencing_token = %s AND lease_expires_at <= %s
                           AND state IN ('STARTING', 'RUNNING', 'CANCELLING')
                        """,
                        (
                            new_token,
                            now,
                            now,
                            tenant.platform_id,
                            tenant.tenant_id,
                            row["run_id"],
                            old_token,
                            now,
                        ),
                    )
                    if cursor.rowcount != 1:
                        continue
                    self.audit_repository.append_in_connection(
                        connection,
                        tenant,
                        event_id=generate_id(),
                        event_type="run.lease_expired",
                        actor_type="SYSTEM",
                        run_id=str(row["run_id"]),
                        fencing_token=new_token,
                        details={"state": "UNKNOWN", "error_code": "RUN_LEASE_EXPIRED"},
                    )
                    cursor.execute(
                        """
                        UPDATE sessions SET last_run_at = %s, updated_at = %s
                         WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                        """,
                        (
                            now,
                            now,
                            tenant.platform_id,
                            tenant.tenant_id,
                            row["session_id"],
                        ),
                    )
                    expired_records.append(
                        _run_from_row(
                            tenant,
                            {
                                **row,
                                "state": RunState.UNKNOWN.value,
                                "fencing_token": new_token,
                                "lease_expires_at": None,
                                "finished_at": now,
                                "error_code": "RUN_LEASE_EXPIRED",
                                "updated_at": now,
                            },
                        )
                    )
            finally:
                cursor.close()
        return tuple(expired_records)

    def request_cancel(
        self,
        tenant: TenantContext,
        run_id: str,
    ) -> RunRecord:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """
                    SELECT run_id, session_id, idempotency_key, request_digest,
                           request_digest_version, state, worker_id, fencing_token,
                           lease_expires_at, started_at, finished_at, created_at,
                           updated_at, error_code, result_ref
                      FROM runs
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                     FOR UPDATE
                    """,
                    (tenant.platform_id, tenant.tenant_id, run_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ResourceNotFoundError(f"run {run_id!r} was not found")
                current = RunState(row["state"])
                if current is RunState.CANCELLING:
                    return _run_from_row(tenant, row)
                if current not in {RunState.STARTING, RunState.RUNNING}:
                    raise InvalidStateTransitionError(
                        f"run state {current.value} cannot be cancelled"
                    )
                now = _now()
                cursor.execute(
                    """
                    UPDATE runs SET state = 'CANCELLING', updated_at = %s
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                       AND state = %s
                    """,
                    (
                        now,
                        tenant.platform_id,
                        tenant.tenant_id,
                        run_id,
                        current.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidStateTransitionError(
                        "run changed while cancellation was requested"
                    )
                self.audit_repository.append_in_connection(
                    connection,
                    tenant,
                    event_id=generate_id(),
                    event_type="run.cancel_requested",
                    actor_type="USER",
                    actor_user_id=tenant.user_id,
                    run_id=run_id,
                    fencing_token=int(row["fencing_token"]),
                    details={"state": "CANCELLING"},
                )
                return _run_from_row(
                    tenant,
                    {**row, "state": "CANCELLING", "updated_at": now},
                )
            finally:
                cursor.close()

    def transition(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        state: RunState,
        *,
        error_code: str | None = None,
        result_ref: str | None = None,
    ) -> RunRecord:
        with self.database.transaction() as connection:
            return self.transition_in_connection(connection, tenant, run_id, fencing_token, state, error_code=error_code, result_ref=result_ref)

    def transition_in_connection(
        self, connection: Any, tenant: TenantContext, run_id: str,
        fencing_token: int, state: RunState, *, error_code: str | None = None,
        result_ref: str | None = None,
    ) -> RunRecord:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """
                SELECT run_id, session_id, idempotency_key, request_digest,
                       request_digest_version, state, worker_id, fencing_token,
                       lease_expires_at, started_at, finished_at, created_at,
                       updated_at, error_code, result_ref
                  FROM runs
                 WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                 FOR UPDATE
                """,
                (tenant.platform_id, tenant.tenant_id, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError(f"run {run_id!r} was not found")
            if int(row["fencing_token"]) != fencing_token:
                raise LeaseLostError(f"run {run_id!r} lease is no longer valid")
            current = RunState(row["state"])
            if (
                current in {RunState.STARTING, RunState.RUNNING, RunState.CANCELLING}
                and row.get("lease_expires_at") is not None
                and _as_utc(row["lease_expires_at"]) <= _now()
            ):
                raise LeaseLostError(f"run {run_id!r} lease has expired")
            validate_run_transition(current, state)
            if current is state:
                return _run_from_row(tenant, row)
            now = _now()
            terminal = state in {
                RunState.SUCCEEDED,
                RunState.FAILED,
                RunState.CANCELLED,
                RunState.UNKNOWN,
            }
            cursor.execute(
                """
                UPDATE runs
                   SET state = %s, error_code = %s, result_ref = %s,
                       finished_at = CASE WHEN %s THEN %s ELSE finished_at END,
                       lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END,
                       updated_at = %s
                 WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                   AND fencing_token = %s
                """,
                (
                    state.value,
                    error_code,
                    result_ref,
                    terminal,
                    now,
                    terminal,
                    now,
                    tenant.platform_id,
                    tenant.tenant_id,
                    run_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError(f"run {run_id!r} lease is no longer valid")
            if terminal:
                cursor.execute(
                    """
                    UPDATE sessions SET last_run_at = %s, updated_at = %s
                     WHERE platform_id = %s AND tenant_id = %s AND session_id = %s
                    """,
                    (
                        now,
                        now,
                        tenant.platform_id,
                        tenant.tenant_id,
                        row["session_id"],
                    ),
                )
            self.audit_repository.append_in_connection(
                connection,
                tenant,
                event_id=generate_id(),
                event_type="run.state_changed",
                actor_type="worker",
                run_id=run_id,
                fencing_token=fencing_token,
                details={
                    "state": state.value,
                    "error_code": error_code,
                },
            )
            return _run_from_row(
                tenant,
                {
                    **row,
                    "state": state.value,
                    "error_code": error_code,
                    "result_ref": result_ref,
                    "finished_at": now if terminal else row.get("finished_at"),
                    "lease_expires_at": None if terminal else row.get("lease_expires_at"),
                    "updated_at": now,
                },
            )
        finally:
            cursor.close()

    def mark_unknown(
        self,
        tenant: TenantContext,
        run_id: str,
        fencing_token: int,
        *,
        error_code: str = "UNKNOWN_RESULT",
    ) -> RunRecord:
        return self.transition(
            tenant,
            run_id,
            fencing_token,
            RunState.UNKNOWN,
            error_code=error_code,
        )


def _upload_from_row(tenant: TenantContext, row: Mapping[str, Any]) -> UploadIntent:
    return UploadIntent(tenant=tenant, **{
        key: row[key] for key in UploadIntent.__dataclass_fields__
        if key != "tenant" and key in row
    })


def _queue_content(connection: Any, tenant: TenantContext, ref: str, object_type: str) -> None:
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """INSERT INTO content_gc_queue
                   (platform_id, tenant_id, cleanup_id, object_ref, object_type)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   status = IF(status = 'DELETED', 'QUEUED', status),
                   next_attempt_at = CURRENT_TIMESTAMP(6)""",
            (*tenant.scope, generate_id(), ref, object_type),
        )
    finally:
        cursor.close()


class MySqlContentUploadRepository:
    """The intent row fences publication against expiry in the database clock."""

    def __init__(self, database: MySqlDatabase) -> None:
        self.database = database

    @staticmethod
    def _lease_seconds(value: int) -> None:
        if not isinstance(value, int) or not 1 <= value <= 86400:
            raise ValueError("lease_seconds must be between 1 and 86400")

    def reserve(self, tenant: TenantContext, object_type: str, *, session_id: str | None = None,
                message_id: str | None = None, run_id: str | None = None,
                lease_seconds: int = 300) -> UploadIntent:
        self._lease_seconds(lease_seconds)
        # Validate type before opening a transaction. Neither ID contains user content.
        upload_id, ref = generate_id(), generate_id()
        UploadIntent(upload_id, tenant, ref, object_type, "UPLOADING", _now())
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """INSERT INTO content_upload_intents
                       (platform_id, tenant_id, upload_id, object_ref, object_type,
                        session_id, message_id, run_id, lease_expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                               TIMESTAMPADD(SECOND, %s, CURRENT_TIMESTAMP(6)))""",
                    (*tenant.scope, upload_id, ref, object_type, session_id, message_id, run_id, lease_seconds),
                )
                return _upload_from_row(tenant, self._lock(connection, tenant, upload_id))
            finally:
                cursor.close()

    @staticmethod
    def _lock(connection: Any, tenant: TenantContext, upload_id: str) -> Mapping[str, Any]:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT *, lease_expires_at > CURRENT_TIMESTAMP(6) AS lease_active
                   FROM content_upload_intents
                   WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s FOR UPDATE""",
                (*tenant.scope, upload_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("upload intent was not found")
            return row
        finally:
            cursor.close()

    def renew(self, tenant: TenantContext, upload_id: str, *, lease_seconds: int = 300) -> UploadIntent:
        self._lease_seconds(lease_seconds)
        with self.database.transaction() as connection:
            self._lock(connection, tenant, upload_id)
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """UPDATE content_upload_intents
                       SET lease_expires_at = TIMESTAMPADD(SECOND, %s, CURRENT_TIMESTAMP(6))
                       WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s
                         AND status = 'UPLOADING' AND lease_expires_at > CURRENT_TIMESTAMP(6)""",
                    (lease_seconds, *tenant.scope, upload_id),
                )
                if cursor.rowcount != 1:
                    raise LeaseLostError("upload intent is expired or already published")
                return _upload_from_row(tenant, self._lock(connection, tenant, upload_id))
            finally:
                cursor.close()

    @staticmethod
    def _abandon(connection: Any, tenant: TenantContext, upload_id: str) -> None:
        row = MySqlContentUploadRepository._lock(connection, tenant, upload_id)
        if row["status"] == "PUBLISHED":
            raise LeaseLostError("published upload cannot be abandoned")
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """UPDATE content_upload_intents SET status = 'EXPIRED'
                   WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s""",
                (*tenant.scope, upload_id),
            )
            _queue_content(connection, tenant, row["object_ref"], row["object_type"])
        finally:
            cursor.close()

    def claim_expired(self, tenant: TenantContext, *, limit: int = 100) -> tuple[UploadIntent, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT * FROM content_upload_intents
                       WHERE platform_id = %s AND tenant_id = %s AND status = 'UPLOADING'
                         AND lease_expires_at <= CURRENT_TIMESTAMP(6)
                       ORDER BY lease_expires_at, upload_id LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (*tenant.scope, limit),
                )
                rows = cursor.fetchall()
                for row in rows:
                    cursor.execute(
                        """UPDATE content_upload_intents SET status = 'EXPIRED'
                           WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s""",
                        (*tenant.scope, row["upload_id"]),
                    )
                    _queue_content(connection, tenant, row["object_ref"], row["object_type"])
                return tuple(_upload_from_row(tenant, {**r, "status": "EXPIRED"}) for r in rows)
            finally:
                cursor.close()

    @staticmethod
    def publish(connection: Any, tenant: TenantContext, upload_id: str, message: MessageRecord) -> None:
        row = MySqlContentUploadRepository._lock(connection, tenant, upload_id)
        allowed_types = {"MESSAGE_CONTENT"}
        if message.role is MessageRole.ASSISTANT:
            allowed_types.add("MANIFEST")
        if row["object_ref"] != message.content_ref or row["object_type"] not in allowed_types:
            raise ValueError("upload intent does not match message content")
        for key in ("session_id", "message_id", "run_id"):
            if row.get(key) != getattr(message, key):
                raise ValueError("upload intent source does not match message")
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """UPDATE content_upload_intents SET status = 'PUBLISHED'
                   WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s
                     AND status = 'UPLOADING' AND lease_expires_at > CURRENT_TIMESTAMP(6)""",
                (*tenant.scope, upload_id),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError("upload intent is expired or already published")
        finally:
            cursor.close()

    @staticmethod
    def publish_chunk(
        connection: Any,
        tenant: TenantContext,
        upload_id: str,
        chunk: MessageChunkRecord,
        *,
        session_id: str,
        run_id: str,
    ) -> None:
        row = MySqlContentUploadRepository._lock(connection, tenant, upload_id)
        if (
            row["object_ref"] != chunk.content_ref
            or row["object_type"] != "STREAM_CHUNK"
            or row.get("session_id") != session_id
            or row.get("message_id") != chunk.message_id
            or row.get("run_id") != run_id
        ):
            raise ValueError("upload intent does not match stream chunk")
        if row["status"] != "UPLOADING" or not row.get("lease_active"):
            raise LeaseLostError("upload intent is expired or already published")
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """UPDATE content_upload_intents SET status = 'PUBLISHED'
                   WHERE platform_id = %s AND tenant_id = %s AND upload_id = %s
                     AND status = 'UPLOADING' AND lease_expires_at > CURRENT_TIMESTAMP(6)""",
                (*tenant.scope, upload_id),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError("upload intent is expired or already published")
        finally:
            cursor.close()


def _message_from_row(tenant: TenantContext, row: Mapping[str, Any]) -> MessageRecord:
    return MessageRecord(tenant=tenant, **{
        key: _as_utc(row[key]) if key in {"created_at", "updated_at", "completed_at"} and row[key] is not None else row[key]
        for key in MessageRecord.__dataclass_fields__
        if key != "tenant" and key in row
    })


def _message_chunk_from_row(row: Mapping[str, Any]) -> MessageChunkRecord:
    return MessageChunkRecord(
        message_id=str(row["message_id"]),
        chunk_sequence=int(row["chunk_sequence"]),
        content_ref=str(row["content_ref"]),
        content_sha256=str(row["content_sha256"]),
        byte_length=int(row["byte_length"]),
        created_at=_as_utc(row["created_at"]),
    )


class MySqlMessageRepository:
    def __init__(self, database: MySqlDatabase, audit_repository: MySqlAuditRepository | None = None) -> None:
        self.database = database
        self.audit_repository = audit_repository or MySqlAuditRepository(database)
        self.runs = MySqlRunRepository(database, self.audit_repository)

    @staticmethod
    def _scope(tenant: TenantContext, record: MessageRecord) -> None:
        if record.tenant.scope != tenant.scope:
            raise ValueError("message record tenant does not match explicit tenant context")

    @staticmethod
    def _run_lock(connection: Any, tenant: TenantContext, run_id: str,
                  fencing_token: int | None = None) -> Mapping[str, Any]:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT *, lease_expires_at > CURRENT_TIMESTAMP(6) AS lease_active FROM runs
                   WHERE platform_id = %s AND tenant_id = %s AND run_id = %s FOR UPDATE""",
                (*tenant.scope, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("run was not found")
            if fencing_token is not None:
                if row["fencing_token"] != fencing_token:
                    raise LeaseLostError("worker fencing token is stale")
                if row["state"] != "SUCCEEDED" and not row.get("lease_active"):
                    raise LeaseLostError("worker lease has expired")
            return row
        finally:
            cursor.close()

    @staticmethod
    def _session_lock(connection: Any, tenant: TenantContext, session_id: str) -> Mapping[str, Any]:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT status, last_message_sequence FROM sessions
                   WHERE platform_id = %s AND tenant_id = %s AND session_id = %s FOR UPDATE""",
                (*tenant.scope, session_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("session was not found")
            return row
        finally:
            cursor.close()

    def _insert(self, connection: Any, tenant: TenantContext, record: MessageRecord) -> MessageRecord:
        session = self._session_lock(connection, tenant, record.session_id)
        sequence = int(session["last_message_sequence"]) + 1
        record = replace(record, tenant=tenant, sequence_no=sequence,
                         completed_at=_now() if record.status is MessageStatus.COMPLETE else None)
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """UPDATE sessions SET last_message_sequence = %s
                   WHERE platform_id = %s AND tenant_id = %s AND session_id = %s""",
                (sequence, *tenant.scope, record.session_id),
            )
            cursor.execute(
                """INSERT INTO messages
                   (platform_id, tenant_id, message_id, session_id, run_id, sequence_no, role,
                    status, content_ref, content_sha256, byte_length, content_type,
                    created_at, updated_at, completed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (*tenant.scope, record.message_id, record.session_id, record.run_id, sequence,
                 record.role.value, record.status.value, record.content_ref, record.content_sha256,
                 record.byte_length, record.content_type, record.created_at, record.updated_at, record.completed_at),
            )
            return self._read(connection, tenant, record.message_id)
        finally:
            cursor.close()

    @staticmethod
    def _for_run(connection: Any, tenant: TenantContext, run_id: str, role: MessageRole) -> Mapping[str, Any] | None:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT * FROM messages
                   WHERE platform_id = %s AND tenant_id = %s AND run_id = %s AND role = %s
                   ORDER BY sequence_no LIMIT 1 FOR UPDATE""",
                (*tenant.scope, run_id, role.value),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def submit_run(self, tenant: TenantContext, run: RunRecord, message: MessageRecord, *, upload_id: str) -> RunRecord:
        self._scope(tenant, message)
        if (message.role is not MessageRole.USER or message.status is not MessageStatus.COMPLETE
                or message.run_id != run.run_id or message.session_id != run.session_id
                or run.state is not RunState.QUEUED):
            raise ValueError("submission requires a matching queued run and complete user message")
        with self.database.transaction() as connection:
            stored = self.runs.create_or_get_in_connection(connection, tenant, run)
            existing = self._for_run(connection, tenant, stored.run_id, MessageRole.USER)
            if existing is not None:
                if existing["content_ref"] != message.content_ref:
                    MySqlContentUploadRepository._abandon(connection, tenant, upload_id)
                return replace(stored, message_ids=(str(existing["message_id"]),))
            if stored.run_id != run.run_id or stored.state is not RunState.QUEUED:
                raise InvalidStateTransitionError("existing run has no persisted user message")
            session = self._session_lock(connection, tenant, run.session_id)
            if session["status"] != "ACTIVE":
                raise InvalidStateTransitionError("session is not active")
            MySqlContentUploadRepository.publish(connection, tenant, upload_id, message)
            inserted = self._insert(connection, tenant, message)
            self.audit_repository.append_in_connection(
                connection, tenant, event_id=generate_id(), event_type="message.created",
                actor_type="USER", actor_user_id=tenant.user_id,
                session_id=run.session_id, run_id=run.run_id,
            )
            return replace(stored, message_ids=(inserted.message_id,))

    def create(self, tenant: TenantContext, record: MessageRecord, *, upload_id: str | None = None,
               fencing_token: int | None = None) -> MessageRecord:
        self._scope(tenant, record)
        if record.role is MessageRole.ASSISTANT and record.status is MessageStatus.COMPLETE:
            raise ValueError("assistant completion requires complete()")
        if (record.content_ref is None) != (upload_id is None):
            raise ValueError("content requires an upload intent")
        with self.database.transaction() as connection:
            if record.run_id is not None:
                run = self._run_lock(connection, tenant, record.run_id, fencing_token)
                if run["session_id"] != record.session_id:
                    raise ValueError("message session does not match run")
                if self._for_run(connection, tenant, record.run_id, record.role) is not None:
                    raise InvalidStateTransitionError("run already has this message role")
            if tenant.auth_source == "worker" and fencing_token is None:
                raise LeaseLostError("worker writes require a fencing token")
            if upload_id is not None:
                MySqlContentUploadRepository.publish(connection, tenant, upload_id, record)
            return self._insert(connection, tenant, record)

    def complete(self, tenant: TenantContext, record: MessageRecord, *, upload_id: str,
                 fencing_token: int) -> MessageRecord:
        self._scope(tenant, record)
        if record.role is not MessageRole.ASSISTANT or record.status is not MessageStatus.COMPLETE or record.run_id is None:
            raise ValueError("completion requires a complete assistant message with a run")
        with self.database.transaction() as connection:
            run = self._run_lock(connection, tenant, record.run_id, fencing_token)
            if run["session_id"] != record.session_id:
                raise ValueError("message session does not match run")
            existing = self._for_run(connection, tenant, record.run_id, MessageRole.ASSISTANT)
            if run["state"] == "SUCCEEDED":
                if existing is None or existing["content_ref"] != run["result_ref"]:
                    raise InvalidStateTransitionError("run has no matching assistant completion")
                # A retry may upload to a fresh reservation or repeat the published intent.
                if existing["content_ref"] != record.content_ref:
                    MySqlContentUploadRepository._abandon(connection, tenant, upload_id)
                return _message_from_row(tenant, existing)
            validate_run_transition(RunState(run["state"]), RunState.SUCCEEDED)
            MySqlContentUploadRepository.publish(connection, tenant, upload_id, record)
            if existing is None:
                stored = self._insert(connection, tenant, record)
            else:
                if existing["message_id"] != record.message_id or existing["status"] not in {"PENDING", "STREAMING"}:
                    raise InvalidStateTransitionError("assistant message cannot be completed")
                stored = replace(record, sequence_no=int(existing["sequence_no"]), completed_at=_now())
                cursor = _cursor(connection)
                try:
                    cursor.execute(
                        """UPDATE messages SET status = 'COMPLETE', content_ref = %s, content_sha256 = %s,
                           byte_length = %s, content_type = %s, completed_at = CURRENT_TIMESTAMP(6)
                           WHERE platform_id = %s AND tenant_id = %s AND message_id = %s""",
                        (record.content_ref, record.content_sha256, record.byte_length, record.content_type,
                         *tenant.scope, record.message_id),
                    )
                finally:
                    cursor.close()
            self.runs.transition_in_connection(connection, tenant, record.run_id, fencing_token,
                                               RunState.SUCCEEDED, result_ref=stored.content_ref)
            return self._read(connection, tenant, stored.message_id)

    @staticmethod
    def _stream_message_lock(
        connection: Any, tenant: TenantContext, message_id: str
    ) -> Mapping[str, Any]:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT * FROM messages
                   WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                   FOR UPDATE""",
                (*tenant.scope, message_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("message was not found")
            return row
        finally:
            cursor.close()

    @staticmethod
    def _stream_message_identity(
        connection: Any, tenant: TenantContext, message_id: str
    ) -> Mapping[str, Any]:
        cursor = _cursor(connection)
        try:
            cursor.execute(
                """SELECT run_id, session_id FROM messages
                   WHERE platform_id = %s AND tenant_id = %s AND message_id = %s""",
                (*tenant.scope, message_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("message was not found")
            return row
        finally:
            cursor.close()

    def append_chunk(
        self,
        tenant: TenantContext,
        record: MessageChunkRecord,
        *,
        upload_id: str,
        fencing_token: int,
    ) -> MessageChunkRecord:
        """Publish exactly the next stream checkpoint under the current run lease."""
        if not upload_id.strip():
            raise ValueError("upload_id must not be blank")
        with self.database.transaction() as connection:
            identity = self._stream_message_identity(connection, tenant, record.message_id)
            run_id = identity["run_id"]
            if run_id is None:
                raise InvalidStateTransitionError("stream message is not attached to a run")
            run = self._run_lock(connection, tenant, str(run_id), fencing_token)
            message = self._stream_message_lock(connection, tenant, record.message_id)
            if run["session_id"] != message["session_id"]:
                raise InvalidStateTransitionError("stream message session does not match run")
            if run["state"] not in {"RUNNING", "CANCELLING"}:
                raise InvalidStateTransitionError("run is not active for stream checkpoints")
            if message["role"] != MessageRole.ASSISTANT.value:
                raise InvalidStateTransitionError("stream checkpoints require an assistant message")
            if message["status"] not in {MessageStatus.PENDING.value, MessageStatus.STREAMING.value}:
                raise InvalidStateTransitionError("assistant message is not accepting checkpoints")
            high_water = int(message["last_committed_chunk_sequence"])
            if record.chunk_sequence != high_water + 1:
                raise InvalidStateTransitionError(
                    "stream chunk sequence must be the next contiguous value"
                )

            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """INSERT INTO message_chunks
                       (platform_id, tenant_id, message_id, chunk_sequence, content_ref,
                        content_sha256, byte_length, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        *tenant.scope,
                        record.message_id,
                        record.chunk_sequence,
                        record.content_ref,
                        record.content_sha256,
                        record.byte_length,
                        record.created_at,
                    ),
                )
                cursor.execute(
                    """UPDATE messages
                       SET status = 'STREAMING', last_committed_chunk_sequence = %s
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                         AND last_committed_chunk_sequence = %s
                         AND status IN ('PENDING', 'STREAMING')""",
                    (
                        record.chunk_sequence,
                        *tenant.scope,
                        record.message_id,
                        high_water,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidStateTransitionError(
                        "assistant stream cursor changed before checkpoint commit"
                    )
            finally:
                cursor.close()

            MySqlContentUploadRepository.publish_chunk(
                connection,
                tenant,
                upload_id,
                record,
                session_id=str(message["session_id"]),
                run_id=str(run_id),
            )
            return record

    def chunk_high_water(self, tenant: TenantContext, message_id: str) -> int:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT last_committed_chunk_sequence FROM messages
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s""",
                    (*tenant.scope, message_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ResourceNotFoundError("message was not found")
                return int(row["last_committed_chunk_sequence"])
            finally:
                cursor.close()

    def list_chunks_after(
        self,
        tenant: TenantContext,
        *,
        message_id: str,
        last_chunk_sequence: int,
        limit: int = 100,
    ) -> Page[MessageChunkRecord]:
        if last_chunk_sequence < 0:
            raise ValueError("last_chunk_sequence must not be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT last_committed_chunk_sequence FROM messages
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s""",
                    (*tenant.scope, message_id),
                )
                message = cursor.fetchone()
                if message is None:
                    raise ResourceNotFoundError("message was not found")
                high_water = int(message["last_committed_chunk_sequence"])
                if last_chunk_sequence > high_water:
                    raise ValueError("cursor is ahead of committed stream")
                cursor.execute(
                    """SELECT message_id, chunk_sequence, content_ref, content_sha256,
                              byte_length, created_at
                       FROM message_chunks
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                         AND chunk_sequence > %s
                       ORDER BY chunk_sequence LIMIT %s""",
                    (*tenant.scope, message_id, last_chunk_sequence, limit + 1),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()

        has_more = len(rows) > limit
        chunks = tuple(_message_chunk_from_row(row) for row in rows[:limit])
        expected = last_chunk_sequence + 1
        for chunk in chunks:
            if chunk.chunk_sequence != expected:
                raise InvalidStateTransitionError("stored stream chunk prefix has a gap")
            expected += 1
        if not chunks and last_chunk_sequence < high_water:
            raise InvalidStateTransitionError("stored stream chunk prefix has a gap")
        if not has_more and expected - 1 < high_water:
            raise InvalidStateTransitionError("stored stream chunk prefix has a trailing gap")
        next_cursor = str(chunks[-1].chunk_sequence) if has_more and chunks else None
        return Page(items=chunks, next_cursor=next_cursor)

    def get_for_run(
        self,
        tenant: TenantContext,
        run_id: str,
        role: MessageRole = MessageRole.ASSISTANT,
    ) -> MessageRecord | None:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT * FROM messages
                       WHERE platform_id = %s AND tenant_id = %s AND run_id = %s AND role = %s
                       ORDER BY sequence_no LIMIT 1""",
                    (*tenant.scope, run_id, MessageRole(role).value),
                )
                row = cursor.fetchone()
                return _message_from_row(tenant, row) if row is not None else None
            finally:
                cursor.close()

    def list_unfinalized_streams(
        self,
        tenant: TenantContext,
        *,
        limit: int = 100,
    ) -> tuple[tuple[RunRecord, MessageRecord], ...]:
        """Find expired UNKNOWN runs whose assistant stream still needs reaping."""
        if tenant.auth_source != "system":
            raise AuthorizationError("system authentication is required")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT
                           r.run_id AS run_id, r.session_id AS run_session_id,
                           r.idempotency_key, r.request_digest, r.request_digest_version,
                           r.state AS run_state, r.worker_id, r.fencing_token,
                           r.lease_expires_at, r.started_at, r.finished_at,
                           r.created_at AS run_created_at, r.updated_at AS run_updated_at,
                           r.error_code, r.result_ref,
                           m.message_id, m.session_id AS message_session_id,
                           m.run_id AS message_run_id, m.sequence_no, m.role, m.status,
                           m.content_ref, m.content_sha256, m.byte_length, m.content_type,
                           m.created_at AS message_created_at,
                           m.updated_at AS message_updated_at, m.completed_at
                       FROM runs AS r
                       JOIN messages AS m
                         ON m.platform_id = r.platform_id
                        AND m.tenant_id = r.tenant_id
                        AND m.run_id = r.run_id
                       WHERE r.platform_id = %s AND r.tenant_id = %s
                         AND r.state = 'UNKNOWN'
                         AND (r.lease_expires_at IS NULL
                              OR r.lease_expires_at <= CURRENT_TIMESTAMP(6))
                         AND m.role = 'ASSISTANT' AND m.status IN ('PENDING', 'STREAMING')
                       ORDER BY r.updated_at, r.run_id LIMIT %s""",
                    (*tenant.scope, limit),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()
        pending: list[tuple[RunRecord, MessageRecord]] = []
        for row in rows:
            run = _run_from_row(
                tenant,
                {
                    "run_id": row["run_id"],
                    "session_id": row["run_session_id"],
                    "idempotency_key": row["idempotency_key"],
                    "request_digest": row["request_digest"],
                    "request_digest_version": row["request_digest_version"],
                    "state": row["run_state"],
                    "worker_id": row["worker_id"],
                    "fencing_token": row["fencing_token"],
                    "lease_expires_at": row["lease_expires_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "created_at": row["run_created_at"],
                    "updated_at": row["run_updated_at"],
                    "error_code": row["error_code"],
                    "result_ref": row["result_ref"],
                },
            )
            message = _message_from_row(
                tenant,
                {
                    "message_id": row["message_id"],
                    "session_id": row["message_session_id"],
                    "run_id": row["message_run_id"],
                    "sequence_no": row["sequence_no"],
                    "role": row["role"],
                    "status": row["status"],
                    "content_ref": row["content_ref"],
                    "content_sha256": row["content_sha256"],
                    "byte_length": row["byte_length"],
                    "content_type": row["content_type"],
                    "created_at": row["message_created_at"],
                    "updated_at": row["message_updated_at"],
                    "completed_at": row["completed_at"],
                },
            )
            pending.append((run, message))
        return tuple(pending)

    def finalize_worker_partial(
        self,
        tenant: TenantContext,
        record: MessageRecord,
        *,
        upload_id: str,
        fencing_token: int,
        run_state: RunState = RunState.UNKNOWN,
        error_code: str | None = None,
    ) -> MessageRecord:
        """Publish a partial manifest and terminal state under the active worker lease."""
        if tenant.auth_source != "worker":
            raise AuthorizationError("worker authentication is required")
        if run_state not in {RunState.FAILED, RunState.CANCELLED, RunState.UNKNOWN}:
            raise ValueError("partial worker finalization requires a terminal run state")
        if error_code is None:
            error_code = {
                RunState.FAILED: "ENGINE_FAILED",
                RunState.CANCELLED: "CANCELLED",
                RunState.UNKNOWN: "ENGINE_RESULT_UNKNOWN",
            }[run_state]
        return self._finalize_partial(
            tenant,
            record,
            upload_id=upload_id,
            fencing_token=fencing_token,
            run_state=run_state,
            error_code=error_code,
            reaper=False,
        )

    def recover_partial(
        self,
        tenant: TenantContext,
        record: MessageRecord,
        *,
        upload_id: str,
        fencing_token: int,
    ) -> MessageRecord:
        """Publish a manifest for a lease-expired UNKNOWN run as visible partial content."""
        if tenant.auth_source != "system":
            raise AuthorizationError("system authentication is required")
        return self._finalize_partial(
            tenant,
            record,
            upload_id=upload_id,
            fencing_token=fencing_token,
            run_state=RunState.UNKNOWN,
            error_code=None,
            reaper=True,
        )

    def _finalize_partial(
        self,
        tenant: TenantContext,
        record: MessageRecord,
        *,
        upload_id: str,
        fencing_token: int,
        run_state: RunState,
        error_code: str | None,
        reaper: bool,
    ) -> MessageRecord:
        self._scope(tenant, record)
        if (
            record.role is not MessageRole.ASSISTANT
            or record.status is not MessageStatus.PARTIAL
            or record.run_id is None
            or record.content_ref is None
        ):
            raise ValueError("partial recovery requires an assistant manifest")
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT *, lease_expires_at > CURRENT_TIMESTAMP(6) AS lease_active
                       FROM runs
                       WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, record.run_id),
                )
                run = cursor.fetchone()
                if run is None:
                    raise ResourceNotFoundError("run was not found")
                if int(run["fencing_token"]) != fencing_token:
                    raise LeaseLostError("run fencing token is stale")
                if reaper:
                    if run["state"] != RunState.UNKNOWN.value or run.get("lease_active"):
                        raise LeaseLostError("run is not an expired UNKNOWN run for this fence")
                elif (
                    run["state"] not in {"RUNNING", "CANCELLING"}
                    or not run.get("lease_active")
                    or (run_state is RunState.CANCELLED and run["state"] != "CANCELLING")
                ):
                    raise LeaseLostError("worker lease is not active for partial finalization")
                if run["session_id"] != record.session_id:
                    raise ValueError("partial message session does not match run")
                message = self._stream_message_lock(connection, tenant, record.message_id)
                if (
                    message["run_id"] != record.run_id
                    or message["session_id"] != record.session_id
                    or message["role"] != MessageRole.ASSISTANT.value
                    or message["status"] not in {"PENDING", "STREAMING"}
                ):
                    raise InvalidStateTransitionError(
                        "assistant message is not eligible for partial recovery"
                    )
                MySqlContentUploadRepository.publish(
                    connection, tenant, upload_id, record
                )
                cursor.execute(
                    """UPDATE messages
                       SET status = 'PARTIAL', content_ref = %s, content_sha256 = %s,
                           byte_length = %s, content_type = %s, completed_at = CURRENT_TIMESTAMP(6)
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                         AND status IN ('PENDING', 'STREAMING')""",
                    (
                        record.content_ref,
                        record.content_sha256,
                        record.byte_length,
                        record.content_type,
                        *tenant.scope,
                        record.message_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidStateTransitionError(
                        "assistant message changed before partial recovery"
                    )
                if reaper:
                    cursor.execute(
                        """UPDATE runs SET result_ref = %s
                           WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                             AND state = 'UNKNOWN' AND fencing_token = %s
                             AND (lease_expires_at IS NULL
                                  OR lease_expires_at <= CURRENT_TIMESTAMP(6))""",
                        (record.content_ref, *tenant.scope, record.run_id, fencing_token),
                    )
                    if cursor.rowcount != 1:
                        raise LeaseLostError("expired run changed before partial recovery")
            finally:
                cursor.close()
            if not reaper:
                self.runs.transition_in_connection(
                    connection,
                    tenant,
                    str(record.run_id),
                    fencing_token,
                    run_state,
                    error_code=error_code,
                    result_ref=record.content_ref,
                )
            self.audit_repository.append_in_connection(
                connection,
                tenant,
                event_id=generate_id(),
                event_type="message.partial",
                actor_type="SYSTEM" if reaper else "WORKER",
                session_id=record.session_id,
                run_id=record.run_id,
                fencing_token=fencing_token,
                details={"status": "PARTIAL", "state": run_state.value},
            )
            return self._read(connection, tenant, record.message_id)

    def fail_pending_message(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        fencing_token: int,
        terminal_state: RunState = RunState.FAILED,
        error_code: str = "ENGINE_FAILED",
    ) -> MessageRecord:
        """Atomically fail a not-yet-streamed assistant message and its run."""
        if terminal_state not in {RunState.FAILED, RunState.CANCELLED, RunState.UNKNOWN}:
            raise ValueError("empty assistant completion requires a terminal run state")
        with self.database.transaction() as connection:
            identity = self._stream_message_identity(connection, tenant, message_id)
            run_id = identity["run_id"]
            if run_id is None:
                raise InvalidStateTransitionError("assistant message is not attached to a run")
            self._run_lock(connection, tenant, str(run_id), fencing_token)
            message = self._stream_message_lock(connection, tenant, message_id)
            if (
                message["role"] != MessageRole.ASSISTANT.value
                or message["status"] != MessageStatus.PENDING.value
                or int(message["last_committed_chunk_sequence"]) != 0
            ):
                raise InvalidStateTransitionError(
                    "only a pending assistant message without chunks can be failed"
                )
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """UPDATE messages SET status = 'FAILED', completed_at = CURRENT_TIMESTAMP(6)
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                         AND status = 'PENDING' AND last_committed_chunk_sequence = 0""",
                    (*tenant.scope, message_id),
                )
                if cursor.rowcount != 1:
                    raise InvalidStateTransitionError("assistant message changed before failure")
            finally:
                cursor.close()
            self.runs.transition_in_connection(
                connection,
                tenant,
                str(run_id),
                fencing_token,
                terminal_state,
                error_code=error_code,
            )
            return self._read(connection, tenant, message_id)

    def fail_expired_pending(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        fencing_token: int,
    ) -> MessageRecord:
        """Fail an empty assistant message after the system reaper expires its run."""
        if tenant.auth_source != "system":
            raise AuthorizationError("system authentication is required")
        with self.database.transaction() as connection:
            identity = self._stream_message_identity(connection, tenant, message_id)
            run_id = identity["run_id"]
            if run_id is None:
                raise InvalidStateTransitionError("assistant message is not attached to a run")
            cursor = _cursor(connection)
            try:
                cursor.execute(
                    """SELECT *, lease_expires_at > CURRENT_TIMESTAMP(6) AS lease_active
                       FROM runs WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                       FOR UPDATE""",
                    (*tenant.scope, run_id),
                )
                run = cursor.fetchone()
                if run is None:
                    raise ResourceNotFoundError("run was not found")
                if (
                    run["state"] != RunState.UNKNOWN.value
                    or int(run["fencing_token"]) != fencing_token
                    or run.get("lease_active")
                ):
                    raise LeaseLostError("run is not an expired UNKNOWN run for this fence")
                message = self._stream_message_lock(connection, tenant, message_id)
                if (
                    message["run_id"] != run_id
                    or message["role"] != MessageRole.ASSISTANT.value
                    or message["status"] != MessageStatus.PENDING.value
                    or int(message["last_committed_chunk_sequence"]) != 0
                ):
                    raise InvalidStateTransitionError(
                        "only a pending assistant message without chunks can be failed"
                    )
                cursor.execute(
                    """UPDATE messages SET status = 'FAILED', completed_at = CURRENT_TIMESTAMP(6)
                       WHERE platform_id = %s AND tenant_id = %s AND message_id = %s
                         AND status = 'PENDING' AND last_committed_chunk_sequence = 0""",
                    (*tenant.scope, message_id),
                )
                if cursor.rowcount != 1:
                    raise InvalidStateTransitionError("assistant message changed before failure")
            finally:
                cursor.close()
            self.audit_repository.append_in_connection(
                connection,
                tenant,
                event_id=generate_id(),
                event_type="message.failed",
                actor_type="SYSTEM",
                session_id=str(message["session_id"]),
                run_id=str(run_id),
                fencing_token=fencing_token,
                details={"status": "FAILED", "state": "UNKNOWN"},
            )
            return self._read(connection, tenant, message_id)

    @staticmethod
    def _read(connection: Any, tenant: TenantContext, message_id: str) -> MessageRecord:
        cursor = _cursor(connection)
        try:
            cursor.execute("SELECT * FROM messages WHERE platform_id = %s AND tenant_id = %s AND message_id = %s",
                           (*tenant.scope, message_id))
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("message was not found")
            return _message_from_row(tenant, row)
        finally:
            cursor.close()

    def get(self, tenant: TenantContext, message_id: str) -> MessageRecord:
        with self.database.transaction() as connection:
            return self._read(connection, tenant, message_id)

    def list(self, tenant: TenantContext, *, session_id: str, limit: int = 50,
             cursor: str | None = None) -> Page[MessageRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        after = 0
        if cursor is not None:
            try:
                after = int(cursor)
                if after < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("invalid message cursor") from exc
        with self.database.transaction() as connection:
            db_cursor = _cursor(connection)
            try:
                db_cursor.execute("SELECT session_id FROM sessions WHERE platform_id = %s AND tenant_id = %s AND session_id = %s",
                                  (*tenant.scope, session_id))
                if db_cursor.fetchone() is None:
                    raise ResourceNotFoundError("session was not found")
                db_cursor.execute(
                    """SELECT * FROM messages WHERE platform_id = %s AND tenant_id = %s
                       AND session_id = %s AND sequence_no > %s ORDER BY sequence_no LIMIT %s""",
                    (*tenant.scope, session_id, after, limit + 1),
                )
                rows = db_cursor.fetchall()
                items = tuple(_message_from_row(tenant, r) for r in rows[:limit])
                return Page(items, str(items[-1].sequence_no) if len(rows) > limit else None)
            finally:
                db_cursor.close()

    def delete(self, tenant: TenantContext, message_id: str) -> None:
        with self.database.transaction() as connection:
            cursor = _cursor(connection)
            try:
                cursor.execute("SELECT run_id FROM messages WHERE platform_id = %s AND tenant_id = %s AND message_id = %s",
                               (*tenant.scope, message_id))
                first = cursor.fetchone()
                if first is None:
                    raise ResourceNotFoundError("message was not found")
                if first["run_id"] is not None:
                    self._run_lock(connection, tenant, first["run_id"])
                cursor.execute("SELECT * FROM messages WHERE platform_id = %s AND tenant_id = %s AND message_id = %s FOR UPDATE",
                               (*tenant.scope, message_id))
                row = cursor.fetchone()
                if row is None:
                    raise ResourceNotFoundError("message was not found")
                cursor.execute("SELECT content_ref FROM message_chunks WHERE platform_id = %s AND tenant_id = %s AND message_id = %s FOR UPDATE",
                               (*tenant.scope, message_id))
                for chunk in cursor.fetchall():
                    _queue_content(connection, tenant, chunk["content_ref"], "STREAM_CHUNK")
                if row["content_ref"] is not None:
                    _queue_content(connection, tenant, row["content_ref"], "MESSAGE_CONTENT")
                    cursor.execute("""UPDATE runs SET result_ref = NULL WHERE platform_id = %s AND tenant_id = %s
                                      AND run_id = %s AND result_ref = %s""",
                                   (*tenant.scope, row["run_id"], row["content_ref"]))
                cursor.execute("DELETE FROM messages WHERE platform_id = %s AND tenant_id = %s AND message_id = %s",
                               (*tenant.scope, message_id))
            finally:
                cursor.close()


_ALLOWED_AUDIT_DETAIL_KEYS = {
    "engine_name",
    "error_code",
    "finish_reason",
    "from",
    "state",
    "status",
    "to",
    "worker_id",
}
_SAFE_AUDIT_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
_SAFE_ERROR_CODE = re.compile(r"[A-Z0-9_.-]{1,128}\Z")
_AUDIT_STATES = {
    "ACTIVE",
    "PAUSED",
    "INTERRUPTED",
    "CLOSED",
    "QUEUED",
    "STARTING",
    "RUNNING",
    "CANCELLING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "UNKNOWN",
    "PARTIAL",
}


def _safe_audit_details(details: Mapping[str, object] | None) -> str:
    value = dict(details or {})
    for key, item in value.items():
        if not isinstance(key, str) or key not in _ALLOWED_AUDIT_DETAIL_KEYS:
            raise ValueError("audit details contain an unknown field")
        if key == "error_code":
            if item is not None and (
                not isinstance(item, str) or _SAFE_ERROR_CODE.fullmatch(item) is None
            ):
                raise ValueError("audit details contain an invalid error code")
            continue
        if key in {"state", "status", "from", "to"}:
            if not isinstance(item, str) or item not in _AUDIT_STATES:
                raise ValueError("audit details contain an invalid state value")
            continue
        if not isinstance(item, str) or _SAFE_AUDIT_VALUE.fullmatch(item) is None:
            raise ValueError("audit details contain an invalid scalar value")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("audit details exceed the size limit")
    return encoded


class MySqlAuditRepository:
    def __init__(self, database: MySqlDatabase) -> None:
        self.database = database

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
    ) -> str:
        event_id = generate_id()
        with self.database.transaction() as connection:
            self.append_in_connection(
                connection,
                tenant,
                event_id=event_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                session_id=session_id,
                run_id=run_id,
                fencing_token=fencing_token,
                policy_version=policy_version,
                details=details,
                occurred_at=occurred_at,
            )
        return event_id

    @staticmethod
    def append_in_connection(
        connection: Any,
        tenant: TenantContext,
        *,
        event_id: str,
        event_type: str,
        actor_type: str,
        actor_user_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        fencing_token: int | None = None,
        policy_version: str = "v1",
        details: Mapping[str, object] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        for value, name in (
            (event_id, "event_id"),
            (event_type, "event_type"),
            (actor_type, "actor_type"),
            (policy_version, "policy_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        normalized_actor_type = actor_type.strip().upper()
        if normalized_actor_type not in {"USER", "WORKER", "SYSTEM"}:
            raise ValueError("actor_type must be USER, WORKER or SYSTEM")
        if normalized_actor_type == "USER" and actor_user_id != tenant.user_id:
            raise ValueError("user audit actor must match TenantContext.user_id")
        if normalized_actor_type != "USER" and actor_user_id is not None:
            raise ValueError("non-user audit actor cannot set actor_user_id")
        cursor = _cursor(connection)
        try:
            if normalized_actor_type == "WORKER":
                if run_id is None or fencing_token is None:
                    raise ValueError("worker audit events require run_id and fencing_token")
                cursor.execute(
                    """
                    SELECT fencing_token FROM runs
                     WHERE platform_id = %s AND tenant_id = %s AND run_id = %s
                     FOR UPDATE
                    """,
                    (tenant.platform_id, tenant.tenant_id, run_id),
                )
                run_row = cursor.fetchone()
                if run_row is None or int(run_row["fencing_token"]) != fencing_token:
                    raise LeaseLostError("worker audit event has a stale fencing token")
            cursor.execute(
                """
                INSERT INTO audit_events (
                    platform_id, tenant_id, event_id, actor_type, actor_user_id,
                    session_id, run_id, event_type, policy_version,
                    fencing_token, payload_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant.platform_id,
                    tenant.tenant_id,
                    event_id,
                    normalized_actor_type,
                    actor_user_id,
                    session_id,
                    run_id,
                    event_type,
                    policy_version,
                    fencing_token,
                    _safe_audit_details(details),
                    occurred_at or _now(),
                ),
            )
        finally:
            cursor.close()


class MySqlContentGcRepository:
    """Tenant-scoped queue operations for asynchronous content deletion."""

    def __init__(self, database: MySqlDatabase) -> None:
        self.database = database

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")

    @staticmethod
    def _live_root_sql() -> str:
        return """SELECT 1 AS live
            FROM (
                SELECT content_ref AS object_ref FROM messages
                 WHERE platform_id = %s AND tenant_id = %s AND content_ref = %s
                UNION ALL
                SELECT content_ref FROM message_chunks
                 WHERE platform_id = %s AND tenant_id = %s AND content_ref = %s
                UNION ALL
                SELECT result_ref FROM runs
                 WHERE platform_id = %s AND tenant_id = %s AND result_ref = %s
                UNION ALL
                SELECT harness_home_ref FROM sessions
                 WHERE platform_id = %s AND tenant_id = %s AND harness_home_ref = %s
                UNION ALL
                SELECT workspace_ref FROM sessions
                 WHERE platform_id = %s AND tenant_id = %s AND workspace_ref = %s
                UNION ALL
                SELECT object_ref FROM content_upload_intents
                 WHERE platform_id = %s AND tenant_id = %s AND object_ref = %s
                   AND status <> 'EXPIRED'
            ) roots
            LIMIT 1"""

    def enqueue_orphans(
        self,
        tenant: TenantContext,
        store: Any,
        *,
        older_than: datetime,
        limit: int = 100,
    ) -> int:
        self._validate_limit(limit)
        if older_than.tzinfo is None:
            raise ValueError("older_than must be timezone-aware")
        cursor_value: str | None = None
        queued = 0
        while queued < limit:
            page = store.list_candidates(
                tenant,
                older_than,
                cursor=cursor_value,
            )
            if not page.items:
                break
            with self.database.transaction() as connection:
                for info in page.items:
                    if queued >= limit:
                        break
                    ref = str(info.ref)
                    params = []
                    for _ in range(6):
                        params.extend((*tenant.scope, ref))
                    db_cursor = _cursor(connection)
                    try:
                        db_cursor.execute(self._live_root_sql(), tuple(params))
                        if db_cursor.fetchone() is None:
                            _queue_content(connection, tenant, ref, "MESSAGE_CONTENT")
                            queued += 1
                    finally:
                        db_cursor.close()
            if page.next_cursor is None:
                break
            cursor_value = page.next_cursor
        return queued

    def claim(self, tenant: TenantContext, *, limit: int = 100) -> tuple[ContentGcItem, ...]:
        self._validate_limit(limit)
        with self.database.transaction() as connection:
            db_cursor = _cursor(connection)
            try:
                db_cursor.execute(
                    """SELECT cleanup_id, object_ref, object_type, attempt_count
                       FROM content_gc_queue
                       WHERE platform_id = %s AND tenant_id = %s
                         AND status IN ('QUEUED', 'RETRY', 'DELETING')
                         AND next_attempt_at <= CURRENT_TIMESTAMP(6)
                       ORDER BY created_at, cleanup_id
                       LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (*tenant.scope, limit),
                )
                rows = db_cursor.fetchall()
                items: list[ContentGcItem] = []
                for row in rows:
                    db_cursor.execute(
                        """UPDATE content_gc_queue
                           SET status = 'DELETING',
                               next_attempt_at = TIMESTAMPADD(SECOND, 300, CURRENT_TIMESTAMP(6))
                           WHERE platform_id = %s AND tenant_id = %s AND cleanup_id = %s
                             AND status IN ('QUEUED', 'RETRY', 'DELETING')""",
                        (*tenant.scope, row["cleanup_id"]),
                    )
                    if db_cursor.rowcount == 1:
                        items.append(
                            ContentGcItem(
                                cleanup_id=str(row["cleanup_id"]),
                                object_ref=str(row["object_ref"]),
                                object_type=str(row["object_type"]),
                                attempt_count=int(row["attempt_count"]),
                            )
                        )
                return tuple(items)
            finally:
                db_cursor.close()

    def complete(
        self,
        tenant: TenantContext,
        cleanup_id: str,
        *,
        success: bool,
        error_code: str | None = None,
    ) -> None:
        if not cleanup_id.strip():
            raise ValueError("cleanup_id must not be blank")
        if not success and (not error_code or not re.fullmatch(r"[A-Z0-9_.-]{1,128}", error_code)):
            raise ValueError("error_code is required for a failed cleanup")
        with self.database.transaction() as connection:
            db_cursor = _cursor(connection)
            try:
                if success:
                    db_cursor.execute(
                        """UPDATE content_gc_queue
                           SET status = 'DELETED', attempt_count = attempt_count + 1,
                               last_error_code = NULL
                           WHERE platform_id = %s AND tenant_id = %s AND cleanup_id = %s
                             AND status = 'DELETING'""",
                        (*tenant.scope, cleanup_id),
                    )
                else:
                    db_cursor.execute(
                        """UPDATE content_gc_queue
                           SET status = 'RETRY', attempt_count = attempt_count + 1,
                               last_error_code = %s,
                               next_attempt_at = TIMESTAMPADD(
                                   SECOND, LEAST(3600, POW(2, attempt_count)), CURRENT_TIMESTAMP(6)
                               )
                           WHERE platform_id = %s AND tenant_id = %s AND cleanup_id = %s
                             AND status = 'DELETING'""",
                        (error_code, *tenant.scope, cleanup_id),
                    )
            finally:
                db_cursor.close()


class MySqlContentGarbageCollector:
    """Run one retryable content cleanup pass outside the DB transaction."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    @staticmethod
    def _item_fields(item: Any) -> tuple[str, str, str]:
        if isinstance(item, ContentGcItem):
            return item.cleanup_id, item.object_ref, item.object_type
        cleanup_id, object_ref, object_type, *_ = item
        return str(cleanup_id), str(object_ref), str(object_type)

    def enqueue_orphans(
        self,
        tenant: TenantContext,
        store: Any,
        *,
        older_than: datetime | None = None,
        limit: int = 100,
    ) -> int:
        if older_than is None:
            older_than = _now() - timedelta(hours=24)
        if older_than.tzinfo is None:
            raise ValueError("older_than must be timezone-aware")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return self.repository.enqueue_orphans(
            tenant,
            store,
            older_than=older_than,
            limit=limit,
        )

    def run_once(
        self,
        tenant: TenantContext,
        store: Any,
        *,
        older_than: datetime | None = None,
        limit: int = 100,
    ) -> int:
        self.enqueue_orphans(tenant, store, older_than=older_than, limit=limit)
        items = self.repository.claim(tenant, limit=limit)
        for item in items:
            cleanup_id, object_ref, _object_type = self._item_fields(item)
            try:
                store.delete(tenant, ContentRef(object_ref))
            except Exception:  # noqa: BLE001 - cleanup remains retryable and access is already revoked
                self.repository.complete(
                    tenant,
                    cleanup_id,
                    success=False,
                    error_code="OBJECT_DELETE_FAILED",
                )
            else:
                self.repository.complete(tenant, cleanup_id, success=True)
        return len(items)

    def cleanup(
        self,
        tenant: TenantContext,
        store: Any,
        *,
        older_than: datetime | None = None,
        limit: int = 100,
    ) -> int:
        """Compatibility name for one asynchronous cleanup pass."""

        return self.run_once(
            tenant,
            store,
            older_than=older_than,
            limit=limit,
        )


__all__ = [
    "MySqlAuditRepository",
    "MySqlConfig",
    "MySqlContentGarbageCollector",
    "MySqlContentGcRepository",
    "MySqlContentUploadRepository",
    "MySqlDatabase",
    "MySqlMessageRepository",
    "MySqlRunRepository",
    "MySqlSessionRepository",
]
