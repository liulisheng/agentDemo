"""MySQL Conversation/AgentBinding 与消息正文引用索引。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import AgentBinding, Conversation, MessageSource
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.conversation_repository import ConversationRepository, StoredMessage


class MySqlConversationRepository(ConversationRepository):
    """事务化写入对话索引；消息正文通过 LocalObjectStore 引用读取。"""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            yield connection, cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @contextmanager
    def _reader(self) -> Iterator[tuple[Any, Any]]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            yield connection, cursor
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    def save_conversation(self, identity: IdentityContext, value: Conversation) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO conversations "
                "(platform_id, tenant_id, conversation_id, title, is_group, status, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.conversation_id,
                    value.title,
                    value.is_group,
                    value.status,
                    identity.user_id,
                ),
            )
            cursor.execute(
                "INSERT INTO conversation_participants (platform_id, tenant_id, conversation_id, user_id, role) "
                "VALUES (%s,%s,%s,%s,'owner')",
                (identity.platform_id, identity.tenant_id, value.conversation_id, identity.user_id),
            )

    def get_conversation(self, identity: IdentityContext, conversation_id: str) -> Conversation:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT conversation_id, platform_id, tenant_id, title, is_group, status FROM conversations "
                "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
                (identity.platform_id, identity.tenant_id, conversation_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("conversation")
        return Conversation(str(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]), str(row[5]))

    def save_binding(self, identity: IdentityContext, value: AgentBinding) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT employee_id, employee_version, deployment_id FROM sessions "
                "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.session_id),
            )
            row = cursor.fetchone()
            if row is None or str(row[0]) != value.employee_id:
                raise self._not_found("session")
            cursor.execute(
                "INSERT INTO agent_bindings "
                "(platform_id, tenant_id, binding_id, conversation_id, employee_id, employee_version, deployment_id, session_id, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE employee_version=VALUES(employee_version), deployment_id=VALUES(deployment_id), "
                "session_id=VALUES(session_id), status=VALUES(status)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    generate_database_id(),
                    value.conversation_id,
                    value.employee_id,
                    int(row[1]),
                    str(row[2]),
                    value.session_id,
                    value.status,
                ),
            )

    def list_bindings(self, identity: IdentityContext, conversation_id: str) -> tuple[AgentBinding, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT b.conversation_id, b.employee_id, e.name, b.session_id, b.status "
                "FROM agent_bindings b JOIN digital_employees e ON e.platform_id=b.platform_id "
                "AND e.tenant_id=b.tenant_id AND e.employee_id=b.employee_id "
                "WHERE b.platform_id=%s AND b.tenant_id=%s AND b.conversation_id=%s "
                "ORDER BY b.created_at, b.binding_id",
                (identity.platform_id, identity.tenant_id, conversation_id),
            )
            rows = cursor.fetchall()
        return tuple(AgentBinding(*map(str, row)) for row in rows)

    @staticmethod
    def _stored_message(row: tuple[Any, ...]) -> StoredMessage:
        role_source = MessageSource(str(row[5]))
        return StoredMessage(
            message_id=str(row[0]),
            conversation_id=str(row[1]),
            platform_id=str(row[2]),
            tenant_id=str(row[3]),
            sender_id=str(row[4]),
            source=role_source,
            content_ref=str(row[6]),
            content_sha256=str(row[7]),
            byte_length=int(row[8]),
            content_type=str(row[9]),
            sequence_no=int(row[10]),
            routing_depth=int(row[11]),
        )

    def save_message(
        self,
        identity: IdentityContext,
        value: Any,
        *,
        session_id: str,
        content_ref: str,
        content_sha256: str,
        byte_length: int,
        content_type: str,
    ) -> StoredMessage:
        """锁定 Session 的消息序号，再以同一事务提交索引和正文引用。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT last_message_sequence FROM sessions "
                "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, session_id),
            )
            session = cursor.fetchone()
            if session is None:
                raise self._not_found("session")
            cursor.execute(
                "SELECT 1 FROM conversations WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
                (identity.platform_id, identity.tenant_id, value.conversation_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found("conversation")
            sequence = int(session[0]) + 1
            role = "USER" if value.source is MessageSource.USER else "ASSISTANT"
            sender_id = value.sender_id if value.source is MessageSource.USER else identity.user_id
            cursor.execute(
                "INSERT INTO messages "
                "(platform_id, tenant_id, message_id, session_id, run_id, sequence_no, role, status, "
                "content_ref, content_sha256, byte_length, content_type, sender_id, source, routing_depth, "
                "conversation_id, retention_policy) "
                "VALUES (%s,%s,%s,%s,NULL,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,'conversation')",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.message_id,
                    session_id,
                    sequence,
                    role,
                    content_ref,
                    content_sha256,
                    byte_length,
                    content_type,
                    sender_id,
                    value.source.value,
                    value.routing_depth,
                    value.conversation_id,
                ),
            )
            cursor.execute(
                "UPDATE sessions SET last_message_sequence=%s WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
                (sequence, identity.platform_id, identity.tenant_id, session_id),
            )
        return StoredMessage(
            value.message_id,
            value.conversation_id,
            identity.platform_id,
            identity.tenant_id,
            sender_id,
            value.source,
            content_ref,
            content_sha256,
            byte_length,
            content_type,
            sequence,
            value.routing_depth,
        )

    def get_message(self, identity: IdentityContext, message_id: str) -> StoredMessage:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT message_id, conversation_id, platform_id, tenant_id, sender_id, source, content_ref, "
                "content_sha256, byte_length, content_type, sequence_no, routing_depth FROM messages "
                "WHERE platform_id=%s AND tenant_id=%s AND message_id=%s",
                (identity.platform_id, identity.tenant_id, message_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("message")
        return self._stored_message(row)

    def list_messages(
        self, identity: IdentityContext, conversation_id: str
    ) -> tuple[StoredMessage, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT message_id, conversation_id, platform_id, tenant_id, sender_id, source, content_ref, "
                "content_sha256, byte_length, content_type, sequence_no, routing_depth FROM messages "
                "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s AND status='COMPLETE' "
                "ORDER BY sequence_no, message_id",
                (identity.platform_id, identity.tenant_id, conversation_id),
            )
            rows = cursor.fetchall()
        return tuple(self._stored_message(row) for row in rows)
