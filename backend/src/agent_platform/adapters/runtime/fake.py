"""离线 Fake Runtime 适配器，模拟生命周期和可观测事件。"""

from __future__ import annotations

from uuid import uuid4

from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.ports.runtime import (
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)


class FakeRuntimeAdapter:
    """用确定性文本和无副作用 echo 工具验证平台 Runtime 边界。"""

    engine_name = "fake"
    capabilities = RuntimeCapabilities(
        streaming=False,
        resume=True,
        cancellation=True,
        tool_calls=True,
        approvals=False,
        mcp=False,
        workspace=True,
        sandbox=False,
    )

    def __init__(self) -> None:
        self._sessions: set[str] = set()

    def create_session(self, request=None) -> str:
        del request
        session_ref = f"fake-session-{uuid4().hex}"
        self._sessions.add(session_ref)
        return session_ref

    def resume_session(self, session_ref: str) -> str:
        if session_ref not in self._sessions:
            raise ValueError(f"runtime session {session_ref!r} was not found")
        return session_ref

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        if request.session_ref is None:
            session_ref = self.create_session()
        else:
            session_ref = self.resume_session(request.session_ref)
        input_tokens = max(1, len(request.prompt.split()))
        events: list[RuntimeEvent] = [
            RuntimeEvent("run_started", "RUNTIME", {"run_id": request.run_id}),
        ]
        status = RuntimeStatus.SUCCEEDED
        text = f"fake: {request.prompt}"
        if request.prompt.startswith("echo:") and "echo" not in request.allowed_tools:
            status = RuntimeStatus.FAILED
            text = ""
            events.append(
                RuntimeEvent(
                    "tool_denied",
                    "TOOL",
                    {"tool_name": "echo", "error_category": "POLICY_DENIED"},
                )
            )
        elif request.prompt.startswith("echo:"):
            events.append(
                RuntimeEvent(
                    "tool_called",
                    "TOOL",
                    {"tool_name": "echo", "status": "succeeded"},
                )
            )
        events.append(
            RuntimeEvent(
                "runtime_completed",
                "RUNTIME",
                {"status": status.value},
            )
        )
        events.append(
            RuntimeEvent(
                "run_finished",
                "RESULT",
                {"status": status.value},
            )
        )
        output_tokens = max(1, len(text.split())) if text else 0
        return RuntimeResult(
            text=text,
            status=status,
            runtime_session_ref=session_ref,
            runtime_result_ref=f"fake-result-{uuid4().hex}",
            usage=UsageRecord(input_tokens, output_tokens, input_tokens + output_tokens),
            events=tuple(events),
            capabilities=self.capabilities,
            runtime_thread_ref=f"fake-thread-{session_ref}",
            runtime_workspace_ref=request.workspace_ref,
        )

    def cancel(self, session_ref: str) -> bool:
        """Fake Runtime 只确认已存在 Session，真实取消由供应商适配器负责。"""

        self.resume_session(session_ref)
        return True
