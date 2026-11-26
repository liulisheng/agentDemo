"""Conversation、Message、AgentBinding 和渠道投递领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class MessageSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    CHANNEL = "channel"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


class ConversationMode(StrEnum):
    """用户可见 Conversation 的执行模式；模式创建后保持固定。"""

    SINGLE_AGENT = "SINGLE_AGENT"
    TEAM = "TEAM"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str
    platform_id: str
    tenant_id: str
    title: str
    is_group: bool = False
    status: str = "ACTIVE"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    conversation_mode: ConversationMode | str = ConversationMode.SINGLE_AGENT

    def __post_init__(self) -> None:
        try:
            mode = ConversationMode(self.conversation_mode)
        except ValueError as error:
            raise ValueError("conversation_mode must be SINGLE_AGENT or TEAM") from error
        if mode is ConversationMode.TEAM and not self.is_group:
            raise ValueError("TEAM conversation must be a group")
        object.__setattr__(self, "conversation_mode", mode)


@dataclass(frozen=True, slots=True)
class Participant:
    conversation_id: str
    user_id: str
    role: str = "member"


@dataclass(frozen=True, slots=True)
class AgentBinding:
    conversation_id: str
    employee_id: str
    employee_name: str
    session_id: str
    status: str = "ACTIVE"
    team_member_id: str | None = None
    team_member_role: str | None = None
    capabilities_snapshot: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    conversation_id: str
    platform_id: str
    tenant_id: str
    sender_id: str
    text: str
    source: MessageSource
    external_message_id: str | None = None
    reply_to_id: str | None = None
    routing_depth: int = 0
    trigger_kind: str | None = None
    created_at: datetime | None = field(default_factory=lambda: datetime.now(UTC))
    sender_name: str | None = None
    team_member_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.message_id, "message_id")
        _text(self.conversation_id, "conversation_id")
        _text(self.tenant_id, "tenant_id")
        _text(self.sender_id, "sender_id")
        if not self.text.strip():
            raise ValueError("message text must not be blank")
        if self.routing_depth < 0:
            raise ValueError("routing_depth must not be negative")


@dataclass(frozen=True, slots=True)
class InboundEvent:
    event_id: str
    channel_name: str
    external_message_id: str
    message_id: str


@dataclass(frozen=True, slots=True)
class OutboundDelivery:
    delivery_id: str
    conversation_id: str
    tenant_id: str
    body: str
    idempotency_key: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    external_message_id: str | None = None
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: str
    conversation_id: str
    session_id: str
    run_id: str | None = None
    status: str = "CREATED"


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """把员工版本的能力槽位映射到项目拥有的资源引用。"""

    binding_id: str
    employee_version_id: str
    slot: str
    module_id: str
    module_version: int
    resource_ref: str


@dataclass(frozen=True, slots=True)
class CompositionSnapshot:
    """Runtime 启动前固化的能力组成快照。"""

    snapshot_id: str
    employee_version_id: str
    execution_engine: str
    security_profile: str
    skill_refs: tuple[str, ...]
    tool_refs: tuple[str, ...]
    trace_names: tuple[str, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        employee_version_id: str,
        execution_engine: str,
        security_profile: str,
        skill_refs: tuple[str, ...],
        tool_refs: tuple[str, ...],
        trace_names: tuple[str, ...],
        created_at: str,
    ) -> CompositionSnapshot:
        return cls(
            snapshot_id=f"composition-{uuid4().hex}",
            employee_version_id=employee_version_id,
            execution_engine=execution_engine,
            security_profile=security_profile,
            skill_refs=skill_refs,
            tool_refs=tool_refs,
            trace_names=trace_names,
            created_at=created_at,
        )
