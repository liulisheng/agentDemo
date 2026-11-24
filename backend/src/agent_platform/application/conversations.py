"""Conversation、消息路由和可重试渠道投递服务。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime

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
from agent_platform.ports.conversation_repository import (
    ConversationRepository,
    ConversationSummaryRecord,
    StoredMessage,
)
from agent_platform.ports.storage import ObjectStore, UploadRequest


@dataclass(frozen=True, slots=True)
class ConversationLastMessagePreview:
    """面向工作台的最后一条消息摘要，不包含对象引用或完整正文。"""

    message_id: str
    preview: str
    source: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConversationListItem:
    """已按当前用户权限聚合的员工工作台列表项。"""

    conversation_id: str
    title: str
    employee_id: str
    employee_name: str
    session_id: str
    last_message: ConversationLastMessagePreview | None
    last_run_id: str | None
    last_run_status: str | None
    pending_approval_count: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationListPage:
    """稳定游标 Conversation 列表页。"""

    items: tuple[ConversationListItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _MemoryConversationSummary:
    """无 MySQL 的 Fake 列表行；正文只留在进程内服务缓存。"""

    conversation: Conversation
    updated_at: datetime
    employee_id: str
    employee_name: str
    session_id: str
    last_message: Message | None
    last_run_id: str | None = None
    last_run_status: str | None = None
    pending_approval_count: int = 0


_MAX_PREVIEW_CHARS = 120
_MAX_PAGE_SIZE = 100


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
        self._participants: dict[tuple[str, str, str], set[str]] = {}
        self._bindings: dict[tuple[str, str, str], list[AgentBinding]] = {}
        self._messages: dict[tuple[str, str, str], Message] = {}
        self._run_summaries: dict[tuple[str, str, str, str], tuple[str, str]] = {}
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
        scope = self._scope(identity, conversation.conversation_id)
        self._conversations[scope] = conversation
        self._participants[scope] = {identity.user_id}
        return conversation

    def resolve_active_binding(
        self,
        identity: IdentityContext,
        conversation_id: str,
        employee_id: str | None = None,
    ) -> AgentBinding:
        """解析当前用户有权访问的单一活动员工绑定。"""

        self._get_conversation(identity, conversation_id)
        bindings = tuple(
            binding
            for binding in self._list_bindings(identity, conversation_id)
            if binding.status == "ACTIVE"
            and (employee_id is None or binding.employee_id == employee_id)
        )
        if not bindings:
            raise DomainError("RESOURCE_NOT_FOUND", "员工绑定不存在", {"resource": "agent_binding"})
        if len(bindings) > 1:
            raise DomainError(
                "INVALID_ARGUMENT",
                "此 Conversation 绑定了多个员工，请明确指定 employee_id",
            )
        return bindings[0]

    def list_for_employee(
        self,
        identity: IdentityContext,
        employee_id: str,
        *,
        status: str = "ACTIVE",
        cursor: str | None = None,
        limit: int = 20,
    ) -> ConversationListPage:
        """按租户、参与者和员工绑定稳定分页并按需读取最后消息摘要。"""

        if not isinstance(employee_id, str) or not employee_id.strip():
            raise DomainError("INVALID_ARGUMENT", "employee_id 不能为空")
        if status not in {"ACTIVE", "ARCHIVED", "DELETED"}:
            raise DomainError("INVALID_ARGUMENT", "Conversation status 不受支持")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 100 之间")
        decoded_cursor = self._decode_cursor(cursor) if cursor is not None else None
        if self.repository is None:
            records = self._list_memory_summaries(
                identity, employee_id.strip(), status, decoded_cursor, limit + 1
            )
        else:
            records = self.repository.list_conversation_summaries(
                identity,
                employee_id.strip(),
                status,
                decoded_cursor,
                limit + 1,
            )

        has_more = len(records) > limit
        selected = records[:limit]
        next_cursor = (
            self._encode_cursor(selected[-1].updated_at, selected[-1].conversation.conversation_id)
            if has_more and selected
            else None
        )
        return ConversationListPage(
            items=tuple(self._list_item(identity, record) for record in selected),
            next_cursor=next_cursor,
        )

    def bind_agent(
        self, identity: IdentityContext, conversation_id: str, employee_id: str, employee_name: str, session_id: str
    ) -> AgentBinding:
        conversation = self._get_conversation(identity, conversation_id)
        binding = AgentBinding(conversation.conversation_id, employee_id, employee_name, session_id)
        if self.repository is not None:
            self.repository.save_binding(identity, binding)
        self._bindings.setdefault(
            (identity.platform_id, identity.tenant_id, conversation_id), []
        ).append(binding)
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
        message_id: str | None = None,
    ) -> Message:
        conversation = self._get_conversation(identity, conversation_id)
        message = Message(
            message_id or generate_database_id(),
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
            content_ref, content_sha256, byte_length, content_type = self.store_message_body(
                identity, message
            )
            stored = self.repository.save_message(
                identity,
                message,
                session_id=bound_session,
                content_ref=content_ref,
                content_sha256=content_sha256,
                byte_length=byte_length,
                content_type=content_type,
            )
            message = replace(message, created_at=stored.created_at)
        self.remember_message(identity, message)
        return message

    def store_message_body(
        self, identity: IdentityContext, message: Message
    ) -> tuple[str, str, int, str]:
        """先将正文写入租户对象存储，返回供事务索引使用的 opaque 引用。"""

        if self.object_store is None:
            raise DomainError("INVALID_STATE", "持久化消息需要配置 ObjectStore")
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
        return asset.object_ref, asset.sha256, asset.size_bytes, asset.mime_type

    def remember_message(self, identity: IdentityContext, message: Message) -> None:
        """缓存本次已提交的消息，并推进进程内 Conversation 活动时间。"""

        scope = self._scope(identity, message.conversation_id)
        self._conversations[scope] = replace(
            self._get_conversation(identity, message.conversation_id),
            updated_at=datetime.now(UTC),
        )
        self._messages[(identity.platform_id, identity.tenant_id, message.message_id)] = message

    def note_run(
        self,
        identity: IdentityContext,
        conversation_id: str,
        employee_id: str,
        run_id: str,
        status: str,
    ) -> None:
        """更新 Fake 工作台的 Run 摘要；持久化列表从 runs 表聚合。"""

        self._get_conversation(identity, conversation_id)
        scope = self._scope(identity, conversation_id)
        self._run_summaries[
            (identity.platform_id, identity.tenant_id, conversation_id, employee_id)
        ] = (run_id, status)
        self._conversations[scope] = replace(
            self._conversations[scope], updated_at=datetime.now(UTC)
        )

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
        scope = self._scope(identity, conversation_id)
        try:
            conversation = self._conversations[scope]
            if identity.user_id not in self._participants.get(scope, set()):
                raise DomainError("RESOURCE_NOT_FOUND", "会话不存在", {"resource": "conversation"})
            return conversation
        except KeyError as error:
            if self.repository is not None:
                conversation = self.repository.get_conversation(identity, conversation_id)
                self._conversations[scope] = conversation
                self._participants.setdefault(scope, set()).add(identity.user_id)
                return conversation
            raise DomainError("RESOURCE_NOT_FOUND", "会话不存在", {"resource": "conversation"}) from error

    def get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation:
        return self._get_conversation(identity, conversation_id)

    def _get_message(self, identity: IdentityContext, message_id: str) -> Message:
        try:
            message = self._messages[(identity.platform_id, identity.tenant_id, message_id)]
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
                for (platform_id, tenant_id, _), message in self._messages.items()
                if platform_id == identity.platform_id
                and tenant_id == identity.tenant_id
                and message.conversation_id == conversation_id
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
        return tuple(
            self._bindings.get(
                (identity.platform_id, identity.tenant_id, conversation_id), ()
            )
        )

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
            created_at=stored.created_at,
        )

    def _list_memory_summaries(
        self,
        identity: IdentityContext,
        employee_id: str,
        status: str,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[_MemoryConversationSummary, ...]:
        rows: list[_MemoryConversationSummary] = []
        for (platform_id, tenant_id, conversation_id), conversation in self._conversations.items():
            if (
                platform_id != identity.platform_id
                or tenant_id != identity.tenant_id
                or conversation.status != status
                or identity.user_id
                not in self._participants.get((platform_id, tenant_id, conversation_id), set())
            ):
                continue
            if not any(
                binding.employee_id == employee_id and binding.status == "ACTIVE"
                for binding in self._bindings.get(
                    (identity.platform_id, identity.tenant_id, conversation_id), ()
                )
            ):
                continue
            messages = [
                message
                for (message_platform, message_tenant, _), message in self._messages.items()
                if message_platform == identity.platform_id
                and message_tenant == identity.tenant_id
                and message.conversation_id == conversation_id
            ]
            last_message = messages[-1] if messages else None
            binding = next(
                binding
                for binding in self._bindings[
                    (identity.platform_id, identity.tenant_id, conversation_id)
                ]
                if binding.employee_id == employee_id and binding.status == "ACTIVE"
            )
            last_run = self._run_summaries.get(
                (identity.platform_id, identity.tenant_id, conversation_id, employee_id)
            )
            rows.append(
                _MemoryConversationSummary(
                    conversation=conversation,
                    updated_at=conversation.updated_at,
                    employee_id=binding.employee_id,
                    employee_name=binding.employee_name,
                    session_id=binding.session_id,
                    last_message=last_message,
                    last_run_id=last_run[0] if last_run else None,
                    last_run_status=last_run[1] if last_run else None,
                )
            )
        rows.sort(
            key=lambda row: (row.updated_at, row.conversation.conversation_id),
            reverse=True,
        )
        if cursor is not None:
            rows = [
                row
                for row in rows
                if (row.updated_at, row.conversation.conversation_id) < cursor
            ]
        return tuple(rows[:limit])

    def _list_item(
        self,
        identity: IdentityContext,
        record: ConversationSummaryRecord | _MemoryConversationSummary,
    ) -> ConversationListItem:
        stored_message = record.last_message
        preview: ConversationLastMessagePreview | None = None
        if isinstance(stored_message, Message):
            body = stored_message.text
            message_id = stored_message.message_id
            message_source = stored_message.source.value
            message_created_at = None
        elif isinstance(stored_message, StoredMessage):
            if self.object_store is None:
                raise DomainError("INVALID_STATE", "读取 Conversation 摘要需要配置 ObjectStore")
            try:
                body = self.object_store.read(
                    stored_message.content_ref,
                    identity.platform_id,
                    identity.tenant_id,
                ).decode("utf-8")
            except (PermissionError, UnicodeDecodeError):
                body = ""
            message_id = stored_message.message_id
            message_source = stored_message.source.value
            message_created_at = stored_message.created_at
        else:
            body = ""
            message_id = ""
            message_source = ""
            message_created_at = None

        if message_id:
            normalized = " ".join(body.split())
            text = (
                normalized[:_MAX_PREVIEW_CHARS] + "…"
                if len(normalized) > _MAX_PREVIEW_CHARS
                else normalized
            )
            if text:
                preview = ConversationLastMessagePreview(
                    message_id=message_id,
                    preview=text,
                    source=message_source,
                    created_at=message_created_at,
                )
        return ConversationListItem(
            conversation_id=record.conversation.conversation_id,
            title=record.conversation.title,
            employee_id=record.employee_id,
            employee_name=record.employee_name,
            session_id=record.session_id,
            last_message=preview,
            last_run_id=record.last_run_id,
            last_run_status=record.last_run_status,
            pending_approval_count=record.pending_approval_count,
            updated_at=self._as_utc(record.updated_at),
        )

    @staticmethod
    def _encode_cursor(updated_at: datetime, conversation_id: str) -> str:
        payload = json.dumps(
            {
                "updated_at": ConversationService._as_utc(updated_at).isoformat(),
                "conversation_id": conversation_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
                raise ValueError("cursor length")
            padding = "=" * ((4 - len(cursor) % 4) % 4)
            payload = base64.b64decode(
                cursor + padding, altchars=b"-_", validate=True
            )
            value = json.loads(payload.decode("utf-8"))
            updated_at = datetime.fromisoformat(str(value["updated_at"]))
            conversation_id = str(value["conversation_id"])
            if updated_at.tzinfo is None or not conversation_id.strip():
                raise ValueError("cursor values")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as error:
            raise DomainError("INVALID_CURSOR", "Conversation cursor 无效") from error
        return ConversationService._as_utc(updated_at), conversation_id

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
