"""把目录部署、Session、Run、Runtime 和 Trace 串成最小同步闭环。"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from agent_platform.application.catalog_service import CatalogService
from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeReference, RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import (
    RunRecord,
    RunSnapshot,
    RunTimeline,
    SessionRecord,
    SpanType,
    TraceSpan,
)
from agent_platform.ports.runtime import RuntimeAdapter, RuntimeEvent, RuntimeRequest


class ControlPlaneService:
    """同步执行本地 Fake Runtime，并保存可查询的租户级 Trace。"""

    def __init__(self, catalog: CatalogService, runtime: RuntimeAdapter) -> None:
        self.catalog = catalog
        self.runtime = runtime
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
        return f"{prefix}-{uuid4().hex}"

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

    def create_session(self, identity: IdentityContext, deployment_id: str) -> SessionRecord:
        deployment = self.catalog.resolve_deployment(identity, deployment_id)
        session_id = self._new_id("session")
        trace_id = self._new_id("trace")
        session = SessionRecord(
            session_id=session_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            deployment_id=deployment.deployment_id,
            trace_id=trace_id,
            employee_version=deployment.employee_version.version,
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
        return session

    def _selected_tools(
        self,
        identity: IdentityContext,
        employee_version: object,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        skill_instructions: list[str] = []
        declared_tool_ids: set[str] = set()
        for skill_id in employee_version.skill_version_ids:
            skill = self.catalog.repository.get_skill(identity, skill_id)
            skill_instructions.append(skill.instruction)
            declared_tool_ids.update(skill.allowed_tool_ids)
        employee_tool_ids = set(employee_version.tool_version_ids)
        allowed_ids = declared_tool_ids.intersection(employee_tool_ids)
        names_by_id = {
            tool_id: self.catalog.repository.get_tool(identity, tool_id).name
            for tool_id in allowed_ids
        }
        profile = self.catalog.repository.get_profile(identity, employee_version.execution_profile_id)
        if profile.tool_policy:
            names_by_id = {
                tool_id: name for tool_id, name in names_by_id.items() if name in profile.tool_policy
            }
        return tuple(skill_instructions), tuple(names_by_id.values())

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
        session = self._get_session(identity, session_id)
        if session.status != "ACTIVE":
            raise DomainError("INVALID_STATE", "Session 不是 ACTIVE 状态")
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if idempotency_key:
            idempotency_index = (*self._scope(identity, session_id), idempotency_key)
            previous = self._idempotency.get(idempotency_index)
            if previous is not None:
                previous_digest, previous_run_id = previous
                if previous_digest != prompt_digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变")
                return self._get_run(identity, previous_run_id)
        deployment = self.catalog.resolve_deployment(identity, session.deployment_id)
        employee_version = deployment.employee_version
        snapshot = RunSnapshot(
            employee_id=employee_version.employee_id,
            employee_version=employee_version.version,
            skill_version_ids=tuple(employee_version.skill_version_ids),
            tool_version_ids=tuple(employee_version.tool_version_ids),
            execution_profile_id=employee_version.execution_profile_id,
        )
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
        run_span = self._append_span(
            session.trace_id,
            run_id=run.run_id,
            span_type=SpanType.RUN,
            status="running",
            parent_span_id=session.span_id,
            attributes={"employee_version": snapshot.employee_version},
        )
        run.span_id = run_span.span_id
        skill_instructions, allowed_tools = self._selected_tools(identity, employee_version)
        for skill_id in snapshot.skill_version_ids:
            self._append_span(
                session.trace_id,
                run_id=run.run_id,
                span_type=SpanType.SKILL,
                status="selected",
                parent_span_id=run.span_id,
                attributes={"skill_id": skill_id},
            )
        result = self.runtime.run(
            RuntimeRequest(
                run_id=run.run_id,
                session_ref=session.runtime_session_ref,
                prompt=prompt,
                skill_instructions=skill_instructions,
                allowed_tools=allowed_tools,
            )
        )
        session.runtime_session_ref = result.runtime_session_ref
        run.status = result.status
        run.result_text = result.text
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
        if idempotency_key:
            self._idempotency[(*self._scope(identity, session_id), idempotency_key)] = (
                prompt_digest,
                run.run_id,
            )
        return run

    def get_run(self, identity: IdentityContext, run_id: str) -> RunRecord:
        return self._get_run(identity, run_id)

    def get_trace(self, identity: IdentityContext, run_id: str) -> RunTimeline:
        run = self._get_run(identity, run_id)
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
        session = self._get_session(identity, session_id)
        return tuple(self._spans.get(session.trace_id, []))
