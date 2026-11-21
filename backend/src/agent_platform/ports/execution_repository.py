"""平台 Session、Run 和执行租约的持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_platform.domain.conversations import Message
from agent_platform.domain.execution import UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, SessionRecord, TraceSpan
from agent_platform.domain.usage import ModelCallRecord, ToolInvocationRecord, UsageAdjustment
from agent_platform.ports.conversation_repository import StoredMessage


@dataclass(frozen=True, slots=True)
class RunClaim:
    """平台范围队列领取后返回的租户 Run 标识和 fencing token。"""

    platform_id: str
    tenant_id: str
    run_id: str
    worker_id: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class RunInputRef:
    """Worker 读取 Conversation 用户消息正文前必须校验的对象引用。"""

    message_id: str
    content_ref: str
    content_sha256: str
    byte_length: int


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
        harness_home_ref: str | None = None,
    ) -> None: ...

    def save_run(self, identity: IdentityContext, run: RunRecord, idempotency_key: str) -> None: ...

    def submit_run(
        self, identity: IdentityContext, run: RunRecord, idempotency_key: str
    ) -> tuple[RunRecord, bool]: ...

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
        """在一个租户事务里插入 USER Message 和 QUEUED Run，按同一幂等键重放。"""
        ...

    def get_run_by_idempotency(
        self, identity: IdentityContext, session_id: str, idempotency_key: str
    ) -> RunRecord | None:
        """按平台、租户及 Session 范围读取已提交的 Run。"""
        ...

    def acquire_lease(
        self, identity: IdentityContext, run_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> int | None: ...

    def claim_next_queued_run(
        self, platform_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> RunClaim | None: ...

    def start_claimed_run(
        self, identity: IdentityContext, run_id: str, worker_id: str, fencing_token: int
    ) -> bool: ...

    def is_run_lease_current(
        self, identity: IdentityContext, run_id: str, worker_id: str, fencing_token: int
    ) -> bool: ...

    def get_run_input_ref(self, identity: IdentityContext, run_id: str) -> RunInputRef: ...

    def request_run_cancellation(self, identity: IdentityContext, run_id: str) -> str: ...

    def heartbeat(
        self,
        identity: IdentityContext,
        run_id: str,
        fencing_token: int,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 30,
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

    def list_session_trace(
        self, identity: IdentityContext, session_id: str
    ) -> tuple[TraceSpan, ...]: ...

    def record(self, identity: IdentityContext, record: ModelCallRecord) -> None: ...

    def add_adjustment(self, identity: IdentityContext, adjustment: UsageAdjustment) -> None: ...

    def record_tool_invocation(
        self, identity: IdentityContext, record: ToolInvocationRecord, fencing_token: int
    ) -> bool: ...
