"""Conversation、AgentBinding 和正文引用的租户持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
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


class ConversationRepository(Protocol):
    """为应用服务提供跨进程 Conversation 与消息引用查询。"""

    def save_conversation(self, identity: IdentityContext, value: Conversation) -> None: ...

    def get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation: ...

    def save_binding(self, identity: IdentityContext, value: AgentBinding) -> None: ...

    def list_bindings(self, identity: IdentityContext, conversation_id: str) -> tuple[AgentBinding, ...]: ...

    def get_message(self, identity: IdentityContext, message_id: str) -> StoredMessage: ...

    def save_message(
        self,
        identity: IdentityContext,
        value: Message,
        *,
        session_id: str,
        content_ref: str,
        content_sha256: str,
        byte_length: int,
        content_type: str,
    ) -> StoredMessage: ...

    def list_messages(
        self, identity: IdentityContext, conversation_id: str
    ) -> tuple[StoredMessage, ...]: ...
