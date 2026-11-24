"""将每 Run 独立的 Harness 生命周期适配到平台 Runtime 端口。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event, RLock
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.model_routes import UsageStatus, normalize_usage
from agent_platform.ports.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)


@dataclass(slots=True)
class _ActiveHarnessRun:
    """记录当前 Session 的 Engine 和取消信号，供 Worker 租约回调关闭进程。"""

    engine: object
    cancel_requested: Event


class HarnessRuntimeAdapter:
    """每次 Runtime.run 都根据固定 RunRequest 创建并关闭独立 Engine。"""

    def __init__(
        self,
        engine_factory: Callable[[object, RuntimeRequest], object],
        *,
        engine_name: str,
    ) -> None:
        self._engine_factory = engine_factory
        self._engine_name = engine_name
        self._active_runs: dict[str, _ActiveHarnessRun] = {}
        self._lock = RLock()

    @property
    def engine_name(self) -> str:
        """向平台公开适配器标识，避免把 Harness Run 错存为 Fake Runtime。"""

        return self._engine_name

    @property
    def capabilities(self) -> RuntimeCapabilities:
        """仅在当前 Engine 明确能取消或关闭时报告可取消。"""

        with self._lock:
            can_cancel = any(
                callable(getattr(active.engine, "cancel", None))
                or callable(getattr(active.engine, "close", None))
                for active in self._active_runs.values()
            )
        return RuntimeCapabilities(
            resume=True,
            cancellation=can_cancel,
            tool_calls=True,
            workspace=True,
            mcp=True,
            sandbox=False,
            # Harness EngineRequest 已接收平台统一策略；真实供应商字段仍由
            # DeepSeek Engine 在契约验证后映射，Provider 不返回时再标记用量不可用。
            reasoning=True,
            reasoning_effort=True,
            reasoning_budget=True,
            max_output_tokens=True,
        )

    def create_session(self, request: RuntimeRequest | None = None) -> str:
        """仅分配 opaque Session ID；API/Worker 创建 Session 时不启动 Harness。"""

        del request
        return f"harness-pending:{uuid4().hex}"

    def resume_session(self, session_ref: str) -> str:
        """Session 状态由稳定 Harness Home 恢复，适配器本身不缓存 SDK Engine。"""

        if not session_ref.strip():
            raise ValueError("runtime session reference must not be blank")
        return session_ref

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        session_ref = request.session_ref or self.create_session()
        engine_request = self._engine_request(request, session_ref)
        engine = self._engine_factory(engine_request, request)
        active = _ActiveHarnessRun(engine=engine, cancel_requested=Event())
        with self._lock:
            if session_ref in self._active_runs:
                raise RuntimeError("同一 Session 已有活动 Harness Run")
            self._active_runs[session_ref] = active

        try:
            start = getattr(engine, "start", None)
            if callable(start):
                start()
            engine_events: list[object] = []
            result = engine.run(engine_request, on_event=engine_events.append)
            text = str(getattr(result, "text", ""))
            raw_result_ref = getattr(result, "result_ref", None)
            result_ref = (
                str(raw_result_ref)
                if raw_result_ref is not None and str(raw_result_ref).strip()
                else f"{self._engine_name}-result"
            )
            status = self._result_status(result)
            result_session = getattr(result, "session", None)
            resolved_session = getattr(result_session, "session_id", None) or session_ref
            source_events = engine_events or tuple(getattr(result, "events", ()) or ())
            events = tuple(
                self._runtime_event(event, request.tool_definitions)
                for event in source_events
            )
            if not events:
                events = (
                    RuntimeEvent("run_finished", "RESULT", {"status": status.value}),
                )
            usage = self._normalized_usage(request.prompt, text, result)
            return RuntimeResult(
                text=text,
                status=status,
                runtime_session_ref=resolved_session,
                runtime_result_ref=result_ref,
                usage=usage,
                events=events,
                capabilities=self.capabilities,
            )
        except Exception as error:
            if not active.cancel_requested.is_set():
                message = str(error).lower()
                if "does not support reasoning effort" in message or "does not support reasoning budget" in message:
                    raise DomainError(
                        "ENGINE_CAPABILITY_UNSUPPORTED",
                        "真实模型不支持当前回答策略参数",
                        {"engine_name": self._engine_name, "capability": "reasoning"},
                    ) from error
                raise
            # Engine.close() 已切断该 Run 的 Harness 传输；Worker 可将其视为已取消。
            return RuntimeResult(
                text="",
                status=RuntimeStatus.CANCELLED,
                runtime_session_ref=session_ref,
                runtime_result_ref=f"{self._engine_name}-cancelled",
                usage=self._estimated_usage(request.prompt, ""),
                events=(
                    RuntimeEvent(
                        "run_finished",
                        "RESULT",
                        {"status": RuntimeStatus.CANCELLED.value},
                    ),
                ),
                capabilities=self.capabilities,
            )
        finally:
            try:
                close = getattr(engine, "close", None)
                if callable(close):
                    close()
            finally:
                with self._lock:
                    if self._active_runs.get(session_ref) is active:
                        self._active_runs.pop(session_ref, None)

    def cancel(self, session_ref: str) -> bool:
        """关闭该 Run 独占的 Harness，避免 SDK 缺少 callback 时留下运行子进程。"""

        with self._lock:
            active = self._active_runs.get(session_ref)
            if active is None:
                return False
            active.cancel_requested.set()
        cancel = getattr(active.engine, "cancel", None)
        if callable(cancel):
            result = cancel(session_ref)
            if result is True or bool(getattr(result, "confirmed", False)):
                return True
        close = getattr(active.engine, "close", None)
        if not callable(close):
            return False
        close()
        return True

    def _engine_request(self, request: RuntimeRequest, session_ref: str) -> object:
        try:
            from harness_engine.core import (
                EmployeeContext,
                EngineModelConfig,
                EngineRequest,
                EngineToolDefinition,
                SkillFileContext,
            )
        except ImportError as error:
            raise RuntimeError(
                "HarnessRuntimeAdapter requires the optional harness-engine package"
            ) from error

        employee_context = None
        if request.employee_context is not None:
            context = request.employee_context
            employee_context = EmployeeContext(
                employee_id=context.employee_id,
                employee_version=context.employee_version,
                system_prompt=context.system_prompt,
                skill_refs=context.skill_refs,
                skill_instructions=context.skill_instructions,
                skill_files=tuple(
                    SkillFileContext(
                        skill_id=item.skill_id,
                        skill_version=item.skill_version,
                        path=item.path,
                        content=item.content,
                        content_sha256=item.content_sha256,
                    )
                    for item in context.skill_files
                ),
                model_route_id=context.model_route_id,
                model_alias=context.model_alias,
            )
        elif request.system_prompt or request.skill_instructions or request.skill_file_packages:
            raise ValueError(
                "typed employee_context is required when RuntimeRequest carries employee resources"
            )

        tool_definitions = tuple(
            EngineToolDefinition(
                tool_id=item.tool_id,
                tool_version=item.tool_version,
                name=item.name,
                description=item.description,
                input_schema=item.input_schema,
                risk_level=item.risk_level,
                remote_tool_name=item.remote_tool_name,
            )
            for item in request.tool_definitions
        )
        model_config = None
        if request.model_route_snapshot is not None:
            snapshot = request.model_route_snapshot
            model_config = EngineModelConfig(
                tenant_id=snapshot.tenant_id,
                route_id=snapshot.route_id,
                route_version=snapshot.route_version,
                connection_id=snapshot.connection_id,
                connection_version=snapshot.connection_version,
                provider_kind=snapshot.provider_kind,
                model_id=snapshot.model_id,
                protocol=snapshot.protocol,
                endpoint=snapshot.endpoint,
                parameters=snapshot.parameters,
            )
        runtime_options = request.runtime_options.resolve() if request.runtime_options is not None else None
        reasoning_effort = None
        max_tokens = request.max_tokens
        response_verbosity = None
        if runtime_options is not None:
            if (
                runtime_options.reasoning_effort is not None
                and runtime_options.reasoning_effort.value != "AUTO"
            ):
                reasoning_effort = runtime_options.reasoning_effort.value.lower()
            if runtime_options.max_output_tokens is not None:
                max_tokens = runtime_options.max_output_tokens
            if runtime_options.response_verbosity is not None:
                response_verbosity = runtime_options.response_verbosity.value.lower()
        return EngineRequest(
            prompt=request.prompt,
            session_id=session_ref,
            harness_home_ref=request.harness_home_ref,
            run_id=request.run_id,
            metadata=dict(request.metadata),
            employee_context=employee_context,
            tool_definitions=tool_definitions,
            model_config=model_config,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            reasoning_budget_tokens=(
                runtime_options.reasoning_budget_tokens if runtime_options is not None else None
            ),
            response_verbosity=response_verbosity,
        )

    @staticmethod
    def _runtime_event(
        event: object, tool_definitions: tuple[object, ...] = ()
    ) -> RuntimeEvent:
        """将 Harness 事件映射到平台 Trace 类型；调用参数仅保留在进程内。"""

        event_type = str(getattr(event, "event_type", "engine_event"))
        if event_type.startswith("tool/"):
            span_type = "TOOL"
        elif event_type.startswith(("assistant/", "llm/")):
            span_type = "MODEL"
        else:
            span_type = "RUNTIME"
        payload = dict(getattr(event, "payload", {}) or {})
        if event_type == "tool/call":
            raw_data = payload.get("data", payload)
            if isinstance(raw_data, Mapping):
                data = dict(raw_data)
                name = data.get("name")
                arguments_value = data.get("arguments", {})
                try:
                    arguments = (
                        json.loads(arguments_value)
                        if isinstance(arguments_value, str)
                        else arguments_value
                    )
                except json.JSONDecodeError:
                    arguments = None
                if isinstance(name, str) and isinstance(arguments, Mapping):
                    resolved = HarnessRuntimeAdapter._resolve_tool_name(
                        name, arguments, tool_definitions
                    )
                    if resolved is not None:
                        data["name"] = resolved
                        if "data" in payload:
                            payload["data"] = data
                        else:
                            payload = data
        return RuntimeEvent(event_type, span_type, payload)

    @staticmethod
    def _resolve_tool_name(
        reported_name: str,
        arguments: Mapping[str, object],
        tool_definitions: tuple[object, ...],
    ) -> str | None:
        """把 SDK 为长度限制改写的 MCP 名称还原到 Run 固定 ToolVersion。"""

        definitions = [
            definition
            for definition in tool_definitions
            if isinstance(getattr(definition, "remote_tool_name", None), str)
        ]
        exact = [
            definition.remote_tool_name
            for definition in definitions
            if reported_name == definition.remote_tool_name
            or reported_name.endswith("__" + definition.remote_tool_name)
        ]
        if len(exact) == 1:
            return str(exact[0])

        alias = reported_name.rsplit("__", maxsplit=1)[-1]
        alias_prefix = re.sub(r"_[0-9a-f]{12}$", "", alias)
        if alias_prefix == alias:
            return None
        candidates = []
        for definition in definitions:
            remote_name = str(definition.remote_tool_name)
            schema = getattr(definition, "input_schema", {})
            properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
            required = schema.get("required", []) if isinstance(schema, Mapping) else []
            if not isinstance(properties, Mapping) or not isinstance(required, list):
                continue
            if not remote_name.startswith(alias_prefix):
                continue
            if not set(required) <= set(arguments):
                continue
            if not set(arguments) <= set(properties) and schema.get("additionalProperties") is not True:
                continue
            candidates.append(remote_name)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _result_status(result: object) -> RuntimeStatus:
        value = getattr(result, "status", None)
        if value is not None:
            try:
                return RuntimeStatus(str(getattr(value, "value", value)).lower())
            except ValueError:
                pass
        reason = str(getattr(result, "finish_reason", "completed")).lower()
        if reason in {"completed", "succeeded"}:
            return RuntimeStatus.SUCCEEDED
        if reason in {"cancelled", "canceled"}:
            return RuntimeStatus.CANCELLED
        if reason == "unknown":
            return RuntimeStatus.UNKNOWN
        return RuntimeStatus.FAILED

    @staticmethod
    def _estimated_usage(prompt: str, text: str) -> UsageRecord:
        input_tokens = max(1, len(prompt.split()))
        output_tokens = max(1, len(text.split())) if text else 0
        return UsageRecord(
            input_tokens,
            output_tokens,
            input_tokens + output_tokens,
            source="ESTIMATED",
            estimated=True,
            pricing_version="estimated-v1",
        )

    @staticmethod
    def _normalized_usage(prompt: str, text: str, result: object) -> UsageRecord:
        """优先保留上游精确用量和请求 ID；缺失时显式回退到词数估算。"""

        raw_usage = getattr(result, "usage", None)
        usage_mapping: Mapping[str, object] | None = None
        if isinstance(raw_usage, Mapping):
            usage_mapping = raw_usage
        elif raw_usage is not None:
            candidate = {
                name: getattr(raw_usage, name)
                for name in (
                    "input_tokens",
                    "output_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                )
                if getattr(raw_usage, name, None) is not None
            }
            usage_mapping = candidate or None
        request_id = next(
            (
                str(value)
                for name in ("provider_request_id", "request_id", "id")
                if (value := getattr(result, name, None)) is not None and str(value).strip()
            ),
            None,
        )
        if usage_mapping is None:
            estimated = HarnessRuntimeAdapter._estimated_usage(prompt, text)
            return UsageRecord(
                estimated.input_tokens,
                estimated.output_tokens,
                estimated.total_tokens,
                source=estimated.source,
                estimated=estimated.estimated,
                pricing_version=estimated.pricing_version,
                provider_request_id=request_id,
            )
        normalized = normalize_usage(
            usage_mapping,
            estimation_method="harness-estimated:v1",
            provider_request_id=request_id,
        )
        exact = normalized.usage_status is UsageStatus.EXACT
        return UsageRecord(
            normalized.input_tokens,
            normalized.output_tokens,
            normalized.total_tokens,
            reasoning_tokens=int(
                usage_mapping.get("reasoning_tokens", 0)
                if isinstance(usage_mapping.get("reasoning_tokens", 0), int)
                else 0
            ),
            source="UPSTREAM" if exact else "ESTIMATED",
            estimated=not exact,
            pricing_version="unpriced-v1",
            provider_request_id=request_id,
        )
