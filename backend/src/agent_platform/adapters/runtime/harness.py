"""现有 harness-engine 的薄适配边界，不复制供应商 Agent Loop。"""

from __future__ import annotations

from collections.abc import Callable

from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.ports.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)


class HarnessRuntimeAdapter:
    """把 Harness 生命周期类型转换为平台 RuntimeResult。"""

    capabilities = RuntimeCapabilities(resume=True, cancellation=True, workspace=True)

    def __init__(self, engine_factory: Callable[[], object], *, engine_name: str) -> None:
        self._engine_factory = engine_factory
        self._engine_name = engine_name
        self._engine: object | None = None
        self._sessions: set[str] = set()

    def _ensure_engine(self) -> object:
        if self._engine is None:
            self._engine = self._engine_factory()
            start = getattr(self._engine, "start", None)
            if callable(start):
                start()
        return self._engine

    def create_session(self) -> str:
        engine = self._ensure_engine()
        ref = engine.create_session()
        session_id = getattr(ref, "session_id", getattr(ref, "engine_session_id", str(ref)))
        self._sessions.add(session_id)
        return session_id

    def resume_session(self, session_ref: str) -> str:
        if session_ref not in self._sessions:
            raise ValueError("runtime session was not found")
        return session_ref

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        session_ref = request.session_ref or self.create_session()
        engine = self._ensure_engine()
        engine_request = type("PlatformRequest", (), {"prompt": request.prompt, "session_id": session_ref, "run_id": request.run_id})()
        result = engine.run(engine_request)
        text = str(getattr(result, "text", ""))
        result_ref = str(getattr(result, "result_ref", f"{self._engine_name}-result"))
        return RuntimeResult(
            text=text,
            status=RuntimeStatus.SUCCEEDED,
            runtime_session_ref=session_ref,
            runtime_result_ref=result_ref,
            usage=UsageRecord(max(1, len(request.prompt.split())), max(1, len(text.split())), max(1, len(request.prompt.split())) + max(1, len(text.split()))),
            events=(RuntimeEvent("run_finished", "RESULT", {"status": "succeeded"}),),
            capabilities=self.capabilities,
        )

    def cancel(self, session_ref: str) -> bool:
        if session_ref not in self._sessions:
            raise ValueError("runtime session was not found")
        cancel = getattr(self._engine, "cancel", None) if self._engine is not None else None
        if callable(cancel):
            cancel(session_ref)
        return True
