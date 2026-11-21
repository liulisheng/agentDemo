"""把目录部署、Session、Run、Runtime 和 Trace 串成最小同步闭环。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import Lock

from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.context import BusinessContextAssembler
from agent_platform.application.resolvers import AgentResolver, ResolvedAgent
from agent_platform.domain.catalog import (
    Deployment,
    LifecycleStatus,
    compute_tool_input_schema_sha256,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.context import BusinessContextPolicy
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.mcp import McpProfileStatus, McpRiskLevel, ToolApprovalRequest
from agent_platform.domain.observability import (
    RunRecord,
    RunTimeline,
    SessionRecord,
    SpanType,
    TraceSpan,
)
from agent_platform.ports.conversation_repository import ConversationRepository
from agent_platform.ports.execution_repository import ExecutionRepository
from agent_platform.ports.mcp import McpRepository
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.runtime import (
    RuntimeAdapter,
    RuntimeEmployeeContext,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)
from agent_platform.ports.storage import ObjectStore, UploadRequest


class ControlPlaneService:
    """同步执行本地 Fake Runtime，并保存可查询的租户级 Trace。"""

    def __init__(
        self,
        catalog: CatalogService,
        runtime: RuntimeAdapter,
        persistence: ExecutionRepository | None = None,
        object_store: ObjectStore | None = None,
        resource_center_repository: ResourceCenterRepository | None = None,
        mcp_repository: McpRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
    ) -> None:
        self.catalog = catalog
        self.runtime = runtime
        self.persistence = persistence
        self.object_store = object_store
        self.mcp_repository = mcp_repository
        self.conversation_repository = conversation_repository
        self.agent_resolver = AgentResolver(
            catalog,
            resource_center_repository,
            object_store=object_store,
            mcp_repository=mcp_repository,
        )
        self.context_assembler = BusinessContextAssembler(BusinessContextPolicy())
        self._sessions: dict[tuple[str, str, str], SessionRecord] = {}
        self._runs: dict[tuple[str, str, str], RunRecord] = {}
        self._spans: dict[str, list[TraceSpan]] = {}
        self._usage: dict[str, UsageRecord] = {}
        self._idempotency: dict[tuple[str, str, str, str], str] = {}
        self._local_submission_locks: dict[tuple[str, str, str, str], tuple[Lock, int]] = {}
        self._local_submission_locks_guard = Lock()

    @staticmethod
    def _scope(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
        return identity.platform_id, identity.tenant_id, resource_id

    @staticmethod
    def _new_id(prefix: str) -> str:
        del prefix  # 存储主键不编码资源类型，保持与 V001/V002 CHAR(26) 兼容。
        return generate_database_id()

    def _get_session(self, identity: IdentityContext, session_id: str) -> SessionRecord:
        try:
            return self._sessions[self._scope(identity, session_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": "session"}) from error

    def _get_run(self, identity: IdentityContext, run_id: str) -> RunRecord:
        try:
            return self._runs[self._scope(identity, run_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": "run"}) from error

    def _append_span(
        self,
        trace_id: str,
        *,
        run_id: str,
        span_type: SpanType,
        status: str,
        parent_span_id: str | None,
        attributes: dict[str, object] | None = None,
    ) -> TraceSpan:
        spans = self._spans.setdefault(trace_id, [])
        span = TraceSpan(
            span_id=self._new_id("span"),
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            run_id=run_id,
            span_type=span_type,
            status=status,
            attributes=attributes or {},
            sequence=len(spans) + 1,
        )
        spans.append(span)
        return span

    def _require_active_deployment(self, identity: IdentityContext, deployment_id: str) -> Deployment:
        deployment = self.catalog.resolve_deployment(identity, deployment_id)
        # 只用部署状态准入。固定版本下线后，已上线的部署仍可运行。
        if deployment.status is LifecycleStatus.DISABLED:
            raise DomainError("DEPLOYMENT_DISABLED", "部署已下线", {"deployment_id": deployment_id})
        if deployment.status is not LifecycleStatus.ACTIVE:
            raise DomainError("INVALID_STATE", "部署当前未上线")
        return deployment

    def create_session(self, identity: IdentityContext, deployment_id: str) -> SessionRecord:
        deployment = self._require_active_deployment(identity, deployment_id)
        profile = self.catalog.repository.get_profile_version(
            identity,
            deployment.employee_version.execution_profile_id,
            deployment.employee_version.execution_profile_version,
        )
        session_id = self._new_id("session")
        trace_id = self._new_id("trace")
        session = SessionRecord(
            session_id=session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            deployment_id=deployment.deployment_id,
            trace_id=trace_id,
            employee_version=deployment.employee_version.version,
            employee_id=deployment.employee_version.employee_id,
            execution_profile_id=deployment.employee_version.execution_profile_id,
            execution_profile_version=deployment.employee_version.execution_profile_version,
            model_route_id=deployment.employee_version.model_route_id or profile.model_route_id,
            harness_home_ref=(
                "harness-session:"
                + hashlib.sha256(
                    f"{identity.platform_id}\0{identity.tenant_id}\0{session_id}".encode()
                ).hexdigest()
            ),
        )
        session_span = self._append_span(
            trace_id,
            run_id="",
            span_type=SpanType.SESSION,
            status="started",
            parent_span_id=None,
            attributes={
                "session_id": session_id,
                "deployment_id": deployment_id,
                "employee_version": deployment.employee_version.version,
            },
        )
        session.span_id = session_span.span_id
        self._sessions[self._scope(identity, session_id)] = session
        if self.persistence is not None and hasattr(self.persistence, "save_session"):
            self.persistence.save_session(identity, session, engine_name=profile.engine_name)
        elif self.persistence is not None and hasattr(self.persistence, "register_session"):
            self.persistence.register_session(identity, session_id, deployment_id, trace_id)
        return session

    def _event_to_span(
        self,
        trace_id: str,
        run: RunRecord,
        event: RuntimeEvent,
    ) -> TraceSpan | None:
        if event.event_type in {"run_started", "run_finished"}:
            return None
        type_by_name = {
            "TOOL": SpanType.TOOL,
            "RUNTIME": SpanType.RUNTIME,
            "MODEL": SpanType.MODEL,
            "RESULT": SpanType.RESULT,
        }
        span_type = type_by_name.get(event.span_type, SpanType.RUNTIME)
        if event.event_type == "tool/call":
            status = "running"
        elif event.event_type == "tool/result":
            data = event.payload.get("data", event.payload)
            message = data.get("message", {}) if isinstance(data, Mapping) else {}
            status = "failed" if isinstance(message, Mapping) and message.get("isError") else "succeeded"
        else:
            status = "failed" if event.event_type == "tool_denied" else "succeeded"
        attributes = self._trace_event_attributes(event)
        return self._append_span(
            trace_id,
            run_id=run.run_id,
            span_type=span_type,
            status=status,
            parent_span_id=run.span_id,
            attributes=attributes,
        )

    @staticmethod
    def _trace_event_attributes(event: RuntimeEvent) -> dict[str, object]:
        """只把可审计元数据写入 Trace；调用参数和工具正文留在内存供审批处理。"""

        payload = event.payload
        data = payload.get("data", payload)
        if not isinstance(data, Mapping):
            return {"event_type": event.event_type}

        attributes: dict[str, object] = {"event_type": event.event_type}
        for key in ("turn", "step", "source", "role", "provider", "model", "reason"):
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)):
                attributes[key] = value

        if event.event_type == "tool/call":
            name = data.get("name")
            call_id = data.get("callId")
            if isinstance(name, str):
                attributes["tool_name"] = name
            if isinstance(call_id, str):
                attributes["call_id"] = call_id
            arguments = data.get("arguments")
            if arguments is not None:
                try:
                    normalized = (
                        json.loads(arguments)
                        if isinstance(arguments, str)
                        else arguments
                    )
                    canonical = json.dumps(
                        normalized,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                except (TypeError, ValueError, json.JSONDecodeError):
                    canonical = str(arguments).encode("utf-8")
                attributes["arguments_sha256"] = hashlib.sha256(canonical).hexdigest()
                attributes["arguments_size_bytes"] = len(canonical)
            return attributes

        if event.event_type == "tool/result":
            message = data.get("message", {})
            if not isinstance(message, Mapping):
                message = {}
            call_id = message.get("toolCallId")
            if isinstance(call_id, str):
                attributes["tool_call_id"] = call_id
            is_error = message.get("isError")
            if isinstance(is_error, bool):
                attributes["is_error"] = is_error
            content = message.get("content")
            if isinstance(content, str):
                result_bytes = content.encode("utf-8")
            else:
                text_parts = [
                    str(item.get("text"))
                    for item in content
                    if isinstance(item, Mapping)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ] if isinstance(content, list) else []
                result_bytes = (
                    "\n".join(text_parts).encode("utf-8")
                    if text_parts
                    else json.dumps(
                        content,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                )
            attributes["result_sha256"] = hashlib.sha256(result_bytes).hexdigest()
            attributes["result_size_bytes"] = len(result_bytes)
            return attributes

        for key in ("tool_name", "error_category", "error_code", "call_id", "status"):
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)):
                attributes[key] = value
        return attributes

    def _create_approval_from_preview(
        self,
        identity: IdentityContext,
        run: RunRecord,
        session: SessionRecord,
        tool_definitions: tuple[object, ...],
        events: tuple[RuntimeEvent, ...],
        trace_spans: tuple[TraceSpan, ...],
        usage: UsageRecord,
        runtime_reference: RuntimeReference,
        *,
        run_fencing_token: int,
        result_ref: str | None = None,
    ) -> ToolApprovalRequest | None:
        """把成功 PREVIEW 的结果固定为加密审批对象，不在 Harness 内暴露 WRITE 工具。"""

        if (
            self.mcp_repository is None
            or self.conversation_repository is None
            or self.object_store is None
            or run.submitted_by_user_id is None
        ):
            return None

        calls: dict[str, tuple[object, dict[str, object]]] = {}
        for event in events:
            if event.event_type != "tool/call":
                continue
            data = event.payload.get("data", event.payload)
            if not isinstance(data, Mapping):
                continue
            call_id = data.get("callId")
            reported_name = data.get("name")
            if not isinstance(call_id, str) or not isinstance(reported_name, str):
                continue
            matching = [
                definition
                for definition in tool_definitions
                if getattr(definition, "remote_tool_name", None)
                and (
                    reported_name == definition.remote_tool_name
                    or reported_name.endswith("__" + str(definition.remote_tool_name))
                )
            ]
            if len(matching) != 1:
                continue
            raw_arguments = data.get("arguments", {})
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except json.JSONDecodeError:
                continue
            if isinstance(arguments, dict):
                calls[call_id] = (matching[0], arguments)

        for event in events:
            if event.event_type != "tool/result":
                continue
            data = event.payload.get("data", event.payload)
            if not isinstance(data, Mapping):
                continue
            message = data.get("message", {})
            if not isinstance(message, Mapping) or message.get("isError") is True:
                continue
            call_id = message.get("toolCallId")
            call = calls.get(call_id) if isinstance(call_id, str) else None
            if call is None:
                continue
            definition, preview_arguments = call
            preview_tool = self.catalog.repository.get_tool_version(
                identity, str(definition.tool_id), int(definition.tool_version)
            )
            if (
                preview_tool.protocol != "mcp"
                or preview_tool.mcp_risk_level is not McpRiskLevel.PREVIEW
                or preview_tool.remote_tool_name is None
                or preview_tool.mcp_server_profile_id is None
                or preview_tool.mcp_server_profile_version is None
            ):
                continue

            content = message.get("content", [])
            text_parts = [
                item["text"]
                for item in content
                if isinstance(item, Mapping)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ] if isinstance(content, list) else []
            try:
                preview_result = json.loads("\n".join(text_parts))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(preview_result, dict):
                continue
            confirmation_code = preview_result.get("confirmation_code")
            if not isinstance(confirmation_code, str) or not confirmation_code.strip():
                continue

            write_tools = []
            for tool_id, tool_version in run.snapshot.tool_version_refs:
                candidate = self.catalog.repository.get_tool_version(identity, tool_id, tool_version)
                if (
                    candidate.status is LifecycleStatus.PUBLISHED
                    and candidate.protocol == "mcp"
                    and candidate.mcp_risk_level is McpRiskLevel.WRITE
                    and candidate.preview_tool_name == preview_tool.remote_tool_name
                    and candidate.mcp_server_profile_id == preview_tool.mcp_server_profile_id
                    and candidate.mcp_server_profile_version == preview_tool.mcp_server_profile_version
                ):
                    write_tools.append(candidate)
            if len(write_tools) != 1:
                raise DomainError("MCP_CATALOG_DRIFT", "PREVIEW 未唯一绑定到已发布 WRITE ToolVersion")
            write_tool = write_tools[0]
            profile = self.mcp_repository.get_profile(
                identity,
                str(write_tool.mcp_server_profile_id),
                int(write_tool.mcp_server_profile_version),
            )
            if (
                profile.status is not McpProfileStatus.ACTIVE
                or write_tool.remote_tool_name not in profile.enabled_tool_names
                or write_tool.input_schema_sha256
                != compute_tool_input_schema_sha256(write_tool.input_schema)
            ):
                raise DomainError("MCP_CATALOG_DRIFT", "WRITE ToolVersion 与活动 MCP Profile 不一致")

            write_arguments = dict(preview_arguments)
            write_arguments["confirmation_code"] = confirmation_code
            required_fields = write_tool.input_schema.get("required", [])
            if not isinstance(required_fields, list) or any(
                not isinstance(name, str) or name not in write_arguments
                for name in required_fields
            ):
                raise DomainError("MCP_PREVIEW_ARGUMENT_MISMATCH", "PREVIEW 参数不足以生成完整 WRITE 参数")

            preview_payload = preview_result.get("preview", preview_result)
            safe_preview = self._sanitize_approval_preview(preview_payload, confirmation_code)
            preview_document = {
                "preview": safe_preview,
                "writes_performed": preview_result.get("writes_performed", False),
            }
            preview_bytes = self._canonical_json(preview_document)
            arguments_bytes = self._canonical_json(write_arguments)
            if not bool(getattr(self.object_store, "encryption_enabled", False)):
                raise DomainError(
                    "OBJECT_ENCRYPTION_REQUIRED",
                    "MCP 写操作审批参数与预览必须保存到启用静态加密的对象存储",
                )
            arguments_ref, arguments_digest = self._store_approval_object(
                identity, f"approval-{run.run_id}-arguments.json", arguments_bytes
            )
            preview_ref, preview_digest = self._store_approval_object(
                identity, f"approval-{run.run_id}-preview.json", preview_bytes
            )
            conversation_id = self.conversation_repository.get_conversation_id_for_run(
                identity, run.run_id
            )
            now = datetime.now(UTC)
            request = ToolApprovalRequest(
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                approval_id=self._new_id("approval"),
                run_id=run.run_id,
                session_id=session.session_id,
                conversation_id=conversation_id,
                requested_by=run.submitted_by_user_id,
                tool_invocation_id=next(
                    span.span_id
                    for span in trace_spans
                    if span.span_type is SpanType.TOOL
                    and span.attributes.get("call_id") == call_id
                    and span.attributes.get("event_type") == "tool/call"
                ),
                tool_id=write_tool.tool_id,
                tool_version=write_tool.version,
                mcp_server_profile_id=write_tool.mcp_server_profile_id,
                mcp_server_profile_version=write_tool.mcp_server_profile_version,
                arguments_ref=arguments_ref,
                arguments_sha256=arguments_digest,
                preview_ref=preview_ref,
                preview_sha256=preview_digest,
                expires_at=now + timedelta(minutes=15),
            )
            created = self.mcp_repository.create_tool_approval(
                identity,
                request,
                run_fencing_token=run_fencing_token,
                trace_spans=trace_spans,
                usage=usage,
                runtime_reference=runtime_reference,
                result_ref=result_ref,
            )
            run.status = RuntimeStatus.AWAITING_APPROVAL
            return created
        return None

    def _store_approval_object(
        self, identity: IdentityContext, filename: str, content: bytes
    ) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        upload = self.object_store.create_upload_intent(
            UploadRequest(
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                filename=filename,
                mime_type="application/json",
                size_bytes=len(content),
                retention_policy="session",
                expected_sha256=digest,
            )
        )
        asset = self.object_store.complete_upload(upload.upload_id, content)
        return asset.object_ref, digest

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def _sanitize_approval_preview(cls, value: object, confirmation_code: str) -> object:
        """移除确认码和任何疑似凭据字段后，才生成用户可见预览对象。"""

        sensitive_markers = (
            "confirmation_code",
            "password",
            "secret",
            "token",
            "credential",
            "authorization",
            "api_key",
        )
        if isinstance(value, Mapping):
            return {
                str(key): cls._sanitize_approval_preview(item, confirmation_code)
                for key, item in value.items()
                if not any(marker in str(key).lower() for marker in sensitive_markers)
            }
        if isinstance(value, list):
            return [cls._sanitize_approval_preview(item, confirmation_code) for item in value]
        if isinstance(value, str) and confirmation_code:
            return value.replace(confirmation_code, "[已隐藏]")
        return value

    def _get_idempotent_run(
        self,
        identity: IdentityContext,
        session_id: str,
        idempotency_key: str | None,
        prompt_digest: str,
    ) -> RunRecord | None:
        if not idempotency_key:
            return None
        local_run_id = self._idempotency.get((*self._scope(identity, session_id), idempotency_key))
        if local_run_id is not None:
            previous = self._get_run(identity, local_run_id)
        elif self.persistence is not None and hasattr(self.persistence, "get_run_by_idempotency"):
            previous = self.persistence.get_run_by_idempotency(identity, session_id, idempotency_key)
        else:
            return None
        if previous is None:
            return None
        if previous.prompt_digest != prompt_digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
        return previous if local_run_id is not None else self._load_result(identity, previous)

    @contextmanager
    def _serialize_local_submission(self, key: tuple[str, str, str, str]) -> Iterator[None]:
        # 总锁只保护锁表。登记等候者后才等单键锁，并在最后一个请求退出时清理。
        with self._local_submission_locks_guard:
            entry = self._local_submission_locks.get(key)
            lock, users = entry if entry is not None else (Lock(), 0)
            self._local_submission_locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._local_submission_locks_guard:
                _, users = self._local_submission_locks[key]
                if users == 1:
                    del self._local_submission_locks[key]
                else:
                    self._local_submission_locks[key] = (lock, users - 1)

    def create_run(
        self,
        identity: IdentityContext,
        session_id: str,
        prompt: str,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        """提交新运行或按当前租户和会话的幂等键返回原运行。"""

        if not prompt.strip():
            raise DomainError("INVALID_ARGUMENT", "prompt 不能为空")
        session = self.get_session(identity, session_id)
        persistent_submission = self.persistence is not None and hasattr(self.persistence, "submit_run")
        local_key = None
        if idempotency_key and not persistent_submission:
            local_key = (*self._scope(identity, session_id), idempotency_key)
        guard = self._serialize_local_submission(local_key) if local_key is not None else nullcontext()
        with guard:
            prepared = self._prepare_run(identity, session, prompt, idempotency_key)
            if isinstance(prepared, RunRecord):
                return prepared
            run, resolved_agent = prepared
            run_key = self._scope(identity, run.run_id)
            if local_key is not None:
                # 发布独立的运行中记录，重放不会读到执行线程逐步填充的结果。
                self._runs[run_key] = replace(run, status=RuntimeStatus.RUNNING)
                self._idempotency[local_key] = run.run_id

        try:
            return self._execute_run(
                identity,
                session,
                prompt,
                idempotency_key,
                run,
                resolved_agent,
                persistent_submission=persistent_submission,
            )
        except Exception:
            if local_key is not None and self._runs[run_key].status is RuntimeStatus.RUNNING:
                # 已登记的运行若在准备调用或整理结果时异常，也保留原幂等记录。
                self._runs[run_key] = replace(
                    run, status=RuntimeStatus.UNKNOWN, error_category="RUNTIME_RESULT_UNKNOWN"
                )
            raise

    def prepare_conversation_turn(
        self,
        identity: IdentityContext,
        session_id: str,
        conversation_id: str,
        prompt: str,
    ) -> RunRecord:
        """基于固定 Session/Deployment 快照生成 QUEUED Run，不启动 Runtime。"""

        if not prompt.strip() or not conversation_id.strip():
            raise DomainError("INVALID_ARGUMENT", "conversation_id 和 turn 文本不能为空")
        session = self.get_session(identity, session_id)
        if session.status != "ACTIVE":
            raise DomainError("INVALID_STATE", "Session 不是 ACTIVE 状态")
        deployment = self._require_active_deployment(identity, session.deployment_id)
        resolved_agent = self.agent_resolver.resolve(identity, deployment)
        snapshot_data = asdict(resolved_agent.snapshot)
        request_payload = {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "submitted_by_user_id": identity.user_id,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "snapshot": snapshot_data,
        }
        canonical_request = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        run = RunRecord(
            run_id=self._new_id("run"),
            session_id=session.session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            submitted_by_user_id=identity.user_id,
            trace_id=session.trace_id,
            prompt_digest=hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            snapshot=resolved_agent.snapshot,
            status=RuntimeStatus.QUEUED,
        )
        run.context_snapshot = self.context_assembler.assemble(
            run_id=run.run_id,
            trace_id=session.trace_id,
            agent_ref=f"{run.snapshot.employee_id}:v{run.snapshot.employee_version}",
            skill_refs=run.snapshot.skill_version_ids,
            recent_messages=(),
            memory_refs=(),
            tool_result_refs=(),
        )
        return run

    def queue_prepared_run(
        self, identity: IdentityContext, run: RunRecord, idempotency_key: str
    ) -> RunRecord:
        """Fake 模式的本地排队边界；MySQL 必须使用含 Message 的联合事务。"""

        if self.persistence is not None:
            raise DomainError(
                "INVALID_STATE",
                "持久化 Conversation turn 必须通过 Message/Run 联合事务提交",
            )
        if not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 不能为空")
        if (
            run.platform_id != identity.platform_id
            or run.tenant_id != identity.tenant_id
            or run.submitted_by_user_id != identity.user_id
            or run.status is not RuntimeStatus.QUEUED
        ):
            raise DomainError("AUTHORIZATION_DENIED", "Run 快照与当前身份不一致")
        key = (*self._scope(identity, run.session_id), idempotency_key)
        with self._serialize_local_submission(key):
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                existing = self._get_run(identity, existing_id)
                if existing.prompt_digest != run.prompt_digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
                return existing
            self._runs[self._scope(identity, run.run_id)] = run
            self._idempotency[key] = run.run_id
            return run

    def _prepare_run(
        self,
        identity: IdentityContext,
        session: SessionRecord,
        prompt: str,
        idempotency_key: str | None,
    ) -> RunRecord | tuple[RunRecord, ResolvedAgent]:
        session_id = session.session_id
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        # 已提交请求按原摘要重放；只有新请求需要通过当前部署准入。
        previous = self._get_idempotent_run(identity, session_id, idempotency_key, prompt_digest)
        if previous is not None:
            return previous
        if session.status != "ACTIVE":
            raise DomainError("INVALID_STATE", "Session 不是 ACTIVE 状态")
        try:
            deployment = self._require_active_deployment(identity, session.deployment_id)
        except DomainError as error:
            if error.code != "DEPLOYMENT_DISABLED":
                raise
            # 首次查询与下线之间可能已有同键请求提交，拒绝前用新读取确认。
            previous = self._get_idempotent_run(identity, session_id, idempotency_key, prompt_digest)
            if previous is not None:
                return previous
            raise

        resolved_agent = self.agent_resolver.resolve(identity, deployment)
        snapshot = resolved_agent.snapshot
        run = RunRecord(
            run_id=self._new_id("run"),
            session_id=session.session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            submitted_by_user_id=identity.user_id,
            trace_id=session.trace_id,
            prompt_digest=prompt_digest,
            snapshot=snapshot,
            status=RuntimeStatus.UNKNOWN,
        )
        run.context_snapshot = self.context_assembler.assemble(
            run_id=run.run_id,
            trace_id=session.trace_id,
            agent_ref=f"{snapshot.employee_id}:v{snapshot.employee_version}",
            skill_refs=snapshot.skill_version_ids,
            recent_messages=(),
            memory_refs=(),
            tool_result_refs=(),
        )
        return run, resolved_agent

    def _execute_run(
        self,
        identity: IdentityContext,
        session: SessionRecord,
        prompt: str,
        idempotency_key: str | None,
        run: RunRecord,
        resolved_agent: ResolvedAgent,
        *,
        persistent_submission: bool,
        claimed_fencing_token: int | None = None,
        worker_id: str | None = None,
    ) -> RunRecord:
        session_id = session.session_id
        snapshot = run.snapshot
        context_snapshot = run.context_snapshot
        assert context_snapshot is not None
        persistent_key = idempotency_key or run.run_id
        fencing_token: int | None = None
        if persistent_submission:
            if claimed_fencing_token is not None:
                # 独立 Worker 已经原子 claim；同步入口仍自行提交并取得租约。
                fencing_token = claimed_fencing_token
            else:
                existing_run, created = self.persistence.submit_run(identity, run, persistent_key)
                if not created:
                    # 其他请求可能仍在执行，后续重放继续从仓储读取最新状态。
                    return self._load_result(identity, existing_run)
                fencing_token = self.persistence.acquire_lease(
                    identity,
                    run.run_id,
                    worker_id or generate_database_id(),
                    lease_seconds=30,
                )
                if fencing_token is None:
                    return self.persistence.get_run(identity, run.run_id)

        run_span = self._append_span(
            session.trace_id,
            run_id=run.run_id,
            span_type=SpanType.RUN,
            status="running",
            parent_span_id=session.span_id,
            attributes={"employee_version": snapshot.employee_version},
        )
        run.span_id = run_span.span_id
        file_package_by_skill = {
            package.skill_id: package for package in snapshot.skill_file_packages
        }
        for skill_id in snapshot.skill_version_ids:
            file_package = file_package_by_skill.get(skill_id)
            self._append_span(
                session.trace_id,
                run_id=run.run_id,
                span_type=SpanType.SKILL,
                status="selected",
                parent_span_id=run.span_id,
                attributes={
                    "skill_id": skill_id,
                    **(
                        {
                            "file_manifest_sha256": file_package.manifest_sha256,
                            "file_count": len(file_package.files),
                            "entry_path": file_package.entry_path,
                        }
                        if file_package is not None
                        else {}
                    ),
                },
            )
        self._append_span(
            session.trace_id,
            run_id=run.run_id,
            span_type=SpanType.CONTEXT,
            status="assembled",
            parent_span_id=run.span_id,
            attributes={
                "snapshot_id": context_snapshot.snapshot_id,
                "total_tokens": context_snapshot.total_tokens,
            },
        )
        try:
            result = self.runtime.run(
                RuntimeRequest(
                    run_id=run.run_id,
                    session_ref=session.runtime_session_ref,
                    harness_home_ref=session.harness_home_ref,
                    prompt=prompt,
                    skill_instructions=resolved_agent.skill_instructions,
                    allowed_tools=resolved_agent.allowed_tools,
                    system_prompt=resolved_agent.system_prompt,
                    skill_file_packages=resolved_agent.skill_file_packages,
                    employee_context=RuntimeEmployeeContext(
                        employee_id=snapshot.employee_id,
                        employee_version=snapshot.employee_version,
                        system_prompt=resolved_agent.system_prompt,
                        skill_refs=snapshot.skill_version_refs,
                        skill_instructions=resolved_agent.skill_instructions,
                        skill_files=resolved_agent.skill_file_contents,
                        model_route_id=snapshot.model_route_id,
                    ),
                    tool_definitions=resolved_agent.tool_definitions,
                    mcp_servers=resolved_agent.mcp_servers,
                )
            )
        except Exception:
            run.status = RuntimeStatus.UNKNOWN
            run.error_category = "RUNTIME_RESULT_UNKNOWN"
            self._runs[self._scope(identity, run.run_id)] = run
            if fencing_token is not None and hasattr(self.persistence, "mark_unknown"):
                self.persistence.mark_unknown(identity, run.run_id, fencing_token)
            raise
        session.runtime_session_ref = result.runtime_session_ref
        run.status = result.status
        run.result_text = result.text
        if self.object_store is not None and result.text:
            result_bytes = result.text.encode("utf-8")
            upload = self.object_store.create_upload_intent(
                UploadRequest(
                    platform_id=identity.platform_id,
                    tenant_id=identity.tenant_id,
                    filename=f"run-{run.run_id}.txt",
                    mime_type="text/plain; charset=utf-8",
                    size_bytes=len(result_bytes),
                    retention_policy="session",
                    expected_sha256=hashlib.sha256(result_bytes).hexdigest(),
                )
            )
            result_asset = self.object_store.complete_upload(upload.upload_id, result_bytes)
            run.result_ref = result_asset.object_ref
        run.runtime_reference = RuntimeReference(
            engine_name=str(getattr(self.runtime, "engine_name", "fake")),
            runtime_session_ref=result.runtime_session_ref,
            runtime_result_ref=result.runtime_result_ref,
        )
        run.error_category = next(
            (
                str(event.payload["error_category"])
                for event in result.events
                if event.event_type == "tool_denied" and "error_category" in event.payload
            ),
            None,
        )
        for event in result.events:
            self._event_to_span(session.trace_id, run, event)
        self._append_span(
            session.trace_id,
            run_id=run.run_id,
            span_type=SpanType.MODEL,
            status="succeeded" if result.status is RuntimeStatus.SUCCEEDED else "failed",
            parent_span_id=run.span_id,
            attributes={
                "engine": "fake",
                "model_alias": "fake-model",
                "usage_source": result.usage.source,
            },
        )
        self._append_span(
            session.trace_id,
            run_id=run.run_id,
            span_type=SpanType.RESULT,
            status=result.status.value,
            parent_span_id=run.span_id,
            attributes={"result_digest": hashlib.sha256(result.text.encode()).hexdigest()},
        )
        self._usage[run.run_id] = result.usage

        if (
            persistent_submission
            and fencing_token is not None
            and result.status is RuntimeStatus.SUCCEEDED
        ):
            run_spans = tuple(
                span
                for span in self._spans.get(run.trace_id, [])
                if span.run_id == run.run_id
            )
            approval = self._create_approval_from_preview(
                identity,
                run,
                session,
                tuple(resolved_agent.tool_definitions),
                tuple(result.events),
                run_spans,
                result.usage,
                run.runtime_reference,
                run_fencing_token=fencing_token,
                result_ref=run.result_ref,
            )
            if approval is not None:
                self._spans[run.trace_id] = [
                    replace(
                        span,
                        status="awaiting_approval",
                        attributes={
                            **span.attributes,
                            "approval_ref": approval.approval_id,
                            "approval_state": approval.state.value,
                        },
                    )
                    if span.span_id == approval.tool_invocation_id
                    else span
                    for span in self._spans.get(run.trace_id, [])
                ]
                self._runs[self._scope(identity, run.run_id)] = run
                return run

        self._runs[self._scope(identity, run.run_id)] = run
        if persistent_submission and fencing_token is not None:
            run_spans = tuple(
                span
                for span in self._spans.get(run.trace_id, [])
                if span.run_id in {"", run.run_id}
            )
            try:
                if hasattr(self.persistence, "complete_run"):
                    committed = self.persistence.complete_run(
                        identity, run, fencing_token, run_spans, result.usage
                    )
                else:
                    committed = self.persistence.finish_run(identity, run, fencing_token)
            except Exception:
                # Runtime 已返回结果但结果/Trace/Usage 事务失败；不能让 lease 到期后重跑旧 Run。
                run.status = RuntimeStatus.UNKNOWN
                run.error_category = "RUN_RESULT_PERSISTENCE_UNKNOWN"
                self._runs[self._scope(identity, run.run_id)] = run
                if hasattr(self.persistence, "mark_unknown"):
                    self.persistence.mark_unknown(identity, run.run_id, fencing_token)
                raise
            if not committed:
                persisted = self.persistence.get_run(identity, run.run_id)
                self._runs[self._scope(identity, run.run_id)] = persisted
                return persisted
            if hasattr(self.persistence, "save_session_runtime_refs"):
                self.persistence.save_session_runtime_refs(
                    identity,
                    session.session_id,
                    runtime_session_ref=result.runtime_session_ref,
                    runtime_result_ref=result.runtime_result_ref,
                    harness_home_ref=session.harness_home_ref,
                )
        elif self.persistence is not None and hasattr(self.persistence, "save_run"):
            if hasattr(self.persistence, "register_session") and hasattr(self.persistence, "acquire_lease"):
                lease = self.persistence.acquire_lease(identity, session.session_id)
                self.persistence.save_run(
                    identity,
                    session.session_id,
                    run.run_id,
                    lease.fencing_token,
                    {
                        "employee_version": snapshot.employee_version,
                        "execution_profile_id": snapshot.execution_profile_id,
                        "execution_profile_version": snapshot.execution_profile_version,
                        "model_route_id": snapshot.model_route_id,
                        "trace_id": run.trace_id,
                    },
                    {
                        "runtime_session_ref": result.runtime_session_ref,
                        "result_ref": result.runtime_result_ref,
                    },
                )
            else:
                self.persistence.save_run(identity, run, idempotency_key or run.run_id)
            if hasattr(self.persistence, "save_session_runtime_refs"):
                self.persistence.save_session_runtime_refs(
                    identity,
                    session.session_id,
                    runtime_session_ref=result.runtime_session_ref,
                    runtime_result_ref=result.runtime_result_ref,
                    harness_home_ref=session.harness_home_ref,
                )
        if idempotency_key and persistent_submission:
            self._idempotency[(*self._scope(identity, session_id), idempotency_key)] = run.run_id
        return run

    def execute_claimed_run(
        self,
        identity: IdentityContext,
        run: RunRecord,
        session: SessionRecord,
        prompt: str,
        *,
        worker_id: str,
        fencing_token: int,
    ) -> RunRecord:
        """执行 Worker 已经 claim 并转入 RUNNING 的固定 Run，不重新解析当前员工版本。"""

        if self.persistence is None or not hasattr(self.persistence, "complete_run"):
            raise DomainError("INVALID_STATE", "claimed Run 执行需要持久化租约仓储")
        if identity.auth_source != "worker":
            raise DomainError("AUTHORIZATION_DENIED", "claimed Run 只能由受信 Worker 执行")
        if (
            run.status is not RuntimeStatus.RUNNING
            or run.platform_id != identity.platform_id
            or run.tenant_id != identity.tenant_id
            or session.platform_id != identity.platform_id
            or session.tenant_id != identity.tenant_id
            or run.session_id != session.session_id
            or run.submitted_by_user_id is None
            or not worker_id.strip()
            or fencing_token < 1
        ):
            raise DomainError("INVALID_STATE", "claim、租户或 Run 状态与执行上下文不一致")
        if not prompt.strip():
            raise DomainError("INVALID_ARGUMENT", "Run 输入正文不能为空")

        def finish_before_runtime(status: RuntimeStatus, error_code: str | None) -> RunRecord:
            run.status = status
            run.error_category = error_code
            if self.persistence.complete_run(identity, run, fencing_token, (), None):
                return run
            return self.persistence.get_run(identity, run.run_id)

        def lose_claimed_lease() -> RunRecord:
            if hasattr(self.persistence, "mark_unknown"):
                self.persistence.mark_unknown(identity, run.run_id, fencing_token)
            return self.persistence.get_run(identity, run.run_id)

        def lease_is_current() -> bool:
            if not hasattr(self.persistence, "is_run_lease_current"):
                return True
            return self.persistence.is_run_lease_current(
                identity, run.run_id, worker_id, fencing_token
            )

        current = (
            self.persistence.get_run(identity, run.run_id)
            if hasattr(self.persistence, "get_run")
            else run
        )
        if current.status is RuntimeStatus.CANCELLING:
            return finish_before_runtime(RuntimeStatus.CANCELLED, "SUBMITTER_CANCELLED")
        if current.status is not RuntimeStatus.RUNNING:
            return current
        if not lease_is_current():
            return lose_claimed_lease()

        try:
            resolved_agent = self.agent_resolver.resolve_snapshot(identity, run.snapshot)
        except Exception as error:  # noqa: BLE001 — 固定资源解析失败发生在 Runtime 出站前
            error_code = error.code if isinstance(error, DomainError) else "RUN_PREPARATION_FAILED"
            return finish_before_runtime(RuntimeStatus.FAILED, error_code)
        current = (
            self.persistence.get_run(identity, run.run_id)
            if hasattr(self.persistence, "get_run")
            else run
        )
        if current.status is RuntimeStatus.CANCELLING:
            return finish_before_runtime(RuntimeStatus.CANCELLED, "SUBMITTER_CANCELLED")
        if current.status is not RuntimeStatus.RUNNING:
            return current
        if not lease_is_current():
            return lose_claimed_lease()
        if run.context_snapshot is None:
            run.context_snapshot = self.context_assembler.assemble(
                run_id=run.run_id,
                trace_id=run.trace_id,
                agent_ref=f"{run.snapshot.employee_id}:v{run.snapshot.employee_version}",
                skill_refs=run.snapshot.skill_version_ids,
                recent_messages=(),
                memory_refs=(),
                tool_result_refs=(),
            )
        if hasattr(self.persistence, "list_session_trace"):
            existing_spans = list(
                self.persistence.list_session_trace(identity, session.session_id)
            )
            self._spans[session.trace_id] = existing_spans
            if not any(span.span_type is SpanType.SESSION for span in existing_spans):
                if not session.span_id:
                    session.span_id = self._new_id("span")
                self._spans[session.trace_id].append(
                    TraceSpan(
                        span_id=session.span_id,
                        trace_id=session.trace_id,
                        parent_span_id=None,
                        run_id="",
                        span_type=SpanType.SESSION,
                        status="started",
                        sequence=len(existing_spans) + 1,
                        attributes={
                            "session_id": session.session_id,
                            "deployment_id": session.deployment_id,
                            "employee_version": session.employee_version,
                        },
                    )
                )
        self._runs[self._scope(identity, run.run_id)] = run
        self._sessions[self._scope(identity, session.session_id)] = session
        if session.runtime_session_ref is None:
            try:
                session.runtime_session_ref = self.runtime.create_session()
                if hasattr(self.persistence, "save_session_runtime_refs"):
                    self.persistence.save_session_runtime_refs(
                        identity,
                        session.session_id,
                        runtime_session_ref=session.runtime_session_ref,
                        runtime_result_ref=None,
                        harness_home_ref=session.harness_home_ref,
                    )
            except Exception as error:  # noqa: BLE001 — Session 建立失败时尚未调用 Run
                error_code = error.code if isinstance(error, DomainError) else "RUNTIME_SESSION_CREATE_FAILED"
                return finish_before_runtime(RuntimeStatus.FAILED, error_code)

        # 取消可能恰好发生在 Runtime Session 创建期间；出站前再次读取当前状态。
        current = (
            self.persistence.get_run(identity, run.run_id)
            if hasattr(self.persistence, "get_run")
            else run
        )
        if current.status is RuntimeStatus.CANCELLING:
            capabilities = getattr(self.runtime, "capabilities", None)
            cancel = getattr(self.runtime, "cancel", None)
            try:
                cancelled = bool(
                    getattr(capabilities, "cancellation", False)
                    and callable(cancel)
                    and cancel(session.runtime_session_ref)
                )
            except Exception:  # noqa: BLE001 — 不能证明 Runtime Session 已停止
                cancelled = False
            if cancelled:
                return finish_before_runtime(RuntimeStatus.CANCELLED, "SUBMITTER_CANCELLED")
            if hasattr(self.persistence, "mark_unknown"):
                self.persistence.mark_unknown(identity, run.run_id, fencing_token)
            return self.persistence.get_run(identity, run.run_id)
        if current.status is not RuntimeStatus.RUNNING:
            return current
        if not lease_is_current():
            return lose_claimed_lease()

        try:
            return self._execute_run(
                identity,
                session,
                prompt,
                None,
                run,
                resolved_agent,
                persistent_submission=True,
                claimed_fencing_token=fencing_token,
                worker_id=worker_id,
            )
        except Exception:  # noqa: BLE001 — Runtime 出站后不能推断远端无副作用
            return self.persistence.get_run(identity, run.run_id)

    def test_employee_version(
        self,
        identity: IdentityContext,
        employee_id: str,
        version: int,
        prompt: str,
    ) -> RuntimeResult:
        """对 DRAFT 版本执行一次不创建 Deployment/Session/Run/Trace 的管理员预览。"""

        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以测试员工版本")
        if not prompt.strip():
            raise DomainError("INVALID_ARGUMENT", "测试 prompt 不能为空")
        employee = self.catalog.repository.get_employee(identity, employee_id)
        if employee.status is LifecycleStatus.ARCHIVED:
            raise DomainError("INVALID_STATE", "已归档员工不能测试版本")
        employee_version = self.catalog.repository.get_employee_version(identity, employee_id, version)
        if employee_version.status not in {LifecycleStatus.DRAFT, LifecycleStatus.PUBLISHED}:
            raise DomainError("INVALID_STATE", "当前员工版本状态不支持测试")
        preview_version = (
            employee_version
            if employee_version.status is LifecycleStatus.PUBLISHED
            else replace(employee_version, status=LifecycleStatus.PUBLISHED)
        )
        deployment = Deployment(
            deployment_id=generate_database_id(),
            tenant_id=identity.tenant_id,
            employee_id=employee_id,
            employee_version=preview_version,
        )
        resolved_agent = self.agent_resolver.resolve(identity, deployment)
        return self.runtime.run(
            RuntimeRequest(
                run_id=generate_database_id(),
                session_ref=None,
                prompt=prompt,
                skill_instructions=resolved_agent.skill_instructions,
                allowed_tools=resolved_agent.allowed_tools,
                system_prompt=resolved_agent.system_prompt,
                skill_file_packages=resolved_agent.skill_file_packages,
                metadata={"execution_mode": "draft_test"},
            )
        )

    def get_run(self, identity: IdentityContext, run_id: str) -> RunRecord:
        try:
            run = self._get_run(identity, run_id)
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND" or self.persistence is None or not hasattr(self.persistence, "get_run"):
                raise
            run = self.persistence.get_run(identity, run_id)
        return self._load_result(identity, run)

    def request_run_cancellation(
        self, identity: IdentityContext, run_id: str
    ) -> RunRecord:
        """仅 Run 提交者能请求取消；已 claim 的 Run 由 Worker 处理 Runtime 取消。"""

        run = self.get_run(identity, run_id)
        if run.submitted_by_user_id is None or run.submitted_by_user_id != identity.user_id:
            raise DomainError("AUTHORIZATION_DENIED", "只有 Run 提交者可以请求取消")
        if self.persistence is not None and hasattr(
            self.persistence, "request_run_cancellation"
        ):
            state = self.persistence.request_run_cancellation(identity, run_id)
            run.status = RuntimeStatus(state.lower())
        elif run.status is RuntimeStatus.QUEUED:
            run.status = RuntimeStatus.CANCELLED
            run.error_category = "SUBMITTER_CANCELLED"
        else:
            raise DomainError(
                "INVALID_STATE",
                "Fake Runtime 不支持取消已经开始的同步 Run",
            )
        self._runs[self._scope(identity, run_id)] = run
        return self._load_result(identity, run)

    def _load_result(self, identity: IdentityContext, run: RunRecord) -> RunRecord:
        """通過租户授权对象仓储恢复 Run 正文，数据库只保留引用和摘要。"""

        if run.result_ref is not None and self.object_store is not None:
            try:
                run.result_text = self.object_store.read(
                    run.result_ref, identity.platform_id, identity.tenant_id
                ).decode("utf-8")
            except (PermissionError, UnicodeDecodeError) as error:
                raise DomainError("RESOURCE_NOT_FOUND", "Run 结果不存在") from error
        return run

    def get_session(self, identity: IdentityContext, session_id: str) -> SessionRecord:
        if self.persistence is not None and hasattr(self.persistence, "get_session"):
            session = self.persistence.get_session(identity, session_id)
            self._sessions[self._scope(identity, session_id)] = session
            return session
        try:
            return self._get_session(identity, session_id)
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND" or self.persistence is None or not hasattr(self.persistence, "get_session"):
                raise
            return self.persistence.get_session(identity, session_id)

    def get_trace(self, identity: IdentityContext, run_id: str) -> RunTimeline:
        try:
            run = self._get_run(identity, run_id)
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND" or self.persistence is None or not hasattr(self.persistence, "get_trace"):
                raise
            timeline = self.persistence.get_trace(identity, run_id)
            self._load_result(identity, timeline.run)
            return timeline
        spans = tuple(
            span
            for span in self._spans.get(run.trace_id, [])
            if span.run_id in {"", run.run_id}
        )
        usage = self._usage.get(run_id)
        if usage is None:
            usage = UsageRecord(0, 0, 0, source="UNAVAILABLE")
        return RunTimeline(
            run=run,
            spans=spans,
            usage=usage,
            runtime_reference=run.runtime_reference,
        )

    def get_session_trace(self, identity: IdentityContext, session_id: str) -> tuple[TraceSpan, ...]:
        try:
            session = self._get_session(identity, session_id)
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND" or self.persistence is None or not hasattr(self.persistence, "list_session_trace"):
                raise
            return self.persistence.list_session_trace(identity, session_id)
        return tuple(self._spans.get(session.trace_id, []))
