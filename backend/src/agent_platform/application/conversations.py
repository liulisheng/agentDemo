"""Conversation、消息路由和可重试渠道投递服务。"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import (
    AgentBinding,
    Conversation,
    DeliveryStatus,
    Message,
    MessageSource,
    OutboundDelivery,
)
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.conversation_repository import ConversationRepository, StoredMessage
from agent_platform.ports.storage import ObjectStore, UploadRequest


class ConversationService:
    """维护共享 Conversation，按明确触发规则启动绑定的 Agent Session。"""

    def __init__(
        self,
        repository: ConversationRepository | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self.repository = repository
        self.object_store = object_store
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
            generate_database_id(), identity.platform_id, identity.tenant_id, title, is_group
        )
        if self.repository is not None:
            self.repository.save_conversation(identity, conversation)
        self._conversations[self._scope(identity, conversation.conversation_id)] = conversation
        return conversation

    def bind_agent(
        self, identity: IdentityContext, conversation_id: str, employee_id: str, employee_name: str, session_id: str
    ) -> AgentBinding:
        conversation = self._get_conversation(identity, conversation_id)
        binding = AgentBinding(conversation.conversation_id, employee_id, employee_name, session_id)
        if self.repository is not None:
            self.repository.save_binding(identity, binding)
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
        session_id: str | None = None,
    ) -> Message:
        conversation = self._get_conversation(identity, conversation_id)
        message = Message(
            generate_database_id(),
            conversation.conversation_id,
            identity.platform_id,
            identity.tenant_id,
            sender_id,
            text,
            MessageSource(source),
            routing_depth=routing_depth,
        )
        if self.repository is not None:
            if self.object_store is None:
                raise DomainError("INVALID_STATE", "持久化消息需要配置 ObjectStore")
            binding = next(
                iter(self._list_bindings(identity, conversation.conversation_id)), None
            )
            bound_session = session_id or (binding.session_id if binding is not None else None)
            if bound_session is None:
                raise DomainError("INVALID_STATE", "保存消息前必须绑定 Agent Session")
            payload = message.text.encode("utf-8")
            upload = self.object_store.create_upload_intent(
                UploadRequest(
                    platform_id=identity.platform_id,
                    tenant_id=identity.tenant_id,
                    filename=f"message-{message.message_id}.txt",
                    mime_type="text/plain; charset=utf-8",
                    size_bytes=len(payload),
                    retention_policy="conversation",
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
            asset = self.object_store.complete_upload(upload.upload_id, payload)
            self.repository.save_message(
                identity,
                message,
                session_id=bound_session,
                content_ref=asset.object_ref,
                content_sha256=asset.sha256,
                byte_length=asset.size_bytes,
                content_type=asset.mime_type,
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
            item
            for item in self._list_bindings(identity, conversation.conversation_id)
            if item.status == "ACTIVE"
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
            generate_database_id(), conversation_id, identity.tenant_id, body, idempotency_key
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
            if self.repository is not None:
                return self.repository.get_conversation(identity, conversation_id)
            raise DomainError("RESOURCE_NOT_FOUND", "会话不存在", {"resource": "conversation"}) from error

    def get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation:
        return self._get_conversation(identity, conversation_id)

    def _get_message(self, identity: IdentityContext, message_id: str) -> Message:
        try:
            message = self._messages[(identity.tenant_id, message_id)]
        except KeyError as error:
            if self.repository is not None and self.object_store is not None:
                stored = self.repository.get_message(identity, message_id)
                return self._hydrate_message(identity, stored)
            raise DomainError("RESOURCE_NOT_FOUND", "消息不存在", {"resource": "message"}) from error
        if message.platform_id != identity.platform_id:
            raise DomainError("RESOURCE_NOT_FOUND", "消息不存在", {"resource": "message"})
        return message

    def list_messages(self, identity: IdentityContext, conversation_id: str) -> tuple[Message, ...]:
        self._get_conversation(identity, conversation_id)
        if self.repository is None:
            return tuple(
                message
                for (tenant_id, _), message in self._messages.items()
                if tenant_id == identity.tenant_id and message.conversation_id == conversation_id
            )
        if self.object_store is None:
            raise DomainError("INVALID_STATE", "读取持久化消息需要配置 ObjectStore")
        return tuple(
            self._hydrate_message(identity, stored)
            for stored in self.repository.list_messages(identity, conversation_id)
        )

    def _list_bindings(self, identity: IdentityContext, conversation_id: str) -> tuple[AgentBinding, ...]:
        if self.repository is not None:
            return self.repository.list_bindings(identity, conversation_id)
        return tuple(self._bindings.get((identity.tenant_id, conversation_id), ()))

    def _hydrate_message(self, identity: IdentityContext, stored: StoredMessage) -> Message:
        if self.object_store is None:
            raise DomainError("INVALID_STATE", "读取持久化消息需要配置 ObjectStore")
        try:
            body = self.object_store.read(
                stored.content_ref, identity.platform_id, identity.tenant_id
            ).decode("utf-8")
        except (PermissionError, UnicodeDecodeError) as error:
            raise DomainError("RESOURCE_NOT_FOUND", "消息不存在") from error
        return Message(
            stored.message_id,
            stored.conversation_id,
            stored.platform_id,
            stored.tenant_id,
            stored.sender_id,
            body,
            stored.source,
            routing_depth=stored.routing_depth,
        )
