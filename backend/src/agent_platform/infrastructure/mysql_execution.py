"""复用 Harness Session/Run 表持久化平台执行快照和 opaque Runtime 引用。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError
from agent_platform.domain.context import ContextSnapshot, ContextSource, ContextSourceType
from agent_platform.domain.conversations import Message, MessageSource
from agent_platform.domain.deployment_runtime import RuntimeOptions
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelRouteSnapshot
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
from agent_platform.ports.conversation_repository import StoredMessage
from agent_platform.ports.execution_repository import AssistantMessageRef, RunClaim, RunInputRef


class _SessionRuntimeLeaseLost(RuntimeError):
    """用于回滚只续 Run、未能同步续 Session Home 的租约事务。"""


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
        "engine_name": snapshot.engine_name,
        "execution_profile_version": snapshot.execution_profile_version,
        "model_route_id": snapshot.model_route_id,
        "model_alias": snapshot.model_alias,
        "runtime_config_version": snapshot.runtime_config_version,
        "runtime_profile_source": snapshot.runtime_profile_source,
        "runtime_model_route_source": snapshot.runtime_model_route_source,
        "runtime_options": snapshot.runtime_options.to_mapping(include_none=True),
        "model_route_snapshot": (
            snapshot.model_route_snapshot.to_snapshot_dict()
            if snapshot.model_route_snapshot is not None
            else None
        ),
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
                "runtime_config_version, runtime_profile_source, runtime_model_route_source, "
                "trace_id, composition_snapshot_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE status=VALUES(status), "
                "harness_home_ref=COALESCE(harness_home_ref,VALUES(harness_home_ref))",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    session.session_id,
                    identity.user_id,
                    engine_name,
                    session.runtime_session_ref or session.session_id,
                    session.harness_home_ref,
                    session.status,
                    _json_dump(
                        {
                            "trace_id": session.trace_id,
                            "session_span_id": session.span_id,
                            "model_alias": session.model_alias,
                            "model_route_snapshot": (
                                session.model_route_snapshot.to_snapshot_dict()
                                if session.model_route_snapshot is not None
                                else None
                            ),
                            "runtime_options": session.runtime_options.to_mapping(include_none=True),
                        }
                    ),
                    session.deployment_id,
                    session.employee_id,
                    session.employee_version,
                    session.execution_profile_id,
                    session.execution_profile_version,
                    session.model_route_id,
                    session.runtime_config_version,
                    session.runtime_profile_source,
                    session.runtime_model_route_source,
                    session.trace_id,
                    _json_dump(
                        {
                            "employee_id": session.employee_id,
                            "employee_version": session.employee_version,
                            "execution_profile_id": session.execution_profile_id,
                            "execution_profile_version": session.execution_profile_version,
                            "model_route_id": session.model_route_id,
                            "model_alias": session.model_alias,
                            "runtime_config_version": session.runtime_config_version,
                            "runtime_profile_source": session.runtime_profile_source,
                            "runtime_model_route_source": session.runtime_model_route_source,
                            "runtime_options": session.runtime_options.to_mapping(include_none=True),
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
        harness_home_ref: str | None = None,
    ) -> None:
        """只更新本租户 Session 的 opaque Runtime 引用，不能由客户端提供路径。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE sessions SET engine_session_id=%s, runtime_result_ref=%s, "
                "harness_home_ref=COALESCE(harness_home_ref,%s) "
                "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (
                    runtime_session_ref,
                    runtime_result_ref,
                    harness_home_ref,
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
                "execution_profile_id, execution_profile_version, model_route_id, trace_id, engine_session_id, "
                "status, metadata_json, harness_home_ref, runtime_config_version, runtime_profile_source, "
                "runtime_model_route_source, engine_name "
                "FROM sessions WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (identity.platform_id, identity.tenant_id, session_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("session")
        runtime_ref = str(row[10]) if str(row[10]) != str(row[0]) else None
        metadata = _json_load(row[12])
        session_span_id = (
            str(metadata.get("session_span_id", "")) if isinstance(metadata, dict) else ""
        )
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
            span_id=session_span_id,
            harness_home_ref=str(row[13]) if row[13] is not None else None,
            runtime_config_version=int(row[14]) if row[14] is not None else None,
            runtime_profile_source=str(row[15]) if row[15] is not None else None,
            runtime_model_route_source=str(row[16]) if row[16] is not None else None,
            engine_name=str(row[17]) if row[17] is not None else "",
            model_route_snapshot=(
                ModelRouteSnapshot.from_snapshot_dict(metadata["model_route_snapshot"])
                if isinstance(metadata, dict)
                and isinstance(metadata.get("model_route_snapshot"), dict)
                else None
            ),
            model_alias=(
                str(metadata["model_alias"])
                if isinstance(metadata, dict) and metadata.get("model_alias") is not None
                else None
            ),
            runtime_options=RuntimeOptions.from_mapping(
                metadata.get("runtime_options", {}) if isinstance(metadata, dict) else {}
            ).resolve(),
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
                "(platform_id, tenant_id, run_id, session_id, idempotency_key, request_digest, state, submitted_by_user_id, "
                "error_code, result_ref, employee_id, employee_version, execution_profile_id, "
                "execution_profile_version, model_route_id, trace_id, snapshot_json, context_snapshot_json, "
                "runtime_session_ref, runtime_result_ref, finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP(6)) "
                "ON DUPLICATE KEY UPDATE state=VALUES(state), error_code=VALUES(error_code), "
                "result_ref=VALUES(result_ref), snapshot_json=VALUES(snapshot_json), "
                "context_snapshot_json=VALUES(context_snapshot_json), runtime_session_ref=VALUES(runtime_session_ref), "
                "runtime_result_ref=VALUES(runtime_result_ref), submitted_by_user_id=VALUES(submitted_by_user_id), "
                "finished_at=VALUES(finished_at)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    run.session_id,
                    idempotency_key,
                    run.prompt_digest,
                    state,
                    run.submitted_by_user_id,
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
            self._touch_conversations_for_run(cursor, identity, run.run_id)

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
                "submitted_by_user_id, "
                "employee_id, employee_version, execution_profile_id, execution_profile_version, model_route_id, "
                "trace_id, snapshot_json, context_snapshot_json, orchestration_id, task_id, attempt_no) "
                "VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE run_id=run_id",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    run.session_id,
                    idempotency_key,
                    run.prompt_digest,
                    run.submitted_by_user_id,
                    snapshot.employee_id,
                    snapshot.employee_version,
                    snapshot.execution_profile_id,
                    snapshot.execution_profile_version,
                    snapshot.model_route_id,
                    run.trace_id,
                    _json_dump(_snapshot_data(snapshot)),
                    _json_dump(_context_data(run.context_snapshot)),
                    run.orchestration_id,
                    run.task_id,
                    run.attempt_no,
                ),
            )
            created = cursor.rowcount == 1
            existing = self._find_run_idempotency(cursor, identity, run.session_id, idempotency_key)
            if existing is None:
                raise DomainError("CONFLICT", "Run 标识与现有记录冲突")
            if str(existing[1]) != run.prompt_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
            existing_run_id = str(existing[0])
            if created:
                self._touch_conversations_for_run(cursor, identity, run.run_id)
        return self.get_run(identity, existing_run_id), created

    def submit_conversation_turn(
        self,
        identity: IdentityContext,
        conversation_id: str,
        run: RunRecord,
        message: Message,
        idempotency_key: str,
        *,
        content_ref: str,
        content_sha256: str,
        byte_length: int,
        content_type: str,
    ) -> tuple[RunRecord, StoredMessage, bool]:
        """在一个事务中授权并提交用户消息索引、Conversation 时间和 QUEUED Run。"""

        if not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 不能为空")
        if (
            identity.platform_id != run.platform_id
            or identity.tenant_id != run.tenant_id
            or run.submitted_by_user_id != identity.user_id
            or run.status is not RuntimeStatus.QUEUED
        ):
            raise DomainError("AUTHORIZATION_DENIED", "turn 身份或 Run 快照不一致")
        if (
            message.platform_id != identity.platform_id
            or message.tenant_id != identity.tenant_id
            or message.conversation_id != conversation_id
            or message.sender_id != identity.user_id
            or message.source is not MessageSource.USER
        ):
            raise DomainError("AUTHORIZATION_DENIED", "turn Message 不属于当前用户和 Conversation")
        payload = message.text.encode("utf-8")
        if (
            byte_length != len(payload)
            or content_sha256 != hashlib.sha256(payload).hexdigest()
            or not content_ref.strip()
        ):
            raise DomainError("INVALID_ARGUMENT", "Message 对象引用、字节数或摘要不一致")

        created = False
        existing_run_id = run.run_id
        stored_message: StoredMessage | None = None
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT c.status, b.employee_id, b.employee_version, s.employee_version, "
                "s.status, s.last_message_sequence "
                "FROM conversations c "
                "JOIN conversation_participants cp ON cp.platform_id=c.platform_id "
                "AND cp.tenant_id=c.tenant_id AND cp.conversation_id=c.conversation_id AND cp.user_id=%s "
                "JOIN agent_bindings b ON b.platform_id=c.platform_id AND b.tenant_id=c.tenant_id "
                "AND b.conversation_id=c.conversation_id AND b.employee_id=%s AND b.session_id=%s "
                "AND b.status='ACTIVE' "
                "JOIN sessions s ON s.platform_id=b.platform_id AND s.tenant_id=b.tenant_id "
                "AND s.session_id=b.session_id "
                "WHERE c.platform_id=%s AND c.tenant_id=%s AND c.conversation_id=%s FOR UPDATE",
                (
                    identity.user_id,
                    run.snapshot.employee_id,
                    run.session_id,
                    identity.platform_id,
                    identity.tenant_id,
                    conversation_id,
                ),
            )
            binding = cursor.fetchone()
            if binding is None:
                raise self._not_found("conversation_binding")
            if str(binding[0]) != "ACTIVE" or str(binding[4]) != "ACTIVE":
                raise DomainError("INVALID_STATE", "Conversation 或 Session 不是 ACTIVE 状态")
            if int(binding[2]) != run.snapshot.employee_version or int(binding[3]) != run.snapshot.employee_version:
                raise DomainError("CONFLICT", "员工绑定版本与 RunSnapshot 不一致")

            cursor.execute(
                "INSERT INTO runs "
                "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
                "submitted_by_user_id,employee_id,employee_version,execution_profile_id,"
                "execution_profile_version,model_route_id,trace_id,snapshot_json,context_snapshot_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE run_id=run_id",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    run.session_id,
                    idempotency_key,
                    run.prompt_digest,
                    identity.user_id,
                    run.snapshot.employee_id,
                    run.snapshot.employee_version,
                    run.snapshot.execution_profile_id,
                    run.snapshot.execution_profile_version,
                    run.snapshot.model_route_id,
                    run.trace_id,
                    _json_dump(_snapshot_data(run.snapshot)),
                    _json_dump(_context_data(run.context_snapshot)),
                ),
            )
            created = cursor.rowcount == 1
            existing = self._find_run_idempotency(
                cursor, identity, run.session_id, idempotency_key
            )
            if existing is None:
                raise DomainError("CONFLICT", "Run 标识与现有记录冲突")
            existing_run_id = str(existing[0])
            if str(existing[1]) != run.prompt_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的 turn 内容或快照已改变")
            self._inject_failure("after_turn_run_insert")

            if created:
                sequence = int(binding[5]) + 1
                cursor.execute(
                    "INSERT INTO messages "
                    "(platform_id,tenant_id,message_id,session_id,run_id,sequence_no,role,status,"
                    "content_ref,content_sha256,byte_length,content_type,sender_id,source,routing_depth,"
                    "conversation_id,retention_policy) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'USER','COMPLETE',%s,%s,%s,%s,%s,'user',0,%s,'conversation')",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        message.message_id,
                        run.session_id,
                        run.run_id,
                        sequence,
                        content_ref,
                        content_sha256,
                        byte_length,
                        content_type,
                        identity.user_id,
                        conversation_id,
                    ),
                )
                cursor.execute(
                    "UPDATE sessions SET last_message_sequence=%s WHERE platform_id=%s "
                    "AND tenant_id=%s AND session_id=%s",
                    (sequence, identity.platform_id, identity.tenant_id, run.session_id),
                )
                cursor.execute(
                    "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP(6) "
                    "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
                    (identity.platform_id, identity.tenant_id, conversation_id),
                )
                self._inject_failure("after_turn_message_insert")
                stored_message = StoredMessage(
                    message_id=message.message_id,
                    conversation_id=conversation_id,
                    platform_id=identity.platform_id,
                    tenant_id=identity.tenant_id,
                    sender_id=identity.user_id,
                    source=MessageSource.USER,
                    content_ref=content_ref,
                    content_sha256=content_sha256,
                    byte_length=byte_length,
                    content_type=content_type,
                    sequence_no=sequence,
                    routing_depth=0,
                )
            else:
                cursor.execute(
                    "SELECT message_id,conversation_id,platform_id,tenant_id,sender_id,source,"
                    "content_ref,content_sha256,byte_length,content_type,sequence_no,routing_depth,created_at "
                    "FROM messages WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                    "AND conversation_id=%s AND session_id=%s AND source='user'",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        existing_run_id,
                        conversation_id,
                        run.session_id,
                    ),
                )
                message_row = cursor.fetchone()
                if message_row is None:
                    raise DomainError("CONFLICT", "幂等 Run 缺少对应的用户消息")
                stored_message = self._stored_message_from_row(message_row)

        if stored_message is None:
            raise RuntimeError("turn transaction did not return a stored message")
        return self.get_run(identity, existing_run_id), stored_message, created

    def submit_worker_run(
        self,
        identity: IdentityContext,
        conversation_id: str,
        run: RunRecord,
        idempotency_key: str,
        input_message: Message,
        *,
        input_content_ref: str,
        input_content_sha256: str,
        input_byte_length: int,
        input_content_type: str,
        display_message: Message | None = None,
        display_content_ref: str | None = None,
        display_content_sha256: str | None = None,
        display_byte_length: int | None = None,
        display_content_type: str | None = None,
    ) -> tuple[RunRecord, bool]:
        """原子排队 Worker Run，并把私有执行指令与可见原文分开保存。"""

        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise DomainError("INVALID_ARGUMENT", "Worker Run 幂等键必须在 1 到 255 个字符之间")
        if (
            identity.platform_id != run.platform_id
            or identity.tenant_id != run.tenant_id
            or run.submitted_by_user_id != identity.user_id
            or run.status is not RuntimeStatus.QUEUED
        ):
            raise DomainError("AUTHORIZATION_DENIED", "Worker Run 身份或状态不一致")
        if (
            input_message.platform_id != identity.platform_id
            or input_message.tenant_id != identity.tenant_id
            or input_message.conversation_id != conversation_id
            or input_message.sender_id != identity.user_id
            or input_message.source is not MessageSource.SYSTEM
        ):
            raise DomainError("AUTHORIZATION_DENIED", "Worker 私有输入不属于当前 Conversation")
        input_payload = input_message.text.encode("utf-8")
        if (
            input_byte_length != len(input_payload)
            or input_content_sha256 != hashlib.sha256(input_payload).hexdigest()
            or not input_content_ref.strip()
        ):
            raise DomainError("INVALID_ARGUMENT", "Worker 输入对象引用、字节数或摘要不一致")
        if display_message is not None:
            if (
                display_message.platform_id != identity.platform_id
                or display_message.tenant_id != identity.tenant_id
                or display_message.conversation_id != conversation_id
                or display_message.sender_id != identity.user_id
                or display_message.source is not MessageSource.USER
                or display_content_ref is None
                or display_content_sha256 is None
                or display_byte_length is None
                or display_content_type is None
            ):
                raise DomainError("AUTHORIZATION_DENIED", "Worker Run 可见消息不属于当前用户")
            display_payload = display_message.text.encode("utf-8")
            if (
                display_byte_length != len(display_payload)
                or display_content_sha256 != hashlib.sha256(display_payload).hexdigest()
                or not display_content_ref.strip()
            ):
                raise DomainError("INVALID_ARGUMENT", "可见消息对象引用、字节数或摘要不一致")

        created = False
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT c.status,b.employee_id,b.employee_version,s.employee_version,s.status,"
                "s.last_message_sequence FROM conversations c "
                "JOIN conversation_participants cp ON cp.platform_id=c.platform_id "
                "AND cp.tenant_id=c.tenant_id AND cp.conversation_id=c.conversation_id AND cp.user_id=%s "
                "JOIN agent_bindings b ON b.platform_id=c.platform_id AND b.tenant_id=c.tenant_id "
                "AND b.conversation_id=c.conversation_id AND b.employee_id=%s AND b.session_id=%s "
                "AND b.status='ACTIVE' JOIN sessions s ON s.platform_id=b.platform_id "
                "AND s.tenant_id=b.tenant_id AND s.session_id=b.session_id "
                "WHERE c.platform_id=%s AND c.tenant_id=%s AND c.conversation_id=%s "
                "AND c.conversation_mode='TEAM' FOR UPDATE",
                (
                    identity.user_id, run.snapshot.employee_id, run.session_id,
                    identity.platform_id, identity.tenant_id, conversation_id,
                ),
            )
            binding = cursor.fetchone()
            if binding is None:
                raise self._not_found("team_conversation_binding")
            if str(binding[0]) != "ACTIVE" or str(binding[4]) != "ACTIVE":
                raise DomainError("INVALID_STATE", "Conversation 或成员 Session 不是 ACTIVE 状态")
            if int(binding[2]) != run.snapshot.employee_version or int(binding[3]) != run.snapshot.employee_version:
                raise DomainError("CONFLICT", "成员绑定版本与 RunSnapshot 不一致")

            cursor.execute(
                "INSERT INTO runs "
                "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
                "submitted_by_user_id,employee_id,employee_version,execution_profile_id,"
                "execution_profile_version,model_route_id,trace_id,snapshot_json,context_snapshot_json,"
                "orchestration_id,task_id,attempt_no) "
                "VALUES (%s,%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE run_id=run_id",
                (
                    identity.platform_id, identity.tenant_id, run.run_id, run.session_id,
                    idempotency_key, run.prompt_digest, identity.user_id, run.snapshot.employee_id,
                    run.snapshot.employee_version, run.snapshot.execution_profile_id,
                    run.snapshot.execution_profile_version, run.snapshot.model_route_id, run.trace_id,
                    _json_dump(_snapshot_data(run.snapshot)), _json_dump(_context_data(run.context_snapshot)),
                    run.orchestration_id, run.task_id, run.attempt_no,
                ),
            )
            created = cursor.rowcount == 1
            existing = self._find_run_idempotency(cursor, identity, run.session_id, idempotency_key)
            if existing is None:
                raise DomainError("CONFLICT", "Worker Run 标识与现有记录冲突")
            existing_run_id = str(existing[0])
            if str(existing[1]) != run.prompt_digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Worker Run 幂等键对应的快照或输入已改变")
            self._inject_failure("after_worker_run_insert")

            if created:
                sequence = int(binding[5])
                cursor.execute(
                    "INSERT INTO messages "
                    "(platform_id,tenant_id,message_id,session_id,run_id,sequence_no,role,status,"
                    "content_ref,content_sha256,byte_length,content_type,sender_id,source,routing_depth,"
                    "conversation_id,retention_policy) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'USER','COMPLETE',%s,%s,%s,%s,%s,'system',0,%s,'conversation')",
                    (
                        identity.platform_id, identity.tenant_id, input_message.message_id,
                        run.session_id, run.run_id, sequence + 1, input_content_ref,
                        input_content_sha256, input_byte_length, input_content_type,
                        identity.user_id, conversation_id,
                    ),
                )
                sequence += 1
                self._inject_failure("after_worker_input_insert")
                if display_message is not None:
                    cursor.execute(
                        "INSERT INTO messages "
                        "(platform_id,tenant_id,message_id,session_id,run_id,sequence_no,role,status,"
                        "content_ref,content_sha256,byte_length,content_type,sender_id,source,routing_depth,"
                        "conversation_id,retention_policy) "
                        "VALUES (%s,%s,%s,%s,NULL,%s,'USER','COMPLETE',%s,%s,%s,%s,%s,'user',0,%s,'conversation')",
                        (
                            identity.platform_id, identity.tenant_id, display_message.message_id,
                            run.session_id, sequence + 1, display_content_ref,
                            display_content_sha256, display_byte_length, display_content_type,
                            identity.user_id, conversation_id,
                        ),
                    )
                    sequence += 1
                cursor.execute(
                    "UPDATE sessions SET last_message_sequence=%s WHERE platform_id=%s "
                    "AND tenant_id=%s AND session_id=%s",
                    (sequence, identity.platform_id, identity.tenant_id, run.session_id),
                )
                cursor.execute(
                    "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP(6) "
                    "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
                    (identity.platform_id, identity.tenant_id, conversation_id),
                )
        if created:
            return run, True
        return self.get_run(identity, existing_run_id), False

    @staticmethod
    def _stored_message_from_row(row: tuple[Any, ...]) -> StoredMessage:
        return StoredMessage(
            message_id=str(row[0]),
            conversation_id=str(row[1]),
            platform_id=str(row[2]),
            tenant_id=str(row[3]),
            sender_id=str(row[4]),
            source=MessageSource(str(row[5])),
            content_ref=str(row[6]),
            content_sha256=str(row[7]),
            byte_length=int(row[8]),
            content_type=str(row[9]),
            sequence_no=int(row[10]),
            routing_depth=int(row[11]),
            created_at=row[12],
        )

    @staticmethod
    def _touch_conversations_for_run(
        cursor: Any, identity: IdentityContext, run_id: str
    ) -> None:
        """在 Run 状态事务内按实际 Session 推进其绑定 Conversation 更新时间。"""

        cursor.execute(
            "UPDATE conversations c JOIN agent_bindings b ON b.platform_id=c.platform_id "
            "AND b.tenant_id=c.tenant_id AND b.conversation_id=c.conversation_id "
            "JOIN runs r ON r.platform_id=b.platform_id AND r.tenant_id=b.tenant_id "
            "AND r.session_id=b.session_id "
            "SET c.updated_at=CURRENT_TIMESTAMP(6) "
            "WHERE r.platform_id=%s AND r.tenant_id=%s AND r.run_id=%s AND b.status='ACTIVE'",
            (identity.platform_id, identity.tenant_id, run_id),
        )

    @staticmethod
    def _insert_session_runtime_lease(
        cursor: Any,
        identity: IdentityContext,
        session_id: str,
        run_id: str,
        worker_id: str,
        fencing_token: int,
        lease_seconds: int,
    ) -> bool:
        """同一事务中占有一个 Session Home；已 ACTIVE 或 QUARANTINED 时拒绝复用。"""

        cursor.execute(
            "SELECT status FROM sessions WHERE platform_id=%s AND tenant_id=%s "
            "AND session_id=%s FOR UPDATE",
            (identity.platform_id, identity.tenant_id, session_id),
        )
        session_row = cursor.fetchone()
        if session_row is None or str(session_row[0]) != "ACTIVE":
            return False
        cursor.execute(
            "INSERT IGNORE INTO session_runtime_leases "
            "(platform_id,tenant_id,session_id,run_id,worker_id,fencing_token,"
            "lease_expires_at,state) VALUES (%s,%s,%s,%s,%s,%s,"
            "TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)),'ACTIVE')",
            (
                identity.platform_id,
                identity.tenant_id,
                session_id,
                run_id,
                worker_id,
                fencing_token,
                lease_seconds,
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _quarantine_session_runtime_lease(
        cursor: Any,
        identity: IdentityContext,
        session_id: str,
        run_id: str,
    ) -> None:
        """结果不明时永久隔离 Session Home，直到显式恢复或新建 Session。"""

        cursor.execute(
            "UPDATE session_runtime_leases SET state='QUARANTINED', worker_id=NULL, "
            "lease_expires_at=NULL, fencing_token=fencing_token+1 "
            "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s AND run_id=%s "
            "AND state='ACTIVE'",
            (identity.platform_id, identity.tenant_id, session_id, run_id),
        )
        cursor.execute(
            "UPDATE sessions SET status='INTERRUPTED' WHERE platform_id=%s "
            "AND tenant_id=%s AND session_id=%s AND status='ACTIVE'",
            (identity.platform_id, identity.tenant_id, session_id),
        )

    @staticmethod
    def _release_session_runtime_lease(
        cursor: Any,
        identity: IdentityContext,
        run_id: str,
        fencing_token: int,
    ) -> bool:
        """只允许当前 Run token 释放 ACTIVE Session Home。"""

        cursor.execute(
            "DELETE FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s "
            "AND run_id=%s AND fencing_token=%s AND state='ACTIVE'",
            (identity.platform_id, identity.tenant_id, run_id, fencing_token),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _find_run_idempotency(
        cursor: Any, identity: IdentityContext, session_id: str, idempotency_key: str
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            "SELECT run_id, request_digest FROM runs "
            "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s AND idempotency_key=%s",
            (identity.platform_id, identity.tenant_id, session_id, idempotency_key),
        )
        return cursor.fetchone()

    def get_run_by_idempotency(
        self, identity: IdentityContext, session_id: str, idempotency_key: str
    ) -> RunRecord | None:
        """用现有幂等唯一键查找已提交的 Run，供服务重启或跨进程重放。"""

        with self._reader() as (_, cursor):
            existing = self._find_run_idempotency(cursor, identity, session_id, idempotency_key)
        return self.get_run(identity, str(existing[0])) if existing is not None else None

    def worker_pool_metrics(self, identity: IdentityContext, engine_name: str) -> dict[str, int | bool]:
        """从 MySQL Run 租约统计当前租户每个 Engine 的排队数和活跃 Worker 数。"""

        normalized = engine_name.strip().casefold().replace("-", "_")
        if normalized == "deepseek":
            normalized = "deepseek_harness"
        aliases = ("deepseek", "deepseek_harness") if normalized in {"deepseek", "deepseek_harness"} else (normalized,)
        placeholders = ",".join("%s" for _ in aliases)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT COALESCE(SUM(r.state='QUEUED'),0), "
                "COUNT(DISTINCT CASE WHEN r.state IN ('STARTING','RUNNING','CANCELLING') "
                "AND r.lease_expires_at>CURRENT_TIMESTAMP(6) THEN r.worker_id END) "
                "FROM runs r JOIN sessions s ON s.platform_id=r.platform_id "
                "AND s.tenant_id=r.tenant_id AND s.session_id=r.session_id "
                f"WHERE r.platform_id=%s AND r.tenant_id=%s AND LOWER(REPLACE(s.engine_name,'-','_')) IN ({placeholders})",
                (identity.platform_id, identity.tenant_id, *aliases),
            )
            row = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(DISTINCT worker_id) FROM runtime_worker_heartbeats "
                "WHERE platform_id=%s AND engine_name IN (%s) AND status='ACTIVE' "
                "AND heartbeat_at>=TIMESTAMPADD(SECOND,-30,CURRENT_TIMESTAMP(6))",
                (identity.platform_id, normalized),
            )
            worker_row = cursor.fetchone()
        worker_count = int(worker_row[0] or 0) if worker_row is not None else 0
        return {
            "queue_depth": int(row[0] or 0),
            "active_workers": int(row[1] or 0),
            "worker_count": worker_count,
            "healthy": worker_count > 0,
        }

    def heartbeat_worker(
        self,
        platform_id: str,
        worker_id: str,
        engine_name: str,
        *,
        status: str = "ACTIVE",
    ) -> None:
        """受信 Worker 按平台/进程/引擎写入或收敛短期健康心跳。"""

        normalized = engine_name.strip().casefold().replace("-", "_")
        if not platform_id.strip() or not worker_id.strip() or not normalized or status not in {"ACTIVE", "STOPPING"}:
            raise ValueError("Worker 心跳的平台、进程、引擎和状态必须有效")
        if normalized == "deepseek":
            normalized = "deepseek_harness"
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO runtime_worker_heartbeats (platform_id,worker_id,engine_name,status) "
                "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE status=VALUES(status), "
                "heartbeat_at=CURRENT_TIMESTAMP(6)",
                (platform_id, worker_id, normalized, status),
            )

    def acquire_lease(
        self, identity: IdentityContext, run_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> int | None:
        """只领取 QUEUED Run；过期 RUNNING 标记 UNKNOWN，避免不确定执行被自动重放。"""

        if lease_seconds <= 0 or not worker_id.strip():
            raise ValueError("lease_seconds 和 worker_id 必须有效")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT state, fencing_token, lease_expires_at, session_id FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("run")
            state, fencing_token, lease_expires_at, session_id = row
            if str(state) == "RUNNING" and lease_expires_at is not None:
                cursor.execute("SELECT %s <= CURRENT_TIMESTAMP(6)", (lease_expires_at,))
                if bool(cursor.fetchone()[0]):
                    cursor.execute(
                        "UPDATE runs SET state='UNKNOWN', worker_id=NULL, lease_expires_at=NULL, "
                        "fencing_token=fencing_token+1, finished_at=CURRENT_TIMESTAMP(6) "
                        "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='RUNNING' AND fencing_token=%s",
                        (identity.platform_id, identity.tenant_id, run_id, int(fencing_token)),
                    )
                    if cursor.rowcount == 1:
                        self._quarantine_session_runtime_lease(
                            cursor, identity, str(session_id), run_id
                        )
                        self._touch_conversations_for_run(cursor, identity, run_id)
                return None
            if str(state) != "QUEUED":
                return None
            token = int(fencing_token) + 1
            if not self._insert_session_runtime_lease(
                cursor,
                identity,
                str(session_id),
                run_id,
                worker_id,
                token,
                lease_seconds,
            ):
                return None
            cursor.execute(
                "UPDATE runs SET state='RUNNING', worker_id=%s, fencing_token=%s, "
                "lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)), "
                "started_at=COALESCE(started_at,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='QUEUED' AND fencing_token=%s",
                (worker_id, token, lease_seconds, identity.platform_id, identity.tenant_id, run_id, int(fencing_token)),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    "DELETE FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s "
                    "AND session_id=%s AND run_id=%s AND fencing_token=%s AND state='ACTIVE'",
                    (identity.platform_id, identity.tenant_id, session_id, run_id, token),
                )
                return None
            self._touch_conversations_for_run(cursor, identity, run_id)
            return token

    def claim_next_queued_run(
        self,
        platform_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 30,
        engine_names: tuple[str, ...] | None = None,
    ) -> RunClaim | None:
        """以平台队列领取一个 Run；后续状态写入必须重新限定到 claim 返回的租户。"""

        if not platform_id.strip() or not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("platform_id、worker_id 和 lease_seconds 必须有效")
        normalized_names: set[str] = set()
        for name in engine_names or ():
            normalized = name.strip().casefold().replace("-", "_")
            if normalized:
                normalized_names.add(normalized)
                if normalized in {"deepseek", "deepseek_harness"}:
                    normalized_names.update({"deepseek", "deepseek_harness"})
        engine_filter = tuple(sorted(normalized_names))
        engine_clause = ""
        engine_parameters: tuple[str, ...] = ()
        if engine_filter:
            engine_clause = " AND LOWER(REPLACE(s.engine_name,'-','_')) IN (" + ",".join("%s" for _ in engine_filter) + ")"
            engine_parameters = engine_filter
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT r.tenant_id,r.run_id,r.session_id,r.fencing_token FROM runs r "
                "JOIN sessions s ON s.platform_id=r.platform_id AND s.tenant_id=r.tenant_id "
                "AND s.session_id=r.session_id WHERE r.platform_id=%s "
                "AND r.state IN ('STARTING','RUNNING','CANCELLING') "
                "AND r.lease_expires_at<=CURRENT_TIMESTAMP(6)" + engine_clause +
                " ORDER BY r.lease_expires_at,r.run_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (platform_id, *engine_parameters),
            )
            expired = cursor.fetchone()
            if expired is not None:
                tenant_id, expired_run_id, session_id, fencing_token = expired
                cursor.execute(
                    "UPDATE runs SET state='UNKNOWN', error_code='RUN_LEASE_EXPIRED_RESULT_UNKNOWN', "
                    "worker_id=NULL, lease_expires_at=NULL, fencing_token=fencing_token+1, "
                    "finished_at=CURRENT_TIMESTAMP(6) WHERE platform_id=%s AND tenant_id=%s "
                    "AND run_id=%s AND state IN ('STARTING','RUNNING','CANCELLING') AND fencing_token=%s "
                    "AND lease_expires_at<=CURRENT_TIMESTAMP(6)",
                    (platform_id, tenant_id, expired_run_id, int(fencing_token)),
                )
                if cursor.rowcount == 1:
                    identity = IdentityContext(
                        platform_id,
                        str(tenant_id),
                        worker_id,
                        frozenset(),
                        auth_source="worker",
                    )
                    self._quarantine_session_runtime_lease(
                        cursor, identity, str(session_id), str(expired_run_id)
                    )
                    self._touch_conversations_for_run(cursor, identity, str(expired_run_id))
                # 先收敛失租 Run；本轮不同时领取新 Run，防止遗漏不确定结果。
                return None

            cursor.execute(
                "SELECT r.tenant_id, r.run_id, r.session_id, r.fencing_token "
                "FROM runs r JOIN sessions s ON s.platform_id=r.platform_id "
                "AND s.tenant_id=r.tenant_id AND s.session_id=r.session_id "
                "LEFT JOIN session_runtime_leases l ON l.platform_id=r.platform_id "
                "AND l.tenant_id=r.tenant_id AND l.session_id=r.session_id "
                "WHERE r.platform_id=%s AND r.state='QUEUED' AND s.status='ACTIVE' "
                "AND l.session_id IS NULL" + engine_clause + " ORDER BY r.created_at, r.run_id "
                "LIMIT 1 FOR UPDATE SKIP LOCKED",
                (platform_id, *engine_parameters),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            tenant_id, run_id, session_id, fencing_token = row
            next_token = int(fencing_token) + 1
            identity = IdentityContext(
                platform_id,
                str(tenant_id),
                worker_id,
                frozenset(),
                auth_source="worker",
            )
            if not self._insert_session_runtime_lease(
                cursor,
                identity,
                str(session_id),
                str(run_id),
                worker_id,
                next_token,
                lease_seconds,
            ):
                return None
            cursor.execute(
                "UPDATE runs SET state='STARTING', worker_id=%s, fencing_token=fencing_token+1, "
                "lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='QUEUED' "
                "AND fencing_token=%s",
                (
                    worker_id,
                    lease_seconds,
                    platform_id,
                    tenant_id,
                    run_id,
                    int(fencing_token),
                ),
            )
            if cursor.rowcount != 1:
                cursor.execute(
                    "DELETE FROM session_runtime_leases WHERE platform_id=%s AND tenant_id=%s "
                    "AND session_id=%s AND run_id=%s AND fencing_token=%s AND state='ACTIVE'",
                    (platform_id, tenant_id, session_id, run_id, next_token),
                )
                return None
            self._touch_conversations_for_run(cursor, identity, str(run_id))
            return RunClaim(
                platform_id,
                str(tenant_id),
                str(run_id),
                worker_id,
                next_token,
            )

    def start_claimed_run(
        self, identity: IdentityContext, run_id: str, worker_id: str, fencing_token: int
    ) -> bool:
        """仅当前 claim 持有者可将尚未过期的 STARTING Run 转成 RUNNING。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT 1 FROM session_runtime_leases l JOIN sessions s "
                "ON s.platform_id=l.platform_id AND s.tenant_id=l.tenant_id "
                "AND s.session_id=l.session_id WHERE l.platform_id=%s AND l.tenant_id=%s "
                "AND l.run_id=%s AND l.worker_id=%s AND l.fencing_token=%s "
                "AND l.state='ACTIVE' AND l.lease_expires_at>CURRENT_TIMESTAMP(6) "
                "AND s.status='ACTIVE' FOR UPDATE",
                (identity.platform_id, identity.tenant_id, run_id, worker_id, fencing_token),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "UPDATE runs SET state='RUNNING', started_at=COALESCE(started_at,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s AND state='STARTING' "
                "AND worker_id=%s AND fencing_token=%s AND lease_expires_at>CURRENT_TIMESTAMP(6)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run_id,
                    worker_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._touch_conversations_for_run(cursor, identity, run_id)
            return True

    def is_run_lease_current(
        self, identity: IdentityContext, run_id: str, worker_id: str, fencing_token: int
    ) -> bool:
        """Runtime 出站前再确认 worker/token 仍持有未过期 RUNNING 租约。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT 1 FROM runs r JOIN session_runtime_leases l "
                "ON l.platform_id=r.platform_id AND l.tenant_id=r.tenant_id "
                "AND l.run_id=r.run_id AND l.fencing_token=r.fencing_token "
                "JOIN sessions s ON s.platform_id=l.platform_id AND s.tenant_id=l.tenant_id "
                "AND s.session_id=l.session_id "
                "WHERE r.platform_id=%s AND r.tenant_id=%s AND r.run_id=%s "
                "AND r.state='RUNNING' AND r.worker_id=%s AND r.fencing_token=%s "
                "AND r.lease_expires_at>CURRENT_TIMESTAMP(6) AND l.state='ACTIVE' "
                "AND l.worker_id=%s AND l.lease_expires_at>CURRENT_TIMESTAMP(6) "
                "AND s.status='ACTIVE'",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    run_id,
                    worker_id,
                    fencing_token,
                    worker_id,
                ),
            )
            return cursor.fetchone() is not None

    def get_run_input_ref(self, identity: IdentityContext, run_id: str) -> RunInputRef:
        """通过本租户 Run 关联的完整 USER Message 恢复对象引用，不返回正文。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT message_id, content_ref, content_sha256, byte_length FROM messages "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                "AND role='USER' AND status='COMPLETE' "
                "ORDER BY sequence_no DESC, message_id DESC LIMIT 1",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
        if row is None or row[1] is None or row[2] is None or row[3] is None:
            raise DomainError("RUN_INPUT_MISSING", "Run 没有关联完整的用户消息对象")
        return RunInputRef(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            int(row[3]),
        )

    def request_run_cancellation(self, identity: IdentityContext, run_id: str) -> str:
        """仅 Run 提交者可以取消；运行中的动作交由持租约 Worker 处理。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT state, submitted_by_user_id FROM runs "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("run")
            state = str(row[0])
            submitted_by = str(row[1]) if row[1] is not None else None
            if submitted_by is None or submitted_by != identity.user_id:
                raise DomainError("AUTHORIZATION_DENIED", "只有 Run 提交者可以请求取消")
            if state == "QUEUED":
                cursor.execute(
                    "UPDATE runs SET state='CANCELLED', error_code='SUBMITTER_CANCELLED', "
                    "finished_at=CURRENT_TIMESTAMP(6) WHERE platform_id=%s AND tenant_id=%s "
                    "AND run_id=%s AND state='QUEUED'",
                    (identity.platform_id, identity.tenant_id, run_id),
                )
                if cursor.rowcount != 1:
                    return state
                self._touch_conversations_for_run(cursor, identity, run_id)
                return "CANCELLED"
            if state in {"STARTING", "RUNNING"}:
                cursor.execute(
                    "UPDATE runs SET state='CANCELLING' WHERE platform_id=%s AND tenant_id=%s "
                    "AND run_id=%s AND state IN ('STARTING','RUNNING')",
                    (identity.platform_id, identity.tenant_id, run_id),
                )
                if cursor.rowcount != 1:
                    return state
                self._touch_conversations_for_run(cursor, identity, run_id)
                return "CANCELLING"
            return state

    def heartbeat(
        self,
        identity: IdentityContext,
        run_id: str,
        fencing_token: int,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ) -> bool:
        """续租只有当前 Worker 的 token 能成功，过期持有者得到 0 行影响。"""

        try:
            with self._transaction() as (_, cursor):
                worker_clause = " AND worker_id=%s" if worker_id is not None else ""
                worker_params = (worker_id,) if worker_id is not None else ()
                cursor.execute(
                    "UPDATE runs SET lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)) "
                    "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                    "AND state IN ('STARTING','RUNNING','CANCELLING') AND fencing_token=%s "
                    "AND lease_expires_at>CURRENT_TIMESTAMP(6)" + worker_clause,
                    (
                        lease_seconds,
                        identity.platform_id,
                        identity.tenant_id,
                        run_id,
                        fencing_token,
                        *worker_params,
                    ),
                )
                if cursor.rowcount != 1:
                    return False
                cursor.execute(
                    "UPDATE session_runtime_leases l JOIN runs r "
                    "ON r.platform_id=l.platform_id AND r.tenant_id=l.tenant_id "
                    "AND r.run_id=l.run_id JOIN sessions s "
                    "ON s.platform_id=l.platform_id AND s.tenant_id=l.tenant_id "
                    "AND s.session_id=l.session_id "
                    "SET l.lease_expires_at=TIMESTAMPADD(SECOND,%s,CURRENT_TIMESTAMP(6)) "
                    "WHERE r.platform_id=%s AND r.tenant_id=%s AND r.run_id=%s "
                    "AND r.fencing_token=%s AND l.fencing_token=%s AND l.state='ACTIVE' "
                    "AND l.lease_expires_at>CURRENT_TIMESTAMP(6) AND s.status='ACTIVE'"
                    + (" AND l.worker_id=%s" if worker_id is not None else ""),
                    (
                        lease_seconds,
                        identity.platform_id,
                        identity.tenant_id,
                        run_id,
                        fencing_token,
                        fencing_token,
                        *worker_params,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _SessionRuntimeLeaseLost("Run 没有有效的 Session Home lease")
                return True
        except _SessionRuntimeLeaseLost:
            return False

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

    @staticmethod
    def _insert_assistant_message(
        cursor: Any,
        identity: IdentityContext,
        run: RunRecord,
        message: AssistantMessageRef,
    ) -> None:
        """在 Run 终态事务内追加助手消息；同一 Run 重入时复用已有行。"""

        if (
            message.message_id != run.run_id
            or message.conversation_id.strip() == ""
            or message.sender_id.strip() == ""
            or message.content_ref.strip() == ""
            or len(message.content_sha256) != 64
            or message.byte_length <= 0
            or message.content_type.strip() == ""
        ):
            raise DomainError("INVALID_ARGUMENT", "助手消息引用不完整")
        cursor.execute(
            "SELECT message_id, conversation_id, platform_id, tenant_id, sender_id, source, "
            "content_ref, content_sha256, byte_length, content_type, sequence_no, routing_depth, created_at "
            "FROM messages WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
            "AND role='ASSISTANT' AND source='agent' LIMIT 1 FOR UPDATE",
            (identity.platform_id, identity.tenant_id, run.run_id),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(
            "SELECT 1 FROM conversations WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
            (identity.platform_id, identity.tenant_id, message.conversation_id),
        )
        if cursor.fetchone() is None:
            raise DomainError("RESOURCE_NOT_FOUND", "助手消息所属 Conversation 不存在")
        cursor.execute(
            "SELECT last_message_sequence FROM sessions "
            "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s FOR UPDATE",
            (identity.platform_id, identity.tenant_id, run.session_id),
        )
        session = cursor.fetchone()
        if session is None:
            raise DomainError("RESOURCE_NOT_FOUND", "助手消息所属 Session 不存在")
        sequence = int(session[0]) + 1
        cursor.execute(
            "INSERT INTO messages "
            "(platform_id,tenant_id,message_id,session_id,run_id,sequence_no,role,status,"
            "content_ref,content_sha256,byte_length,content_type,sender_id,source,routing_depth,"
            "conversation_id,retention_policy) "
            "VALUES (%s,%s,%s,%s,%s,%s,'ASSISTANT','COMPLETE',%s,%s,%s,%s,%s,'agent',0,%s,'conversation')",
            (
                identity.platform_id,
                identity.tenant_id,
                message.message_id,
                run.session_id,
                run.run_id,
                sequence,
                message.content_ref,
                message.content_sha256,
                message.byte_length,
                message.content_type,
                message.sender_id,
                message.conversation_id,
            ),
        )
        cursor.execute(
            "UPDATE sessions SET last_message_sequence=%s WHERE platform_id=%s "
            "AND tenant_id=%s AND session_id=%s",
            (sequence, identity.platform_id, identity.tenant_id, run.session_id),
        )
        cursor.execute(
            "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP(6) "
            "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
            (identity.platform_id, identity.tenant_id, message.conversation_id),
        )

    def complete_run_with_assistant_message(
        self,
        identity: IdentityContext,
        run: RunRecord,
        fencing_token: int,
        spans: Sequence[TraceSpan],
        usage: UsageRecord | None,
        assistant_message: AssistantMessageRef,
    ) -> bool:
        """以兼容端口保留旧仓储实现，同时把助手消息纳入同一终态事务。"""

        return self.complete_run(
            identity,
            run,
            fencing_token,
            spans,
            usage,
            assistant_message=assistant_message,
        )

    def complete_run(
        self,
        identity: IdentityContext,
        run: RunRecord,
        fencing_token: int,
        spans: Sequence[TraceSpan],
        usage: UsageRecord | None,
        assistant_message: AssistantMessageRef | None = None,
    ) -> bool:
        """在同一事务提交 Run 终态、Trace 索引、Usage 台账和 Runtime 结果引用。"""

        runtime = run.runtime_reference
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE runs SET state=%s, error_code=%s, result_ref=%s, runtime_session_ref=%s, "
                "runtime_result_ref=%s, lease_expires_at=NULL, "
                "fencing_token=CASE WHEN %s='UNKNOWN' THEN fencing_token+1 ELSE fencing_token END, "
                "finished_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                "AND state IN ('RUNNING','CANCELLING') AND fencing_token=%s",
                (
                    run.status.value.upper(),
                    run.error_category,
                    run.result_ref,
                    runtime.runtime_session_ref if runtime else None,
                    runtime.runtime_result_ref if runtime else None,
                    run.status.value.upper(),
                    identity.platform_id,
                    identity.tenant_id,
                    run.run_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._touch_conversations_for_run(cursor, identity, run.run_id)
            if assistant_message is not None:
                self._insert_assistant_message(cursor, identity, run, assistant_message)
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
                    "provider_request_id, input_tokens, output_tokens, cached_input_tokens, reasoning_tokens, total_tokens, cost_amount, currency, pricing_version, "
                    "usage_status, estimation_method) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        identity.platform_id,
                        identity.tenant_id,
                        f"model-{run.run_id}",
                        run.run_id,
                        run.session_id,
                        run.submitted_by_user_id or identity.user_id,
                        run.snapshot.employee_id,
                        run.snapshot.model_route_id,
                        usage.provider_request_id,
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
            self._inject_failure("after_usage")
            if run.status is RuntimeStatus.UNKNOWN:
                self._quarantine_session_runtime_lease(
                    cursor, identity, run.session_id, run.run_id
                )
            elif not self._release_session_runtime_lease(
                cursor, identity, run.run_id, fencing_token
            ):
                raise _SessionRuntimeLeaseLost("当前 Run 没有可释放的 Session Home lease")
            return True

    def mark_unknown(self, identity: IdentityContext, run_id: str, fencing_token: int) -> bool:
        """Runtime 调用结果不明时终止并递增 token，拒绝迟到的 Worker 结果。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE runs SET state='UNKNOWN', error_code='RUNTIME_RESULT_UNKNOWN', worker_id=NULL, "
                "lease_expires_at=NULL, fencing_token=fencing_token+1, finished_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                "AND state IN ('STARTING','RUNNING','CANCELLING') AND fencing_token=%s",
                (identity.platform_id, identity.tenant_id, run_id, fencing_token),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                "SELECT session_id FROM runs WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            lease_row = cursor.fetchone()
            if lease_row is not None:
                self._quarantine_session_runtime_lease(
                    cursor, identity, str(lease_row[0]), run_id
                )
            self._touch_conversations_for_run(cursor, identity, run_id)
            return True

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
                "SELECT input_tokens, output_tokens, reasoning_tokens, total_tokens, usage_status, estimation_method, "
                "cost_amount, currency, pricing_version, provider_request_id FROM usage_ledger "
                "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s ORDER BY call_id LIMIT 1",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
        if row is None:
            return UsageRecord(0, 0, 0, source="UNAVAILABLE", estimated=True)
        status = UsageStatus(str(row[4]))
        return UsageRecord(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            total_tokens=int(row[3]),
            reasoning_tokens=int(row[2]),
            source=str(row[5] or status.value),
            estimated=status is not UsageStatus.EXACT,
            cost_amount=float(row[6]),
            currency=str(row[7]),
            pricing_version=str(row[8]),
            provider_request_id=str(row[9]) if row[9] is not None else None,
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
                "SELECT runs.run_id, runs.session_id, runs.platform_id, runs.tenant_id, runs.trace_id, "
                "runs.request_digest, runs.state, runs.error_code, runs.snapshot_json, "
                "runs.context_snapshot_json, runs.runtime_session_ref, runs.runtime_result_ref, "
                "runs.result_ref, runs.submitted_by_user_id, sessions.engine_name, "
                "runs.orchestration_id, runs.task_id, runs.attempt_no, runs.worker_id, runs.fencing_token "
                "FROM runs JOIN sessions ON sessions.platform_id=runs.platform_id "
                "AND sessions.tenant_id=runs.tenant_id AND sessions.session_id=runs.session_id "
                "WHERE runs.platform_id=%s AND runs.tenant_id=%s AND runs.run_id=%s",
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
            engine_name=(
                str(snapshot_data["engine_name"])
                if snapshot_data.get("engine_name") is not None
                else None
            ),
            execution_profile_version=int(snapshot_data["execution_profile_version"]),
            model_route_id=str(snapshot_data["model_route_id"]) if snapshot_data.get("model_route_id") else None,
            model_alias=(
                str(snapshot_data["model_alias"])
                if snapshot_data.get("model_alias") is not None
                else None
            ),
            model_route_snapshot=(
                ModelRouteSnapshot.from_snapshot_dict(snapshot_data["model_route_snapshot"])
                if isinstance(snapshot_data.get("model_route_snapshot"), dict)
                else None
            ),
            runtime_config_version=(
                int(snapshot_data["runtime_config_version"])
                if snapshot_data.get("runtime_config_version") is not None
                else None
            ),
            runtime_profile_source=(
                str(snapshot_data["runtime_profile_source"])
                if snapshot_data.get("runtime_profile_source") is not None
                else None
            ),
            runtime_model_route_source=(
                str(snapshot_data["runtime_model_route_source"])
                if snapshot_data.get("runtime_model_route_source") is not None
                else None
            ),
            runtime_options=RuntimeOptions.from_mapping(
                snapshot_data.get("runtime_options", {})
            ).resolve(),
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
            RuntimeReference(
                str(row[14] or "fake"),
                runtime_session_ref,
                str(row[11]) if row[11] else None,
            )
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
            submitted_by_user_id=str(row[13]) if row[13] is not None else None,
            orchestration_id=str(row[15]) if row[15] is not None else None,
            task_id=str(row[16]) if row[16] is not None else None,
            attempt_no=int(row[17]) if row[17] is not None else None,
            worker_id=str(row[18]) if row[18] is not None else None,
            fencing_token=int(row[19]),
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
