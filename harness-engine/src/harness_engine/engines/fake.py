"""Offline engine used for contract and CLI verification."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ..core import (
    CancelResult,
    CancelStatus,
    EngineCapabilities,
    EngineRequest,
    EngineResult,
    EventHandler,
    HarnessEvent,
    LifecycleEngine,
    RunStatus,
    SessionMismatchError,
    SessionNotFoundError,
    SessionRef,
)


class FakeEngine(LifecycleEngine):
    capabilities = EngineCapabilities(
        streaming=False,
        resume=True,
        approvals=False,
        mcp=False,
        cancellation=True,
        events=True,
    )

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, SessionRef] = {}
        self._active_runs: dict[str, str] = {}
        self._cancel_requested: set[str] = set()

    def create_session(self) -> SessionRef:
        self._require_started()
        ref = SessionRef(session_id=f"fake-{uuid4().hex}", engine_name="fake")
        self._sessions[ref.session_id] = ref
        return ref

    def resume_session(self, ref: SessionRef) -> SessionRef:
        self._require_started()
        if ref.engine_name != "fake":
            raise SessionMismatchError(
                f"session belongs to engine {ref.engine_name!r}, not 'fake'"
            )
        try:
            return self._sessions[ref.session_id]
        except KeyError as exc:
            raise SessionNotFoundError(
                f"fake session {ref.session_id!r} was not found"
            ) from exc

    def run(
        self,
        request: EngineRequest,
        on_event: EventHandler | None = None,
    ) -> EngineResult:
        self._require_started()
        session = request.session
        if session is None and request.session_id is not None:
            session = self.resume_session(
                SessionRef(session_id=request.session_id, engine_name="fake")
            )
        if session is None:
            session = self.create_session()
        elif session.engine_name != "fake":
            raise SessionMismatchError(
                f"session belongs to engine {session.engine_name!r}, not 'fake'"
            )
        elif session.session_id not in self._sessions:
            raise SessionNotFoundError(
                f"fake session {session.session_id!r} was not found"
            )

        run_id = request.run_id or f"fake-run-{uuid4().hex}"
        self._active_runs[session.session_id] = run_id
        self._cancel_requested.discard(session.session_id)
        try:
            employee = request.employee_context
            started = HarnessEvent(
                event_type="run_started",
                session_id=session.session_id,
                run_id=run_id,
                payload={
                    "parent_run_id": request.parent_run_id,
                    "employee_id": employee.employee_id if employee is not None else None,
                    "employee_version": (
                        employee.employee_version if employee is not None else None
                    ),
                    "allowed_tool_names": tuple(
                        tool.name for tool in request.tool_definitions
                    ),
                },
            )
            if on_event is not None:
                on_event(started)

            cancelled = session.session_id in self._cancel_requested
            status = RunStatus.CANCELLED if cancelled else RunStatus.SUCCEEDED
            finish_reason = "cancelled" if cancelled else "completed"
            text = "" if cancelled else f"fake: {request.prompt}"
            finished = HarnessEvent(
                event_type="run_finished",
                session_id=session.session_id,
                run_id=run_id,
                payload={
                    "parent_run_id": request.parent_run_id,
                    "status": status.value,
                },
            )
            if on_event is not None:
                on_event(finished)
            return EngineResult(
                text=text,
                finish_reason=finish_reason,
                completed_at=datetime.now(UTC),
                capabilities=self.capabilities,
                run_id=run_id,
                session=session,
                status=status,
            )
        finally:
            self._active_runs.pop(session.session_id, None)
            self._cancel_requested.discard(session.session_id)

    def cancel(self, session_id: str) -> CancelResult:
        self._require_started()
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"fake session {session_id!r} was not found")
        run_id = self._active_runs.get(session_id)
        if run_id is None:
            return CancelResult(
                status=CancelStatus.NOT_RUNNING,
                session_id=session_id,
            )
        self._cancel_requested.add(session_id)
        return CancelResult(
            status=CancelStatus.CANCELLING,
            session_id=session_id,
            run_id=run_id,
        )

    def _close_impl(self) -> None:
        self._active_runs.clear()
        self._cancel_requested.clear()
