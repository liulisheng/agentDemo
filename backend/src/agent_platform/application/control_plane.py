"""把目录部署、Session、Run、Runtime 和 Trace 串成最小同步闭环。"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.context import BusinessContextAssembler
from agent_platform.application.resolvers import AgentResolver
from agent_platform.domain.catalog import Deployment, LifecycleStatus
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.context import BusinessContextPolicy
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import (
    RunRecord,
    RunTimeline,
    SessionRecord,
    SpanType,
    TraceSpan,
)
from agent_platform.ports.execution_repository import ExecutionRepository
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.runtime import RuntimeAdapter, RuntimeEvent, RuntimeRequest, RuntimeResult
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
    ) -> None:
        self.catalog = catalog
        self.runtime = runtime
        self.persistence = persistence
        self.object_store = object_store
        self.agent_resolver = AgentResolver(
            catalog, resource_center_repository, object_store=object_store
        )
        self.context_assembler = BusinessContextAssembler(BusinessContextPolicy())
        self._sessions: dict[tuple[str, str, str], SessionRecord] = {}
        self._runs: dict[tuple[str, str, str], RunRecord] = {}
        self._spans: dict[str, list[TraceSpan]] = {}
        self._usage: dict[str, UsageRecord] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, str]] = {}

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
        status = "failed" if event.event_type == "tool_denied" else "succeeded"
        attributes = dict(event.payload)
        return self._append_span(
            trace_id,
            run_id=run.run_id,
            span_type=span_type,
            status=status,
            parent_span_id=run.span_id,
            attributes=attributes,
        )

    def create_run(
        self,
        identity: IdentityContext,
        session_id: str,
        prompt: str,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        if not prompt.strip():
            raise DomainError("INVALID_ARGUMENT", "prompt 不能为空")
        session = self.get_session(identity, session_id)
        if session.status != "ACTIVE":
            raise DomainError("INVALID_STATE", "Session 不是 ACTIVE 状态")
        # 以本次状态读取为准入点；通过后允许完成，无需在 Runtime 完成时再次检查。
        deployment = self._require_active_deployment(identity, session.deployment_id)
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if idempotency_key:
            idempotency_index = (*self._scope(identity, session_id), idempotency_key)
            previous = self._idempotency.get(idempotency_index)
            if previous is not None:
                previous_digest, previous_run_id = previous
                if previous_digest != prompt_digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
                return self._get_run(identity, previous_run_id)
        resolved_agent = self.agent_resolver.resolve(identity, deployment)
        snapshot = resolved_agent.snapshot
        run = RunRecord(
            run_id=self._new_id("run"),
            session_id=session.session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            trace_id=session.trace_id,
            prompt_digest=prompt_digest,
            snapshot=snapshot,
            status=RuntimeStatus.UNKNOWN,
        )
        skill_instructions = resolved_agent.skill_instructions
        allowed_tools = resolved_agent.allowed_tools
        context_snapshot = self.context_assembler.assemble(
            run_id=run.run_id,
            trace_id=session.trace_id,
            agent_ref=f"{snapshot.employee_id}:v{snapshot.employee_version}",
            skill_refs=snapshot.skill_version_ids,
            recent_messages=(),
            memory_refs=(),
            tool_result_refs=(),
        )
        run.context_snapshot = context_snapshot
        persistent_key = idempotency_key or run.run_id
        fencing_token: int | None = None
        persistent_submission = self.persistence is not None and hasattr(self.persistence, "submit_run")
        if persistent_submission:
            existing_run, created = self.persistence.submit_run(identity, run, persistent_key)
            if not created:
                self._load_result(identity, existing_run)
                self._runs[self._scope(identity, existing_run.run_id)] = existing_run
                if idempotency_key:
                    self._idempotency[(*self._scope(identity, session_id), idempotency_key)] = (
                        prompt_digest,
                        existing_run.run_id,
                    )
                return existing_run
            fencing_token = self.persistence.acquire_lease(
                identity, run.run_id, generate_database_id(), lease_seconds=30
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
            attributes={"snapshot_id": context_snapshot.snapshot_id, "total_tokens": context_snapshot.total_tokens},
        )
        try:
            result = self.runtime.run(
                RuntimeRequest(
                    run_id=run.run_id,
                    session_ref=session.runtime_session_ref,
                    prompt=prompt,
                    skill_instructions=skill_instructions,
                    allowed_tools=allowed_tools,
                    system_prompt=resolved_agent.system_prompt,
                    skill_file_packages=resolved_agent.skill_file_packages,
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
            engine_name="fake",
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
        self._runs[self._scope(identity, run.run_id)] = run
        self._usage[run.run_id] = result.usage
        if persistent_submission and fencing_token is not None:
            run_spans = tuple(
                span
                for span in self._spans.get(run.trace_id, [])
                if span.run_id in {"", run.run_id}
            )
            if hasattr(self.persistence, "complete_run"):
                committed = self.persistence.complete_run(
                    identity, run, fencing_token, run_spans, result.usage
                )
            else:
                committed = self.persistence.finish_run(identity, run, fencing_token)
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
                )
        if idempotency_key:
            self._idempotency[(*self._scope(identity, session_id), idempotency_key)] = (
                prompt_digest,
                run.run_id,
            )
        return run

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
        return RunTimeline(
            run=run,
            spans=spans,
            usage=self._usage[run_id],
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
