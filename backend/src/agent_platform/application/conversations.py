"""Conversation、消息路由和可重试渠道投递服务。"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.conversations import (
    AgentBinding,
    Conversation,
    DeliveryStatus,
    Message,
    MessageSource,
    OutboundDelivery,
)
from agent_platform.domain.identity import IdentityContext


class ConversationService:
    """维护共享 Conversation，按明确触发规则启动绑定的 Agent Session。"""

    def __init__(self) -> None:
        self._conversations: dict[tuple[str, str, str], Conversation] = {}
        self._bindings: dict[tuple[str, str], list[AgentBinding]] = {}
        self._messages: dict[tuple[str, str], Message] = {}
        self._deliveries: dict[tuple[str, str], OutboundDelivery] = {}
        self._routing_events: list[dict[str, object]] = []

    @staticmethod
    def _scope(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
        return identity.platform_id, identity.tenant_id, resource_id

    def create_conversation(self, identity: IdentityContext, title: str, *, is_group: bool) -> Conversation:
        conversation = Conversation(
            f"conversation-{uuid4().hex}", identity.platform_id, identity.tenant_id, title, is_group
        )
        self._conversations[self._scope(identity, conversation.conversation_id)] = conversation
        return conversation

    def bind_agent(
        self, identity: IdentityContext, conversation_id: str, employee_id: str, employee_name: str, session_id: str
    ) -> AgentBinding:
        conversation = self._get_conversation(identity, conversation_id)
        binding = AgentBinding(conversation.conversation_id, employee_id, employee_name, session_id)
        self._bindings.setdefault((identity.tenant_id, conversation_id), []).append(binding)
        return binding

    def ingest(
        self,
        identity: IdentityContext,
        conversation_id: str,
        sender_id: str,
        text: str,
        *,
        source: str = "user",
        routing_depth: int = 0,
    ) -> Message:
        conversation = self._get_conversation(identity, conversation_id)
        message = Message(
            f"message-{uuid4().hex}",
            conversation.conversation_id,
            identity.platform_id,
            identity.tenant_id,
            sender_id,
            text,
            MessageSource(source),
            routing_depth=routing_depth,
        )
        self._messages[(identity.tenant_id, message.message_id)] = message
        return message

    def route(self, identity: IdentityContext, message_id: str) -> tuple[str, ...]:
        message = self._get_message(identity, message_id)
        conversation = self._get_conversation(identity, message.conversation_id)
        if message.source is MessageSource.AGENT or message.routing_depth > 0:
            self._routing_events.append({"type": "loop_prevented", "message_id": message_id})
            return ()
        bindings = [
            item for item in self._bindings.get((identity.tenant_id, conversation.conversation_id), []) if item.status == "ACTIVE"
        ]
        if not conversation.is_group:
            return tuple(item.session_id for item in bindings)
        selected: list[AgentBinding] = []
        for binding in bindings:
            if f"@{binding.employee_name}" in message.text or message.text.startswith(f"/{binding.employee_name}"):
                selected.append(binding)
        if not selected:
            self._routing_events.append({"type": "message_saved_without_run", "message_id": message_id})
            return ()
        return tuple(item.session_id for item in selected)

    def deliver(
        self, identity: IdentityContext, conversation_id: str, body: str, idempotency_key: str
    ) -> OutboundDelivery:
        self._get_conversation(identity, conversation_id)
        key = (identity.tenant_id, idempotency_key)
        if key in self._deliveries:
            return self._deliveries[key]
        delivery = OutboundDelivery(
            f"delivery-{uuid4().hex}", conversation_id, identity.tenant_id, body, idempotency_key
        )
        self._deliveries[key] = delivery
        return delivery

    def retry_delivery(self, identity: IdentityContext, delivery_id: str) -> OutboundDelivery:
        for key, delivery in self._deliveries.items():
            if delivery.delivery_id == delivery_id and key[0] == identity.tenant_id:
                updated = replace(delivery, status=DeliveryStatus.RETRYING, retry_count=delivery.retry_count + 1)
                self._deliveries[key] = updated
                return updated
        raise DomainError("RESOURCE_NOT_FOUND", "投递不存在", {"resource": "delivery"})

    def _get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation:
        try:
            return self._conversations[self._scope(identity, conversation_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "会话不存在", {"resource": "conversation"}) from error

    def _get_message(self, identity: IdentityContext, message_id: str) -> Message:
        try:
            message = self._messages[(identity.tenant_id, message_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "消息不存在", {"resource": "message"}) from error
        if message.platform_id != identity.platform_id:
            raise DomainError("RESOURCE_NOT_FOUND", "消息不存在", {"resource": "message"})
        return message
