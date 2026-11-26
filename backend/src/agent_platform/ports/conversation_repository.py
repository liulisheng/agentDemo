"""Conversation、AgentBinding 和正文引用的租户持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_platform.domain.conversations import AgentBinding, Conversation, Message, MessageSource
from agent_platform.domain.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """只保存正文对象引用的消息索引，不把正文映射进 SQL DTO。"""

    message_id: str
    conversation_id: str
    platform_id: str
    tenant_id: str
    sender_id: str
    source: MessageSource
    content_ref: str
    content_sha256: str
    byte_length: int
    content_type: str
    sequence_no: int
    routing_depth: int
    created_at: datetime | None = None
    sender_name: str | None = None
    team_member_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationSummaryRecord:
    """员工工作台列表的授权聚合结果，不在 SQL DTO 内包含消息正文。"""

    conversation: Conversation
    updated_at: datetime
    employee_id: str
    employee_name: str
    session_id: str
    last_message: StoredMessage | None
    last_run_id: str | None
    last_run_status: str | None
    pending_approval_count: int


class ConversationRepository(Protocol):
    """为应用服务提供跨进程 Conversation 与消息引用查询。"""

    def save_conversation(self, identity: IdentityContext, value: Conversation) -> None: ...

    def get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation: ...

    def get_conversation_id_for_run(self, identity: IdentityContext, run_id: str) -> str:
        """通过 Run 绑定的用户消息读取 Conversation，避免跨表猜测会话归属。"""
        ...

    def save_binding(self, identity: IdentityContext, value: AgentBinding) -> None: ...

    def list_bindings(self, identity: IdentityContext, conversation_id: str) -> tuple[AgentBinding, ...]: ...

    def get_message(self, identity: IdentityContext, message_id: str) -> StoredMessage: ...

    def save_message(
        self,
        identity: IdentityContext,
        value: Message,
        *,
        session_id: str,
        run_id: str | None = None,
        content_ref: str,
        content_sha256: str,
        byte_length: int,
        content_type: str,
    ) -> StoredMessage: ...

    def list_messages(
        self, identity: IdentityContext, conversation_id: str
    ) -> tuple[StoredMessage, ...]: ...

    def list_conversation_summaries(
        self,
        identity: IdentityContext,
        employee_id: str,
        status: str,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[ConversationSummaryRecord, ...]:
        """按用户参与权、员工绑定和更新时间游标返回授权列表索引。"""
        ...
