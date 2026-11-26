"""Conversation、消息路由和投递 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from agent_platform.api.schemas import (
    AgentBindingRequest,
    ConversationCreateRequest,
    ConversationTurnRequest,
    DeliveryCreateRequest,
    MessageCreateRequest,
)

from .common import Conversations, ConversationTurns, Identity, _check_tenant

router = APIRouter()


@router.get("/conversations")
def list_conversations(
    identity: Identity,
    service: Conversations,
    employee_id: str = Query(min_length=1, max_length=64),
    status: str = Query(default="ACTIVE"),
    cursor: str | None = Query(default=None, max_length=2048),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """按认证用户、租户和数字员工绑定分页返回可恢复对话列表。"""

    page = service.list_for_employee(
        identity,
        employee_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return {
        "items": [
            {
                "conversation_id": item.conversation_id,
                "title": item.title,
                "employee": {
                    "employee_id": item.employee_id,
                    "employee_name": item.employee_name,
                },
                "session_id": item.session_id,
                "last_message": (
                    {
                        "message_id": item.last_message.message_id,
                        "preview": item.last_message.preview,
                        "source": item.last_message.source,
                        "created_at": item.last_message.created_at,
                    }
                    if item.last_message is not None
                    else None
                ),
                "last_run": (
                    {"run_id": item.last_run_id, "status": item.last_run_status}
                    if item.last_run_id is not None
                    else None
                ),
                "pending_approval_count": item.pending_approval_count,
                "updated_at": item.updated_at,
            }
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("/conversations/{conversation_id}/turns", status_code=202)
def submit_conversation_turn(
    conversation_id: str,
    request: ConversationTurnRequest,
    identity: Identity,
    service: ConversationTurns,
) -> dict[str, object]:
    """原子提交一条用户消息和 QUEUED Run，不等待员工 Runtime 完成。"""

    _check_tenant(identity, request)
    submission = service.submit(
        identity,
        conversation_id,
        request.text,
        request.idempotency_key,
        employee_id=request.employee_id,
    )
    return {
        "conversation_id": conversation_id,
        "message_id": submission.message.message_id,
        "run_id": submission.run.run_id,
        "status": submission.run.status.value.upper(),
        "replayed": not submission.created,
    }


@router.post("/conversations", status_code=201)
def create_conversation(request: ConversationCreateRequest, identity: Identity, service: Conversations) -> dict[str, object]:
    _check_tenant(identity, request)
    conversation = service.create_conversation(
        identity,
        request.title,
        is_group=request.is_group,
        conversation_mode=request.conversation_mode,
    )
    return {
        "id": conversation.conversation_id,
        "title": conversation.title,
        "is_group": conversation.is_group,
        "conversation_mode": conversation.conversation_mode.value,
        "status": conversation.status,
    }


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
                "sender_name": message.sender_name,
                "team_member_id": message.team_member_id,
                "source": message.source.value,
                "text": message.text,
                "routing_depth": message.routing_depth,
                "created_at": message.created_at,
            }
            # SYSTEM Message 可作为受限 Run Worker 输入载荷，不能从用户可见对话 API 回传。
            for message in service.list_messages(identity, conversation_id)
            if message.source.value != "system"
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
