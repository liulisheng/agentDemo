"""Conversation、消息路由和投递 API。"""

from __future__ import annotations

from fastapi import APIRouter

from agent_platform.api.schemas import (
    AgentBindingRequest,
    ConversationCreateRequest,
    DeliveryCreateRequest,
    MessageCreateRequest,
)

from .common import Conversations, Identity, _check_tenant

router = APIRouter()

@router.post("/conversations", status_code=201)
def create_conversation(request: ConversationCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    conversation = service.create_conversation(identity, request.title, is_group=request.is_group)
    return {"id": conversation.conversation_id, "title": conversation.title, "is_group": conversation.is_group, "status": conversation.status}


@router.post("/conversations/{conversation_id}/bindings", status_code=201)
def bind_conversation_agent(conversation_id: str, request: AgentBindingRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    binding = service.bind_agent(identity, conversation_id, request.employee_id, request.employee_name, request.session_id)
    return {"employee_id": binding.employee_id, "employee_name": binding.employee_name, "session_id": binding.session_id, "status": binding.status}


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def ingest_conversation_message(conversation_id: str, request: MessageCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    message = service.ingest(identity, conversation_id, request.sender_id, request.text, source=request.source, routing_depth=request.routing_depth)
    return {"id": message.message_id, "conversation_id": message.conversation_id, "source": message.source.value, "routing_depth": message.routing_depth}


@router.get("/conversations/{conversation_id}/messages")
def list_conversation_messages(
    conversation_id: str, identity: Identity, service: Conversations
) -> dict[str, object]:
    """按租户恢复消息正文；持久仓储只保存正文对象引用。"""

    return {
        "items": [
            {
                "id": message.message_id,
                "conversation_id": message.conversation_id,
                "sender_id": message.sender_id,
                "source": message.source.value,
                "text": message.text,
                "routing_depth": message.routing_depth,
            }
            for message in service.list_messages(identity, conversation_id)
        ]
    }


@router.post("/conversations/{conversation_id}/messages/{message_id}/route")
def route_conversation_message(conversation_id: str, message_id: str, identity: Identity, service: Conversations) -> dict[str, object]:
    sessions = service.route(identity, message_id)
    return {"message_id": message_id, "conversation_id": conversation_id, "session_ids": list(sessions)}


@router.post("/conversations/{conversation_id}/deliveries", status_code=201)
def create_delivery(conversation_id: str, request: DeliveryCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    delivery = service.deliver(identity, conversation_id, request.body, request.idempotency_key)
    return {"id": delivery.delivery_id, "status": delivery.status.value, "retry_count": delivery.retry_count}
