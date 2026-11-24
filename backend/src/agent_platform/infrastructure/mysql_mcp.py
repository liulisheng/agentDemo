"""MCP Stdio Profile 与一次性 Approval 的租户范围 MySQL 仓储。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.execution import RuntimeReference, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import (
    McpProfileStatus,
    McpServerProfile,
    ToolApprovalRequest,
    ToolApprovalState,
)
from agent_platform.domain.observability import TraceSpan
from agent_platform.ports.mcp import McpRepository


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError("数据库 JSON 值类型不受支持")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


_PROFILE_SELECT = (
    "SELECT platform_id, tenant_id, profile_id, version, name, transport, command, "
    "args_json, working_directory_ref, environment_secret_refs_json, "
    "enabled_tool_names_json, tool_catalog_sha256, startup_timeout_ms, "
    "tool_timeout_ms, status FROM mcp_server_profiles"
)

_APPROVAL_SELECT = (
    "SELECT platform_id, tenant_id, approval_id, run_id, session_id, conversation_id, "
    "requested_by, tool_invocation_id, tool_id, tool_version, mcp_server_profile_id, "
    "mcp_server_profile_version, arguments_ref, arguments_sha256, preview_ref, preview_sha256, "
    "confirmation_idempotency_key, state, expires_at, worker_id, fencing_token, "
    "lease_expires_at, approved_by, rejected_by, approved_at, consumed_at, finished_at, error_code "
    "FROM tool_approval_requests"
)


class MySqlMcpRepository(McpRepository):
    """以复合租户键读写 Profile，并以行锁驱动一次性审批动作。"""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            yield connection, cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield connection, cursor
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    @staticmethod
    def _require_scope(
        identity: IdentityContext, platform_id: str, tenant_id: str
    ) -> None:
        if identity.platform_id != platform_id or identity.tenant_id != tenant_id:
            raise DomainError("AUTHORIZATION_DENIED", "资源不属于当前租户")

    @staticmethod
    def _require_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以管理 MCP Profile")

    @staticmethod
    def _require_worker(identity: IdentityContext) -> None:
        if identity.auth_source != "worker":
            raise DomainError("AUTHORIZATION_DENIED", "该 MCP 操作只允许 Worker 执行")

    @staticmethod
    def _database_now(cursor: Any) -> datetime:
        """用数据库时钟判定租约和审批有效期，避免 API/Worker 时钟偏差。"""

        cursor.execute("SELECT CURRENT_TIMESTAMP(6)")
        value = _utc(cursor.fetchone()[0])
        if value is None:
            raise RuntimeError("数据库未返回当前时间")
        return value

    @staticmethod
    def _profile_from_row(row: tuple[Any, ...]) -> McpServerProfile:
        args = _json_load(row[7])
        environment_refs = _json_load(row[9])
        tool_names = _json_load(row[10])
        if not isinstance(args, list) or not isinstance(environment_refs, dict) or not isinstance(tool_names, list):
            raise TypeError("MCP Profile JSON 字段格式非法")
        return McpServerProfile(
            platform_id=str(row[0]),
            tenant_id=str(row[1]),
            profile_id=str(row[2]),
            version=int(row[3]),
            name=str(row[4]),
            transport=str(row[5]),
            command=str(row[6]),
            args=tuple(str(value) for value in args),
            working_directory_ref=str(row[8]),
            environment_secret_refs={
                str(key): str(value) for key, value in environment_refs.items()
            },
            enabled_tool_names=tuple(str(value) for value in tool_names),
            tool_catalog_sha256=str(row[11]),
            startup_timeout_ms=int(row[12]),
            tool_timeout_ms=int(row[13]),
            status=McpProfileStatus(str(row[14])),
        )

    @staticmethod
    def _approval_from_row(row: tuple[Any, ...]) -> ToolApprovalRequest:
        expires_at = _utc(row[18])
        if expires_at is None:
            raise TypeError("ToolApprovalRequest expires_at must not be NULL")
        return ToolApprovalRequest(
            platform_id=str(row[0]),
            tenant_id=str(row[1]),
            approval_id=str(row[2]),
            run_id=str(row[3]),
            session_id=str(row[4]),
            conversation_id=str(row[5]),
            requested_by=str(row[6]),
            tool_invocation_id=str(row[7]),
            tool_id=str(row[8]),
            tool_version=int(row[9]),
            mcp_server_profile_id=str(row[10]),
            mcp_server_profile_version=int(row[11]),
            arguments_ref=str(row[12]),
            arguments_sha256=str(row[13]),
            preview_ref=str(row[14]),
            preview_sha256=str(row[15]),
            confirmation_idempotency_key=str(row[16]) if row[16] is not None else None,
            state=ToolApprovalState(str(row[17])),
            expires_at=expires_at,
            worker_id=str(row[19]) if row[19] is not None else None,
            fencing_token=int(row[20]),
            lease_expires_at=_utc(row[21]),
            approved_by=str(row[22]) if row[22] is not None else None,
            rejected_by=str(row[23]) if row[23] is not None else None,
            approved_at=_utc(row[24]),
            consumed_at=_utc(row[25]),
            finished_at=_utc(row[26]),
            error_code=str(row[27]) if row[27] is not None else None,
        )

    def save_profile(self, identity: IdentityContext, profile: McpServerProfile) -> None:
        """只创建不可变 Profile 草稿，拒绝覆盖已存在版本。"""

        self._require_admin(identity)
        self._require_scope(identity, profile.platform_id, profile.tenant_id)
        if profile.status is not McpProfileStatus.DRAFT:
            raise DomainError("INVALID_STATE", "新 MCP Profile 必须先以 DRAFT 保存")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT status FROM mcp_server_profiles "
                "WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, profile.profile_id, profile.version),
            )
            if cursor.fetchone() is not None:
                raise DomainError("IMMUTABLE_VERSION", "MCP Profile 版本已存在且不可覆盖")
            cursor.execute(
                "INSERT INTO mcp_server_profiles "
                "(platform_id, tenant_id, profile_id, version, name, transport, command, args_json, "
                "working_directory_ref, environment_secret_refs_json, enabled_tool_names_json, "
                "tool_catalog_sha256, startup_timeout_ms, tool_timeout_ms, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT')",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    profile.profile_id,
                    profile.version,
                    profile.name,
                    profile.transport,
                    profile.command,
                    _json_dump(list(profile.args)),
                    profile.working_directory_ref,
                    _json_dump(dict(profile.environment_secret_refs)),
                    _json_dump(list(profile.enabled_tool_names)),
                    profile.tool_catalog_sha256,
                    profile.startup_timeout_ms,
                    profile.tool_timeout_ms,
                ),
            )

    def set_profile_status(
        self,
        identity: IdentityContext,
        profile_id: str,
        version: int,
        status: McpProfileStatus,
    ) -> McpServerProfile:
        """显式执行 DRAFT→ACTIVE→DISABLED 生命周期迁移。"""

        self._require_admin(identity)
        target = McpProfileStatus(status)
        with self._transaction() as (_, cursor):
            cursor.execute(
                _PROFILE_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, profile_id, version),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("mcp_server_profile")
            current = McpProfileStatus(str(row[14]))
            if current is target:
                return self._profile_from_row(row)
            allowed = {
                McpProfileStatus.DRAFT: {McpProfileStatus.ACTIVE, McpProfileStatus.DISABLED},
                McpProfileStatus.ACTIVE: {McpProfileStatus.DISABLED},
                McpProfileStatus.DISABLED: set(),
            }
            if target not in allowed[current]:
                raise DomainError("INVALID_STATE", "MCP Profile 状态迁移不合法")
            cursor.execute(
                "UPDATE mcp_server_profiles SET status=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s",
                (target.value, identity.platform_id, identity.tenant_id, profile_id, version),
            )
            updated = list(row)
            updated[14] = target.value
            return self._profile_from_row(tuple(updated))

    def get_profile(
        self, identity: IdentityContext, profile_id: str, version: int
    ) -> McpServerProfile:
        with self._reader() as (_, cursor):
            cursor.execute(
                _PROFILE_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND profile_id=%s AND version=%s",
                (identity.platform_id, identity.tenant_id, profile_id, version),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("mcp_server_profile")
        return self._profile_from_row(row)

    def list_profiles(self, identity: IdentityContext) -> tuple[McpServerProfile, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                _PROFILE_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s ORDER BY name, profile_id, version DESC",
                (identity.platform_id, identity.tenant_id),
            )
            rows = cursor.fetchall()
        return tuple(self._profile_from_row(row) for row in rows)

    def create_tool_approval(
        self,
        identity: IdentityContext,
        request: ToolApprovalRequest,
        *,
        run_fencing_token: int,
        trace_spans: tuple[TraceSpan, ...] = (),
        usage: UsageRecord | None = None,
        runtime_reference: RuntimeReference | None = None,
        result_ref: str | None = None,
    ) -> ToolApprovalRequest:
        """同事务保存 Run 轨迹和用量、创建审批并释放已结束的 Session Home lease。"""

        self._require_worker(identity)
        self._require_scope(identity, request.platform_id, request.tenant_id)
        if request.state is not ToolApprovalState.PENDING:
            raise DomainError("INVALID_STATE", "新审批请求必须处于 PENDING")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT state, fencing_token, lease_expires_at, submitted_by_user_id, "
                "employee_id, model_route_id, trace_id "
                "FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, request.run_id),
            )
            run_row = cursor.fetchone()
            if run_row is None:
                raise self._not_found("run")
            run_lease = _utc(run_row[2])
            now = self._database_now(cursor)
            if request.expires_at <= now:
                raise DomainError("APPROVAL_EXPIRED", "审批有效期必须晚于创建时间")
            if (
                str(run_row[0]) != "RUNNING"
                or int(run_row[1]) != run_fencing_token
                or run_lease is None
                or now >= run_lease
            ):
                raise DomainError("STALE_FENCING_TOKEN", "Run Worker 租约已失效")
            if run_row[3] is None or str(run_row[3]) != request.requested_by:
                raise DomainError("AUTHORIZATION_DENIED", "Approval requester 与 Run 提交者不一致")
            for span in sorted(trace_spans, key=lambda item: item.sequence):
                if span.run_id != request.run_id or span.trace_id != str(run_row[6]):
                    raise DomainError("INVALID_ARGUMENT", "审批轨迹 Span 不属于当前 Run")
                cursor.execute(
                    "INSERT INTO platform_trace_spans "
                    "(platform_id, tenant_id, run_id, span_id, trace_id, parent_span_id, span_type, status, "
                    "sequence_no, attributes_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        span.run_id,
                        span.span_id,
                        span.trace_id,
                        span.parent_span_id,
                        span.span_type.value,
                        span.status,
                        span.sequence,
                        _json_dump(dict(span.attributes)),
                    ),
                )
            cursor.execute(
                "INSERT INTO tool_approval_requests "
                "(platform_id, tenant_id, approval_id, run_id, session_id, conversation_id, requested_by, "
                "tool_invocation_id, tool_id, tool_version, mcp_server_profile_id, mcp_server_profile_version, "
                "arguments_ref, arguments_sha256, preview_ref, preview_sha256, state, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)",
                (
                    request.platform_id,
                    request.tenant_id,
                    request.approval_id,
                    request.run_id,
                    request.session_id,
                    request.conversation_id,
                    request.requested_by,
                    request.tool_invocation_id,
                    request.tool_id,
                    request.tool_version,
                    request.mcp_server_profile_id,
                    request.mcp_server_profile_version,
                    request.arguments_ref,
                    request.arguments_sha256,
                    request.preview_ref,
                    request.preview_sha256,
                    request.expires_at,
                ),
            )
            cursor.execute(
                "UPDATE runs SET state='AWAITING_APPROVAL', worker_id=NULL, lease_expires_at=NULL, "
                "result_ref=COALESCE(%s,result_ref), runtime_session_ref=COALESCE(%s,runtime_session_ref), "
                "runtime_result_ref=COALESCE(%s,runtime_result_ref) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                (
                    result_ref,
                    runtime_reference.runtime_session_ref if runtime_reference else None,
                    runtime_reference.runtime_result_ref if runtime_reference else None,
                    identity.platform_id,
                    identity.tenant_id,
                    request.run_id,
                    run_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise DomainError("STALE_FENCING_TOKEN", "Run 状态已变化，不能创建审批")
            if runtime_reference is not None:
                cursor.execute(
                    "UPDATE sessions SET engine_session_id=%s, runtime_result_ref=%s "
                    "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                    (
                        runtime_reference.runtime_session_ref,
                        runtime_reference.runtime_result_ref,
                        identity.platform_id,
                        identity.tenant_id,
                        request.session_id,
                    ),
                )
            if usage is not None:
                cursor.execute(
                    "INSERT INTO usage_ledger "
                    "(platform_id, tenant_id, call_id, run_id, session_id, user_id, employee_id, model_route_id, "
                    "input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, total_tokens, cost_amount, currency, pricing_version, "
                    "usage_status, estimation_method) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        f"model-{request.run_id}",
                        request.run_id,
                        request.session_id,
                        request.requested_by,
                        str(run_row[4]),
                        str(run_row[5]) if run_row[5] is not None else None,
                        usage.input_tokens,
                        usage.output_tokens,
                        0,
                        usage.reasoning_tokens,
                        usage.total_tokens,
                        usage.cost_amount,
                        usage.currency,
                        usage.pricing_version,
                        "ESTIMATED" if usage.estimated else "EXACT",
                        usage.source if usage.estimated else None,
                    ),
                )
            if trace_spans:
                cursor.execute(
                    "DELETE FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s "
                    "AND session_id=%s AND run_id=%s AND fencing_token=%s AND state='ACTIVE'",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        request.session_id,
                        request.run_id,
                        run_fencing_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DomainError("STALE_FENCING_TOKEN", "审批 Run 缺少有效 Session Home lease")
            self._set_invocation_status(
                cursor, request, "awaiting_approval", finished_at=None, error_code=None
            )
            self._touch_conversation(cursor, request)
        return request

    def get_tool_approval(
        self, identity: IdentityContext, approval_id: str
    ) -> ToolApprovalRequest:
        with self._reader() as (_, cursor):
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s",
                (identity.platform_id, identity.tenant_id, approval_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("tool_approval")
        request = self._approval_from_row(row)
        if identity.auth_source != "worker" and identity.user_id != request.requested_by:
            raise self._not_found("tool_approval")
        return request

    def approve_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        preview_sha256: str,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> ToolApprovalRequest:
        with self._transaction() as (_, cursor):
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, approval_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("tool_approval")
            request = self._approval_from_row(row)
            now = self._database_now(cursor)
            approved = request.approve(
                identity, preview_sha256, idempotency_key=idempotency_key, now=now
            )
            if approved is request:
                return request
            cursor.execute(
                "UPDATE tool_approval_requests SET state='APPROVED', approved_by=%s, approved_at=%s, "
                "confirmation_idempotency_key=%s WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s "
                "AND state='PENDING'",
                (
                    approved.approved_by,
                    approved.approved_at,
                    approved.confirmation_idempotency_key,
                    identity.platform_id,
                    identity.tenant_id,
                    approval_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "审批状态已被其他请求修改")
            self._set_invocation_status(
                cursor, approved, "approved", finished_at=None, error_code=None
            )
            self._append_user_audit(cursor, identity, approved, "TOOL_APPROVAL_APPROVED", now)
            self._touch_conversation(cursor, approved)
            return approved

    def reject_tool_approval(
        self, identity: IdentityContext, approval_id: str, *, now: datetime
    ) -> ToolApprovalRequest:
        with self._transaction() as (_, cursor):
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, approval_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("tool_approval")
            request = self._approval_from_row(row)
            now = self._database_now(cursor)
            rejected = request.reject(identity, now=now)
            if rejected is request:
                return request
            cursor.execute(
                "UPDATE tool_approval_requests SET state='REJECTED', rejected_by=%s, finished_at=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s AND state='PENDING'",
                (rejected.rejected_by, now, identity.platform_id, identity.tenant_id, approval_id),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "审批状态已被其他请求修改")
            self._close_run_for_approval(cursor, rejected, "CANCELLED", "APPROVAL_REJECTED", now)
            self._set_invocation_status(
                cursor,
                rejected,
                "rejected",
                finished_at=now,
                error_code="APPROVAL_REJECTED",
            )
            self._append_user_audit(cursor, identity, rejected, "TOOL_APPROVAL_REJECTED", now)
            self._touch_conversation(cursor, rejected)
            return rejected

    def expire_tool_approval(
        self, identity: IdentityContext, approval_id: str, *, now: datetime
    ) -> ToolApprovalRequest:
        self._require_worker(identity)
        with self._transaction() as (_, cursor):
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, approval_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("tool_approval")
            request = self._approval_from_row(row)
            now = self._database_now(cursor)
            expired = request.expire(now=now)
            cursor.execute(
                "UPDATE tool_approval_requests SET state='EXPIRED', finished_at=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s AND state='PENDING'",
                (now, identity.platform_id, identity.tenant_id, approval_id),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "审批状态已被其他请求修改")
            self._close_run_for_approval(cursor, expired, "CANCELLED", "APPROVAL_EXPIRED", now)
            self._set_invocation_status(
                cursor,
                expired,
                "expired",
                finished_at=now,
                error_code="APPROVAL_EXPIRED",
            )
            self._append_worker_audit(cursor, identity, expired, "TOOL_APPROVAL_EXPIRED", None)
            self._touch_conversation(cursor, expired)
            return expired

    def claim_approved_tool_approval(
        self, identity: IdentityContext, worker_id: str, *, lease_seconds: int = 30
    ) -> ToolApprovalRequest | None:
        self._require_worker(identity)
        with self._transaction() as (_, cursor):
            now = self._database_now(cursor)
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND state='RUNNING' "
                "AND lease_expires_at<=CURRENT_TIMESTAMP(6) "
                "ORDER BY lease_expires_at, approval_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (identity.platform_id, identity.tenant_id),
            )
            expired_row = cursor.fetchone()
            if expired_row is not None:
                expired = self._approval_from_row(expired_row)
                recovered = replace(
                    expired,
                    state=ToolApprovalState.UNKNOWN,
                    worker_id=None,
                    fencing_token=expired.fencing_token + 1,
                    lease_expires_at=None,
                    finished_at=now,
                    error_code="WORKER_LEASE_EXPIRED",
                )
                cursor.execute(
                    "UPDATE tool_approval_requests SET state='UNKNOWN', worker_id=NULL, "
                    "fencing_token=fencing_token+1, lease_expires_at=NULL, finished_at=%s, "
                    "error_code='WORKER_LEASE_EXPIRED' WHERE platform_id=%s AND tenant_id=%s "
                    "AND approval_id=%s AND state='RUNNING' AND fencing_token=%s "
                    "AND lease_expires_at<=CURRENT_TIMESTAMP(6)",
                    (
                        now,
                        identity.platform_id,
                        identity.tenant_id,
                        expired.approval_id,
                        expired.fencing_token,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                cursor.execute(
                    "UPDATE runs SET state='UNKNOWN', error_code='APPROVAL_LEASE_EXPIRED', "
                    "worker_id=NULL, lease_expires_at=NULL, finished_at=CURRENT_TIMESTAMP(6) "
                    "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                    "AND state='AWAITING_APPROVAL'",
                    (identity.platform_id, identity.tenant_id, recovered.run_id),
                )
                self._set_invocation_status(
                    cursor,
                    recovered,
                    "unknown",
                    finished_at=now,
                    error_code="WORKER_LEASE_EXPIRED",
                )
                self._append_worker_audit(
                    cursor,
                    identity,
                    recovered,
                    "TOOL_APPROVAL_ACTION_UNKNOWN",
                    recovered.fencing_token,
                )
                self._touch_conversation(cursor, recovered)
                return None

            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND state='APPROVED' "
                "ORDER BY created_at, approval_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (identity.platform_id, identity.tenant_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            request = self._approval_from_row(row)
            claimed = request.claim(worker_id, now=now, lease_seconds=lease_seconds)
            cursor.execute(
                "UPDATE tool_approval_requests SET state='RUNNING', worker_id=%s, fencing_token=%s, "
                "lease_expires_at=%s, consumed_at=%s WHERE platform_id=%s AND tenant_id=%s "
                "AND approval_id=%s AND state='APPROVED'",
                (
                    claimed.worker_id,
                    claimed.fencing_token,
                    claimed.lease_expires_at,
                    claimed.consumed_at,
                    identity.platform_id,
                    identity.tenant_id,
                    claimed.approval_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._set_invocation_status(
                cursor, claimed, "running", finished_at=None, error_code=None
            )
            self._touch_conversation(cursor, claimed)
            return claimed

    def heartbeat_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        worker_id: str,
        fencing_token: int,
        *,
        lease_seconds: int = 30,
    ) -> bool:
        """使用数据库时钟续租；过期或旧 fencing token 影响零行。"""

        self._require_worker(identity)
        if lease_seconds <= 0:
            raise DomainError("INVALID_ARGUMENT", "lease_seconds 必须为正数")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE tool_approval_requests SET lease_expires_at="
                "TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s AND state='RUNNING' "
                "AND worker_id=%s AND fencing_token=%s AND lease_expires_at>CURRENT_TIMESTAMP(6)",
                (
                    lease_seconds,
                    identity.platform_id,
                    identity.tenant_id,
                    approval_id,
                    worker_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def finish_tool_approval(
        self,
        identity: IdentityContext,
        approval_id: str,
        worker_id: str,
        fencing_token: int,
        state: ToolApprovalState,
        *,
        now: datetime,
        error_code: str | None = None,
        result_ref: str | None = None,
        result_sha256: str | None = None,
    ) -> bool:
        self._require_worker(identity)
        with self._transaction() as (_, cursor):
            cursor.execute(
                _APPROVAL_SELECT
                + " WHERE platform_id=%s AND tenant_id=%s AND approval_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, approval_id),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            request = self._approval_from_row(row)
            now = self._database_now(cursor)
            completed = request.finish(
                worker_id,
                fencing_token=fencing_token,
                state=state,
                now=now,
                error_code=error_code,
            )
            cursor.execute(
                "UPDATE tool_approval_requests SET state=%s, finished_at=%s, error_code=%s, "
                "worker_id=NULL, lease_expires_at=NULL WHERE platform_id=%s AND tenant_id=%s "
                "AND approval_id=%s AND state='RUNNING' AND worker_id=%s AND fencing_token=%s "
                "AND lease_expires_at>CURRENT_TIMESTAMP(6)",
                (
                    completed.state.value,
                    completed.finished_at,
                    completed.error_code,
                    identity.platform_id,
                    identity.tenant_id,
                    approval_id,
                    worker_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                "UPDATE runs SET state=%s, error_code=%s, result_ref=COALESCE(%s,result_ref), "
                "worker_id=NULL, lease_expires_at=NULL, "
                "finished_at=CURRENT_TIMESTAMP(6) WHERE platform_id=%s AND tenant_id=%s "
                "AND run_id=%s AND state='AWAITING_APPROVAL'",
                (
                    completed.state.value,
                    completed.error_code,
                    result_ref,
                    identity.platform_id,
                    identity.tenant_id,
                    completed.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise DomainError("INVALID_STATE", "Run 已离开 AWAITING_APPROVAL 状态")
            self._set_invocation_status(
                cursor,
                completed,
                completed.state.value.lower(),
                finished_at=completed.finished_at,
                error_code=completed.error_code,
                tool_result_ref=result_ref,
                tool_result_sha256=result_sha256,
            )
            self._append_worker_audit(
                cursor,
                identity,
                completed,
                "TOOL_APPROVAL_ACTION_" + completed.state.value,
                fencing_token,
                result_ref=result_ref,
                result_sha256=result_sha256,
            )
            self._touch_conversation(cursor, completed)
            return True

    @staticmethod
    def _touch_conversation(cursor: Any, request: ToolApprovalRequest) -> None:
        """与 Approval/Run 状态变化共用事务，推进工作台列表排序时间。"""

        cursor.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP(6) "
            "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
            (request.platform_id, request.tenant_id, request.conversation_id),
        )

    @staticmethod
    def _set_invocation_status(
        cursor: Any,
        request: ToolApprovalRequest,
        status: str,
        *,
        finished_at: datetime | None,
        error_code: str | None,
        tool_result_ref: str | None = None,
        tool_result_sha256: str | None = None,
    ) -> None:
        """同步更新获批工具的 Trace Span，只追加摘要和引用。"""

        attributes_json = (
            "JSON_SET(attributes_json, '$.approval_ref', %s, '$.approval_state', %s, "
            "'$.tool_result_ref', %s, '$.tool_result_sha256', %s)"
            if tool_result_ref is not None
            else "JSON_SET(attributes_json, '$.approval_ref', %s, '$.approval_state', %s)"
        )
        parameters = (
            status,
            finished_at,
            error_code,
            request.approval_id,
            request.state.value,
            *((tool_result_ref, tool_result_sha256) if tool_result_ref is not None else ()),
            request.platform_id,
            request.tenant_id,
            request.run_id,
            request.tool_invocation_id,
        )
        cursor.execute(
            "UPDATE platform_trace_spans SET status=%s, finished_at=%s, error_category=%s, "
            f"attributes_json={attributes_json} "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND span_id=%s",
            parameters,
        )
        if cursor.rowcount != 1:
            raise DomainError("RESOURCE_NOT_FOUND", "审批关联的 Tool Trace Span 不存在")

    @staticmethod
    def _close_run_for_approval(
        cursor: Any,
        request: ToolApprovalRequest,
        run_state: str,
        error_code: str,
        now: datetime,
    ) -> None:
        cursor.execute(
            "UPDATE runs SET state=%s, error_code=%s, finished_at=%s "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='AWAITING_APPROVAL'",
            (
                run_state,
                error_code,
                now,
                request.platform_id,
                request.tenant_id,
                request.run_id,
            ),
        )
        if cursor.rowcount != 1:
            raise DomainError("INVALID_STATE", "Run 已离开 AWAITING_APPROVAL 状态")

    @staticmethod
    def _append_user_audit(
        cursor: Any,
        identity: IdentityContext,
        request: ToolApprovalRequest,
        event_type: str,
        now: datetime,
    ) -> None:
        cursor.execute(
            "INSERT INTO audit_events "
            "(platform_id, tenant_id, event_id, actor_type, actor_user_id, session_id, run_id, "
            "event_type, policy_version, resource_type, resource_id, payload_json, created_at) "
            "VALUES (%s,%s,%s,'USER',%s,%s,%s,%s,'mcp-approval-v1',"
            "'tool_approval_request',%s,%s,%s)",
            (
                request.platform_id,
                request.tenant_id,
                generate_database_id(),
                identity.user_id,
                request.session_id,
                request.run_id,
                event_type,
                request.approval_id,
                _json_dump(
                    {
                        "approval_id": request.approval_id,
                        "preview_sha256": request.preview_sha256,
                        "tool_id": request.tool_id,
                        "tool_version": request.tool_version,
                    }
                ),
                now,
            ),
        )

    @staticmethod
    def _append_worker_audit(
        cursor: Any,
        identity: IdentityContext,
        request: ToolApprovalRequest,
        event_type: str,
        fencing_token: int | None,
        *,
        result_ref: str | None = None,
        result_sha256: str | None = None,
    ) -> None:
        cursor.execute(
            "INSERT INTO audit_events "
            "(platform_id, tenant_id, event_id, actor_type, actor_user_id, session_id, run_id, "
            "event_type, policy_version, resource_type, resource_id, fencing_token, payload_json) "
            "VALUES (%s,%s,%s,'WORKER',NULL,%s,%s,%s,'mcp-approval-v1',"
            "'tool_approval_request',%s,%s,%s)",
            (
                identity.platform_id,
                identity.tenant_id,
                generate_database_id(),
                request.session_id,
                request.run_id,
                event_type,
                request.approval_id,
                fencing_token,
                _json_dump(
                    {
                        "approval_id": request.approval_id,
                        "tool_id": request.tool_id,
                        "tool_version": request.tool_version,
                        "state": request.state.value,
                        "error_code": request.error_code,
                        "result_ref": result_ref,
                        "result_sha256": result_sha256,
                    }
                ),
            ),
        )
