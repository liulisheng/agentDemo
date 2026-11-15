"""复用 Harness Session/Run 表持久化平台执行快照和 opaque Runtime 引用。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError
from agent_platform.domain.context import ContextSnapshot, ContextSource, ContextSourceType
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import (
    HubResourceSnapshot,
    RunRecord,
    RunSnapshot,
    RunTimeline,
    SessionRecord,
    SkillFilePackageSnapshot,
    SkillFileSnapshotEntry,
    SpanType,
    TraceSpan,
)
from agent_platform.domain.usage import (
    ModelCallRecord,
    ToolInvocationRecord,
    UsageAdjustment,
    UsageStatus,
)


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


def _snapshot_data(snapshot: RunSnapshot) -> dict[str, object]:
    return {
        "employee_id": snapshot.employee_id,
        "employee_version": snapshot.employee_version,
        "skill_version_ids": list(snapshot.skill_version_ids),
        "tool_version_ids": list(snapshot.tool_version_ids),
        "skill_version_refs": [list(ref) for ref in snapshot.skill_version_refs],
        "tool_version_refs": [list(ref) for ref in snapshot.tool_version_refs],
        "execution_profile_id": snapshot.execution_profile_id,
        "execution_profile_version": snapshot.execution_profile_version,
        "model_route_id": snapshot.model_route_id,
        "hub_resource_refs": [
            {
                "local_resource_type": item.local_resource_type,
                "local_resource_id": item.local_resource_id,
                "local_resource_version": item.local_resource_version,
                "hub_resource_id": item.hub_resource_id,
                "hub_version": item.hub_version,
                "manifest_sha256": item.manifest_sha256,
                "mode": item.mode,
            }
            for item in snapshot.hub_resource_refs
        ],
        "skill_file_packages": [
            {
                "skill_id": package.skill_id,
                "skill_version": package.skill_version,
                "entry_path": package.entry_path,
                "manifest_sha256": package.manifest_sha256,
                "files": [
                    {
                        "file_path": item.file_path,
                        "media_asset_id": item.media_asset_id,
                        "mime_type": item.mime_type,
                        "size_bytes": item.size_bytes,
                        "content_sha256": item.content_sha256,
                        "asset_platform_id": item.asset_platform_id,
                        "asset_tenant_id": item.asset_tenant_id,
                        "source_mode": item.source_mode,
                    }
                    for item in package.files
                ],
            }
            for package in snapshot.skill_file_packages
        ],
    }


def _context_data(snapshot: ContextSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "snapshot_id": snapshot.snapshot_id,
        "run_id": snapshot.run_id,
        "trace_id": snapshot.trace_id,
        "sources": [
            {
                "source_type": source.source_type.value,
                "reference": source.reference,
                "token_estimate": source.token_estimate,
                "content_hash": source.content_hash,
                "included": source.included,
                "reason": source.reason,
            }
            for source in snapshot.sources
        ],
        "total_tokens": snapshot.total_tokens,
        "excluded_sources": [
            {
                "source_type": source.source_type.value,
                "reference": source.reference,
                "token_estimate": source.token_estimate,
                "content_hash": source.content_hash,
                "included": source.included,
                "reason": source.reason,
            }
            for source in snapshot.excluded_sources
        ],
        "runtime_handoff": dict(snapshot.runtime_handoff),
    }


class MySqlExecutionRepository:
    """对 V001/V002 Session/Run 主表执行租户范围读写，不另建平行生命周期表。"""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._failure_injector = failure_injector

    def _inject_failure(self, stage: str) -> None:
        """测试故障注入只在显式传入时启用，不受应用环境配置控制。"""

        if self._failure_injector is not None:
            self._failure_injector(stage)

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
    def _not_found(resource: str) -> Exception:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    def save_session(
        self, identity: IdentityContext, session: SessionRecord, *, engine_name: str
    ) -> None:
        """持久化 Session 创建时固定的目录版本；Runtime ID 暂用平台 Session ID 占位。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO sessions "
                "(platform_id, tenant_id, session_id, user_id, engine_name, engine_session_id, "
                "harness_home_ref, workspace_ref, status, metadata_json, deployment_id, employee_id, "
                "employee_version, execution_profile_id, execution_profile_version, model_route_id, "
                "trace_id, composition_snapshot_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE status=VALUES(status)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    session.session_id,
                    identity.user_id,
                    engine_name,
                    session.runtime_session_ref or session.session_id,
                    session.status,
                    _json_dump({"trace_id": session.trace_id, "session_span_id": session.span_id}),
                    session.deployment_id,
                    session.employee_id,
                    session.employee_version,
                    session.execution_profile_id,
                    session.execution_profile_version,
                    session.model_route_id,
                    session.trace_id,
                    _json_dump(
                        {
                            "employee_id": session.employee_id,
                            "employee_version": session.employee_version,
                            "execution_profile_id": session.execution_profile_id,
                            "execution_profile_version": session.execution_profile_version,
                            "model_route_id": session.model_route_id,
                        }
                    ),
                ),
            )

    def save_session_runtime_refs(
        self,
        identity: IdentityContext,
        session_id: str,
        *,
        runtime_session_ref: str,
        runtime_result_ref: str | None,
    ) -> None:
        """只更新本租户 Session 的 opaque Runtime 引用，不能由客户端提供路径。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE sessions SET engine_session_id=%s, runtime_result_ref=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (
                    runtime_session_ref,
                    runtime_result_ref,
                    identity.platform_id,
                    identity.tenant_id,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                raise self._not_found("session")

    def get_session(self, identity: IdentityContext, session_id: str) -> SessionRecord:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT session_id, platform_id, tenant_id, deployment_id, employee_id, employee_version, "
                "execution_profile_id, execution_profile_version, model_route_id, trace_id, engine_session_id, status "
                "FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (identity.platform_id, identity.tenant_id, session_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("session")
        runtime_ref = str(row[10]) if str(row[10]) != str(row[0]) else None
        return SessionRecord(
            session_id=str(row[0]),
            platform_id=str(row[1]),
            tenant_id=str(row[2]),
            deployment_id=str(row[3]),
            trace_id=str(row[9] or ""),
            employee_version=int(row[5]),
            employee_id=str(row[4]),
            execution_profile_id=str(row[6]),
            execution_profile_version=int(row[7]),
            model_route_id=str(row[8]) if row[8] is not None else None,
            runtime_session_ref=runtime_ref,
            status=str(row[11]),
        )

    def save_run(self, identity: IdentityContext, run: RunRecord, idempotency_key: str) -> None:
        """保存完整 Run 快照和 Runtime 引用；提交只允许同一幂等记录幂等更新。"""

        if not idempotency_key.strip():
            raise ValueError("idempotency_key 不能为空")
        runtime = run.runtime_reference
        snapshot = run.snapshot
        state = run.status.value.upper()
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO runs "
                "(platform_id, tenant_id, run_id, session_id, idempotency_key, request_digest, state, "
                "error_code, result_ref, employee_id, employee_version, execution_profile_id, "
                "execution_profile_version, model_route_id, trace_id, snapshot_json, context_snapshot_json, "
                "runtime_session_ref, runtime_result_ref, finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP(6)) "
                "ON DUPLICATE KEY UPDATE state=VALUES(state), error_code=VALUES(error_code), "
                "result_ref=VALUES(result_ref), snapshot_json=VALUES(snapshot_json), "
                "context_snapshot_json=VALUES(context_snapshot_json), runtime_session_ref=VALUES(runtime_session_ref), "
                "runtime_result_ref=VALUES(runtime_result_ref), finished_at=VALUES(finished_at)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    run.session_id,
                    idempotency_key,
                    run.prompt_digest,
                    state,
                    run.error_category,
                    run.result_ref,
                    snapshot.employee_id,
                    snapshot.employee_version,
                    snapshot.execution_profile_id,
                    snapshot.execution_profile_version,
                    snapshot.model_route_id,
                    run.trace_id,
                    _json_dump(_snapshot_data(snapshot)),
                    _json_dump(_context_data(run.context_snapshot)),
                    runtime.runtime_session_ref if runtime else None,
                    runtime.runtime_result_ref if runtime else None,
                ),
            )

    def submit_run(
        self, identity: IdentityContext, run: RunRecord, idempotency_key: str
    ) -> tuple[RunRecord, bool]:
        """先按租户、Session 和幂等键提交 Run 快照，重复请求不创建新记录。"""

        if not idempotency_key.strip():
            raise ValueError("idempotency_key 不能为空")
        snapshot = run.snapshot
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO runs "
                "(platform_id, tenant_id, run_id, session_id, idempotency_key, request_digest, state, "
                "employee_id, employee_version, execution_profile_id, execution_profile_version, model_route_id, "
                "trace_id, snapshot_json, context_snapshot_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE run_id=run_id",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    run.session_id,
                    idempotency_key,
                    run.prompt_digest,
                    snapshot.employee_id,
                    snapshot.employee_version,
                    snapshot.execution_profile_id,
                    snapshot.execution_profile_version,
                    snapshot.model_route_id,
                    run.trace_id,
                    _json_dump(_snapshot_data(snapshot)),
                    _json_dump(_context_data(run.context_snapshot)),
                ),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                "SELECT run_id, request_digest FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s AND idempotency_key=%s",
                (identity.platform_id, identity.tenant_id, run.session_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise DomainError("CONFLICT", "Run 标识与现有记录冲突")
            if str(existing[1]) != run.prompt_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
            existing_run_id = str(existing[0])
        return self.get_run(identity, existing_run_id), created

    def acquire_lease(
        self, identity: IdentityContext, run_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> int | None:
        """只领取 QUEUED Run；过期 RUNNING 标记 UNKNOWN，避免不确定执行被自动重放。"""

        if lease_seconds <= 0 or not worker_id.strip():
            raise ValueError("lease_seconds 和 worker_id 必须有效")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT state, fencing_token, lease_expires_at FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("run")
            state, fencing_token, lease_expires_at = row
            if str(state) == "RUNNING" and lease_expires_at is not None:
                cursor.execute("SELECT %s <= CURRENT_TIMESTAMP(6)", (lease_expires_at,))
                if bool(cursor.fetchone()[0]):
                    cursor.execute(
                        "UPDATE runs SET state='UNKNOWN', worker_id=NULL, lease_expires_at=NULL, "
                        "fencing_token=fencing_token+1, finished_at=CURRENT_TIMESTAMP(6) "
                        "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                        (identity.platform_id, identity.tenant_id, run_id, int(fencing_token)),
                    )
                return None
            if str(state) != "QUEUED":
                return None
            token = int(fencing_token) + 1
            cursor.execute(
                "UPDATE runs SET state='RUNNING', worker_id=%s, fencing_token=%s, "
                "lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)), "
                "started_at=COALESCE(started_at,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='QUEUED' AND fencing_token=%s",
                (worker_id, token, lease_seconds, identity.platform_id, identity.tenant_id, run_id, int(fencing_token)),
            )
            return token if cursor.rowcount == 1 else None

    def heartbeat(self, identity: IdentityContext, run_id: str, fencing_token: int, *, lease_seconds: int = 30) -> bool:
        """续租只有当前 Worker 的 token 能成功，过期持有者得到 0 行影响。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE runs SET lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                (lease_seconds, identity.platform_id, identity.tenant_id, run_id, fencing_token),
            )
            return cursor.rowcount == 1

    def append_trace_span(self, identity: IdentityContext, span: object, fencing_token: int) -> bool:
        """事件写入通过 INSERT...SELECT 绑定当前 lease；旧 token 不产生行。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO platform_trace_spans "
                "(platform_id, tenant_id, run_id, span_id, trace_id, parent_span_id, span_type, status, "
                "sequence_no, attributes_json) "
                "SELECT platform_id, tenant_id, run_id, %s, %s, %s, %s, %s, %s, %s FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.span_type.value,
                    span.status,
                    span.sequence,
                    _json_dump(dict(span.attributes)),
                    identity.platform_id,
                    identity.tenant_id,
                    span.run_id,
                    fencing_token,
                ),
            )
            return cursor.rowcount == 1

    def finish_run(self, identity: IdentityContext, run: RunRecord, fencing_token: int) -> bool:
        """只允许持有有效 fencing token 的 Worker 写入终态和 Runtime 引用。"""

        return self.complete_run(identity, run, fencing_token, (), None)

    def complete_run(
        self,
        identity: IdentityContext,
        run: RunRecord,
        fencing_token: int,
        spans: Sequence[TraceSpan],
        usage: UsageRecord | None,
    ) -> bool:
        """在同一事务提交 Run 终态、Trace 索引、Usage 台账和 Runtime 结果引用。"""

        runtime = run.runtime_reference
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE runs SET state=%s, error_code=%s, result_ref=%s, runtime_session_ref=%s, "
                "runtime_result_ref=%s, lease_expires_at=NULL, worker_id=NULL, finished_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                (
                    run.status.value.upper(),
                    run.error_category,
                    run.result_ref,
                    runtime.runtime_session_ref if runtime else None,
                    runtime.runtime_result_ref if runtime else None,
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._inject_failure("after_run_update")
            for span in spans:
                run_id = span.run_id or run.run_id
                parameters = (
                    identity.platform_id,
                    identity.tenant_id,
                    run_id,
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.span_type.value,
                    span.status,
                    span.sequence,
                    _json_dump(dict(span.attributes)),
                )
                if span.span_type is SpanType.SESSION:
                    cursor.execute(
                        "INSERT IGNORE INTO platform_trace_spans "
                        "(platform_id, tenant_id, run_id, span_id, trace_id, parent_span_id, span_type, status, "
                        "sequence_no, attributes_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        parameters,
                    )
                else:
                    cursor.execute(
                        "INSERT INTO platform_trace_spans "
                        "(platform_id, tenant_id, run_id, span_id, trace_id, parent_span_id, span_type, status, "
                        "sequence_no, attributes_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        parameters,
                    )
            self._inject_failure("after_trace_spans")
            if usage is not None:
                cursor.execute(
                    "INSERT INTO usage_ledger "
                    "(platform_id, tenant_id, call_id, run_id, session_id, user_id, employee_id, model_route_id, "
                    "input_tokens, output_tokens, total_tokens, cost_amount, currency, pricing_version, "
                    "usage_status, estimation_method) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        f"model-{run.run_id}",
                        run.run_id,
                        run.session_id,
                        identity.user_id,
                        run.snapshot.employee_id,
                        run.snapshot.model_route_id,
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.total_tokens,
                        usage.cost_amount,
                        usage.currency,
                        usage.pricing_version,
                        "ESTIMATED" if usage.estimated else "EXACT",
                        usage.source if usage.estimated else None,
                    ),
                )
            self._inject_failure("after_usage")
            return True

    def mark_unknown(self, identity: IdentityContext, run_id: str, fencing_token: int) -> bool:
        """Runtime 调用结果不明时终止当前 lease，后续不能自动重放。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE runs SET state='UNKNOWN', error_code='RUNTIME_RESULT_UNKNOWN', worker_id=NULL, "
                "lease_expires_at=NULL, finished_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                (identity.platform_id, identity.tenant_id, run_id, fencing_token),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _load_span(row: tuple[Any, ...]) -> TraceSpan:
        attributes = _json_load(row[7])
        if not isinstance(attributes, dict):
            raise TypeError("Trace 属性必须是 JSON object")
        return TraceSpan(
            span_id=str(row[0]),
            trace_id=str(row[1]),
            parent_span_id=str(row[2]) if row[2] is not None else None,
            run_id=str(row[3]),
            span_type=SpanType(str(row[4])),
            status=str(row[5]),
            sequence=int(row[6]),
            attributes=attributes,
        )

    def append(self, identity: IdentityContext, run_id: str, span: TraceSpan) -> None:
        """将脱敏的 TraceSpan 写入当前租户 Run；查询端只会返回其租户内记录。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO platform_trace_spans "
                "(platform_id, tenant_id, run_id, span_id, trace_id, parent_span_id, span_type, status, "
                "sequence_no, attributes_json) "
                "SELECT platform_id, tenant_id, run_id, %s, %s, %s, %s, %s, %s, %s FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.span_type.value,
                    span.status,
                    span.sequence,
                    _json_dump(dict(span.attributes)),
                    identity.platform_id,
                    identity.tenant_id,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise self._not_found("run")

    def list(self, identity: IdentityContext, run_id: str) -> tuple[TraceSpan, ...]:
        return self.list_trace_spans(identity, run_id)

    def list_trace_spans(
        self,
        identity: IdentityContext,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TraceSpan, ...]:
        """按单调 sequence 游标分页，插入新 Span 不会改变已读页的顺序。"""

        if limit < 1 or limit > 500:
            raise ValueError("Trace 分页大小必须处于 1 到 500 之间")
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT span_id, trace_id, parent_span_id, run_id, span_type, status, sequence_no, attributes_json "
                "FROM platform_trace_spans WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND sequence_no>%s "
                "ORDER BY sequence_no, span_id LIMIT %s",
                (identity.platform_id, identity.tenant_id, run_id, after_sequence, limit),
            )
            rows = cursor.fetchall()
        return tuple(self._load_span(row) for row in rows)

    def list_session_trace(self, identity: IdentityContext, session_id: str) -> tuple[TraceSpan, ...]:
        """从 Session 的 Trace ID 读取整个链路，不解析 Runtime 私有历史。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT trace_id FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (identity.platform_id, identity.tenant_id, session_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("session")
            cursor.execute(
                "SELECT span_id, trace_id, parent_span_id, run_id, span_type, status, sequence_no, attributes_json "
                "FROM platform_trace_spans WHERE platform_id=%s AND tenant_id=%s AND trace_id=%s "
                "ORDER BY sequence_no, span_id",
                (identity.platform_id, identity.tenant_id, str(row[0])),
            )
            rows = cursor.fetchall()
        return tuple(self._load_span(item) for item in rows)

    def record(self, identity: IdentityContext, record: ModelCallRecord) -> None:
        """模型调用完成后只插入一次；更正必须使用 UsageAdjustment 追加记录。"""

        if record.tenant_id != identity.tenant_id:
            raise self._not_found("usage")
        if record.total_tokens != record.input_tokens + record.output_tokens:
            raise ValueError("total_tokens 必须等于 input_tokens + output_tokens")
        try:
            with self._transaction() as (_, cursor):
                cursor.execute(
                    "INSERT INTO usage_ledger "
                    "(platform_id, tenant_id, call_id, run_id, session_id, user_id, employee_id, model_route_id, "
                    "provider_request_id, input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, "
                    "total_tokens, cost_amount, currency, pricing_version, usage_status, estimation_method, duration_ms) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        record.call_id,
                        record.run_id,
                        record.session_id,
                        record.user_id,
                        record.employee_id,
                        record.model_route_id,
                        record.provider_request_id,
                        record.input_tokens,
                        record.output_tokens,
                        record.cached_input_tokens,
                        record.reasoning_tokens,
                        record.total_tokens,
                        record.cost_amount,
                        record.currency,
                        record.pricing_version,
                        record.usage_status.value,
                        record.usage_status.value if record.usage_status is UsageStatus.ESTIMATED else None,
                        record.duration_ms,
                    ),
                )
        except Exception as error:
            if getattr(error, "errno", None) == 1062:
                from agent_platform.domain.common import DomainError

                raise DomainError("CONFLICT", "已完成的用量记录不可覆盖") from error
            raise

    def get_usage_record(self, identity: IdentityContext, run_id: str) -> UsageRecord:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT input_tokens, output_tokens, total_tokens, usage_status, estimation_method, "
                "cost_amount, currency, pricing_version FROM usage_ledger "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s ORDER BY call_id LIMIT 1",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
        if row is None:
            return UsageRecord(0, 0, 0, source="UNAVAILABLE", estimated=True)
        status = UsageStatus(str(row[3]))
        return UsageRecord(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            total_tokens=int(row[2]),
            source=str(row[4] or status.value),
            estimated=status is not UsageStatus.EXACT,
            cost_amount=float(row[5]),
            currency=str(row[6]),
            pricing_version=str(row[7]),
        )

    def list_usage_records(self, identity: IdentityContext, run_id: str, *, limit: int = 100) -> tuple[ModelCallRecord, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("用量分页大小必须处于 1 到 500 之间")
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT call_id, run_id, tenant_id, user_id, employee_id, session_id, model_route_id, input_tokens, output_tokens, "
                "total_tokens, usage_status, provider_request_id, cost_amount, currency, pricing_version, "
                "cached_input_tokens, reasoning_tokens, duration_ms FROM usage_ledger "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s ORDER BY created_at, call_id LIMIT %s",
                (identity.platform_id, identity.tenant_id, run_id, limit),
            )
            rows = cursor.fetchall()
        return tuple(
            ModelCallRecord(
                call_id=str(row[0]),
                run_id=str(row[1]),
                tenant_id=str(row[2]),
                user_id=str(row[3]),
                employee_id=str(row[4]),
                session_id=str(row[5]),
                model_route_id=str(row[6]) if row[6] is not None else None,
                input_tokens=int(row[7]),
                output_tokens=int(row[8]),
                total_tokens=int(row[9]),
                usage_status=UsageStatus(str(row[10])),
                provider_request_id=str(row[11]) if row[11] is not None else None,
                cost_amount=float(row[12]),
                currency=str(row[13]),
                pricing_version=str(row[14]),
                cached_input_tokens=int(row[15]),
                reasoning_tokens=int(row[16]),
                duration_ms=int(row[17]),
            )
            for row in rows
        )

    def add_adjustment(self, identity: IdentityContext, adjustment: UsageAdjustment) -> None:
        """校验原始 ModelCall 属于当前租户后追加更正，不修改用量台账。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT call_id FROM usage_ledger WHERE platform_id=%s AND tenant_id=%s AND call_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, adjustment.original_call_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found("usage")
            cursor.execute(
                "INSERT INTO usage_adjustments "
                "(platform_id, tenant_id, adjustment_id, original_call_id, reason, delta_tokens, delta_cost_amount) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    adjustment.adjustment_id,
                    adjustment.original_call_id,
                    adjustment.reason,
                    adjustment.delta_tokens,
                    adjustment.delta_cost_amount,
                ),
            )

    def adjustments(self, identity: IdentityContext, original_call_id: str | None = None) -> tuple[UsageAdjustment, ...]:
        with self._reader() as (_, cursor):
            where_call = " AND original_call_id=%s" if original_call_id else ""
            parameters: tuple[object, ...] = (identity.platform_id, identity.tenant_id)
            if original_call_id:
                parameters += (original_call_id,)
            cursor.execute(
                "SELECT adjustment_id, original_call_id, reason, delta_cost_amount, delta_tokens "
                "FROM usage_adjustments WHERE platform_id=%s AND tenant_id=%s" + where_call +
                " ORDER BY created_at, adjustment_id",
                parameters,
            )
            rows = cursor.fetchall()
        return tuple(
            UsageAdjustment(str(row[0]), str(row[1]), str(row[2]), float(row[3]), int(row[4]))
            for row in rows
        )

    def record_tool_invocation(
        self, identity: IdentityContext, record: ToolInvocationRecord, fencing_token: int
    ) -> bool:
        """把 ToolInvocation 作为平台 Trace Span 保存，并由当前 Run fencing token 保护。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT trace_id, COALESCE(MAX(sequence_no),0)+1 FROM platform_trace_spans "
                "WHERE platform_id=%s AND tenant_id=%s AND trace_id=(SELECT trace_id FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s) "
                "GROUP BY trace_id",
                (identity.platform_id, identity.tenant_id, identity.platform_id, identity.tenant_id, record.run_id, fencing_token),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                "INSERT INTO platform_trace_spans "
                "(platform_id, tenant_id, run_id, span_id, trace_id, span_type, status, sequence_no, attributes_json) "
                "VALUES (%s,%s,%s,%s,%s,'tool',%s,%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    record.run_id,
                    record.invocation_id,
                    str(row[0]),
                    record.status,
                    int(row[1]),
                    _json_dump(
                        {
                            "tool_name": record.tool_name,
                            "protocol": record.protocol,
                            "version": record.version,
                            "error_category": record.error_category,
                            "duration_ms": record.duration_ms,
                            "retries": record.retries,
                            "input_ref": record.input_ref,
                            "output_ref": record.output_ref,
                            "approval_ref": record.approval_ref,
                        }
                    ),
                ),
            )
            return cursor.rowcount == 1

    def list_tool_invocations(self, identity: IdentityContext, run_id: str) -> tuple[ToolInvocationRecord, ...]:
        spans = self.list_trace_spans(identity, run_id, limit=500)
        result: list[ToolInvocationRecord] = []
        for span in spans:
            if span.span_type is not SpanType.TOOL:
                continue
            attributes = span.attributes
            result.append(
                ToolInvocationRecord(
                    invocation_id=span.span_id,
                    run_id=span.run_id,
                    tenant_id=identity.tenant_id,
                    tool_name=str(attributes.get("tool_name", "unknown")),
                    protocol=str(attributes.get("protocol", "builtin")),
                    version=str(attributes.get("version", "1")),
                    status=span.status,
                    error_category=str(attributes["error_category"]) if attributes.get("error_category") else None,
                    duration_ms=int(attributes.get("duration_ms", 0)),
                    retries=int(attributes.get("retries", 0)),
                    input_ref=str(attributes["input_ref"]) if attributes.get("input_ref") else None,
                    output_ref=str(attributes["output_ref"]) if attributes.get("output_ref") else None,
                    approval_ref=str(attributes["approval_ref"]) if attributes.get("approval_ref") else None,
                )
            )
        return tuple(result)

    def get_trace(self, identity: IdentityContext, run_id: str) -> RunTimeline:
        run = self.get_run(identity, run_id)
        spans = tuple(
            span for span in self.list_session_trace(identity, run.session_id)
            if span.run_id == run_id or span.span_type is SpanType.SESSION
        )
        return RunTimeline(run, spans, self.get_usage_record(identity, run_id), run.runtime_reference)

    def get_run(self, identity: IdentityContext, run_id: str) -> RunRecord:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT run_id, session_id, platform_id, tenant_id, trace_id, request_digest, state, "
                "error_code, snapshot_json, context_snapshot_json, runtime_session_ref, runtime_result_ref, result_ref "
                "FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("run")
        snapshot_data = _json_load(row[8])
        assert isinstance(snapshot_data, dict)
        snapshot = RunSnapshot(
            employee_id=str(snapshot_data["employee_id"]),
            employee_version=int(snapshot_data["employee_version"]),
            skill_version_ids=tuple(str(value) for value in snapshot_data["skill_version_ids"]),
            tool_version_ids=tuple(str(value) for value in snapshot_data["tool_version_ids"]),
            skill_version_refs=tuple((str(ref[0]), int(ref[1])) for ref in snapshot_data["skill_version_refs"]),
            tool_version_refs=tuple((str(ref[0]), int(ref[1])) for ref in snapshot_data["tool_version_refs"]),
            execution_profile_id=str(snapshot_data["execution_profile_id"]),
            execution_profile_version=int(snapshot_data["execution_profile_version"]),
            model_route_id=str(snapshot_data["model_route_id"]) if snapshot_data.get("model_route_id") else None,
            hub_resource_refs=tuple(
                HubResourceSnapshot(
                    local_resource_type=str(item["local_resource_type"]),
                    local_resource_id=str(item["local_resource_id"]),
                    local_resource_version=int(item["local_resource_version"]),
                    hub_resource_id=str(item["hub_resource_id"]),
                    hub_version=int(item["hub_version"]),
                    manifest_sha256=str(item["manifest_sha256"]),
                    mode=str(item["mode"]),
                )
                for item in snapshot_data.get("hub_resource_refs", [])
                if isinstance(item, dict)
            ),
            skill_file_packages=tuple(
                SkillFilePackageSnapshot(
                    skill_id=str(item["skill_id"]),
                    skill_version=int(item["skill_version"]),
                    entry_path=str(item["entry_path"]),
                    manifest_sha256=str(item["manifest_sha256"]),
                    files=tuple(
                        SkillFileSnapshotEntry(
                            file_path=str(file["file_path"]),
                            media_asset_id=str(file["media_asset_id"]),
                            mime_type=str(file["mime_type"]),
                            size_bytes=int(file["size_bytes"]),
                            content_sha256=str(file["content_sha256"]),
                            asset_platform_id=str(file.get("asset_platform_id", "")),
                            asset_tenant_id=str(file.get("asset_tenant_id", "")),
                            source_mode=str(file.get("source_mode", "LOCAL")),
                        )
                        for file in item.get("files", [])
                        if isinstance(file, dict)
                    ),
                )
                for item in snapshot_data.get("skill_file_packages", [])
                if isinstance(item, dict)
            ),
        )
        context_value = _json_load(row[9]) if row[9] is not None else None
        context_snapshot = self._load_context(context_value) if context_value is not None else None
        runtime_session_ref = str(row[10]) if row[10] is not None else None
        runtime_reference = (
            RuntimeReference("fake", runtime_session_ref, str(row[11]) if row[11] else None)
            if runtime_session_ref
            else None
        )
        state = str(row[6]).lower()
        runtime_status = (
            RuntimeStatus(state)
            if state in {item.value for item in RuntimeStatus}
            else RuntimeStatus.UNKNOWN
        )
        return RunRecord(
            run_id=str(row[0]),
            session_id=str(row[1]),
            platform_id=str(row[2]),
            tenant_id=str(row[3]),
            trace_id=str(row[4]),
            prompt_digest=str(row[5]),
            snapshot=snapshot,
            status=runtime_status,
            error_category=str(row[7]) if row[7] else None,
            result_ref=str(row[12]) if row[12] else None,
            runtime_reference=runtime_reference,
            context_snapshot=context_snapshot,
        )

    @staticmethod
    def _load_context(value: object) -> ContextSnapshot:
        if not isinstance(value, dict):
            raise TypeError("ContextSnapshot 必须为 JSON object")

        def source_from(item: dict[str, object]) -> ContextSource:
            return ContextSource(
                source_type=ContextSourceType(str(item["source_type"])),
                reference=str(item["reference"]),
                token_estimate=int(item["token_estimate"]),
                content_hash=str(item["content_hash"]),
                included=bool(item["included"]),
                reason=str(item["reason"]) if item.get("reason") is not None else None,
            )

        return ContextSnapshot(
            snapshot_id=str(value["snapshot_id"]),
            run_id=str(value["run_id"]),
            trace_id=str(value["trace_id"]),
            sources=tuple(source_from(item) for item in value["sources"]),
            total_tokens=int(value["total_tokens"]),
            excluded_sources=tuple(source_from(item) for item in value["excluded_sources"]),
            runtime_handoff={str(key): str(item) for key, item in value["runtime_handoff"].items()},
        )
