"""平台 Session、Run 和执行租约的持久化端口。"""

from __future__ import annotations

from typing import Protocol

from agent_platform.domain.execution import UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, SessionRecord, TraceSpan
from agent_platform.domain.usage import ModelCallRecord, ToolInvocationRecord, UsageAdjustment


class ExecutionRepository(Protocol):
    """承载 Session/Run 的跨进程状态；实现必须在每次读写中限定平台和租户。"""

    def save_session(self, identity: IdentityContext, session: SessionRecord, *, engine_name: str) -> None: ...

    def get_session(self, identity: IdentityContext, session_id: str) -> SessionRecord: ...

    def save_session_runtime_refs(
        self,
        identity: IdentityContext,
        session_id: str,
        *,
        runtime_session_ref: str,
        runtime_result_ref: str | None,
    ) -> None: ...

    def save_run(self, identity: IdentityContext, run: RunRecord, idempotency_key: str) -> None: ...

    def submit_run(
        self, identity: IdentityContext, run: RunRecord, idempotency_key: str
    ) -> tuple[RunRecord, bool]: ...

    def acquire_lease(
        self, identity: IdentityContext, run_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> int | None: ...

    def heartbeat(
        self, identity: IdentityContext, run_id: str, fencing_token: int, *, lease_seconds: int = 30
    ) -> bool: ...

    def append_trace_span(self, identity: IdentityContext, span: TraceSpan, fencing_token: int) -> bool: ...

    def finish_run(self, identity: IdentityContext, run: RunRecord, fencing_token: int) -> bool: ...

    def complete_run(
        self,
        identity: IdentityContext,
        run: RunRecord,
        fencing_token: int,
        spans: tuple[TraceSpan, ...],
        usage: UsageRecord | None,
    ) -> bool: ...

    def mark_unknown(self, identity: IdentityContext, run_id: str, fencing_token: int) -> bool: ...

    def get_run(self, identity: IdentityContext, run_id: str) -> RunRecord: ...

    def get_trace(self, identity: IdentityContext, run_id: str) -> object: ...

    def list_trace_spans(
        self, identity: IdentityContext, run_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> tuple[TraceSpan, ...]: ...

    def record(self, identity: IdentityContext, record: ModelCallRecord) -> None: ...

    def add_adjustment(self, identity: IdentityContext, adjustment: UsageAdjustment) -> None: ...

    def record_tool_invocation(
        self, identity: IdentityContext, record: ToolInvocationRecord, fencing_token: int
    ) -> bool: ...
