"""MySQL Conversation/AgentBinding 与消息正文引用索引。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import AgentBinding, Conversation, MessageSource
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.conversation_repository import (
    ConversationRepository,
    ConversationSummaryRecord,
    StoredMessage,
)


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
                "(platform_id, tenant_id, conversation_id, title, is_group, conversation_mode, status, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.conversation_id,
                    value.title,
                    value.is_group,
                    value.conversation_mode.value,
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
                "SELECT c.conversation_id, c.platform_id, c.tenant_id, c.title, c.is_group, c.status, c.updated_at, c.conversation_mode "
                "FROM conversations c JOIN conversation_participants cp ON cp.platform_id=c.platform_id "
                "AND cp.tenant_id=c.tenant_id AND cp.conversation_id=c.conversation_id AND cp.user_id=%s "
                "WHERE c.platform_id=%s AND c.tenant_id=%s AND c.conversation_id=%s",
                (identity.user_id, identity.platform_id, identity.tenant_id, conversation_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("conversation")
        return Conversation(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            bool(row[4]),
            str(row[5]),
            row[6],
            conversation_mode=str(row[7]),
        )

    def get_conversation_id_for_run(self, identity: IdentityContext, run_id: str) -> str:
        """从同租户、同 Run 的 USER-role 输入解析审批归属 Conversation；内部 SYSTEM 输入也属于该 Run。"""

        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT conversation_id FROM messages WHERE platform_id=%s AND tenant_id=%s "
                "AND run_id=%s AND role='USER' AND status='COMPLETE' ORDER BY sequence_no LIMIT 1",
                (identity.platform_id, identity.tenant_id, run_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("run_conversation")
        return str(row[0])

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
                "(platform_id, tenant_id, binding_id, conversation_id, employee_id, employee_version, deployment_id, session_id, team_member_id, team_member_role, capabilities_snapshot_json, status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE employee_version=VALUES(employee_version), deployment_id=VALUES(deployment_id), "
                "session_id=VALUES(session_id), team_member_id=VALUES(team_member_id), team_member_role=VALUES(team_member_role), "
                "capabilities_snapshot_json=VALUES(capabilities_snapshot_json), status=VALUES(status)",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    generate_database_id(),
                    value.conversation_id,
                    value.employee_id,
                    int(row[1]),
                    str(row[2]),
                    value.session_id,
                    value.team_member_id,
                    value.team_member_role,
                    json.dumps(list(value.capabilities_snapshot), ensure_ascii=False),
                    value.status,
                ),
            )

    def list_bindings(self, identity: IdentityContext, conversation_id: str) -> tuple[AgentBinding, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT b.conversation_id, b.employee_id, e.name, b.session_id, b.status, b.team_member_id,b.team_member_role,b.capabilities_snapshot_json "
                "FROM agent_bindings b JOIN digital_employees e ON e.platform_id=b.platform_id "
                "AND e.tenant_id=b.tenant_id AND e.employee_id=b.employee_id "
                "WHERE b.platform_id=%s AND b.tenant_id=%s AND b.conversation_id=%s "
                "ORDER BY b.created_at, b.binding_id",
                (identity.platform_id, identity.tenant_id, conversation_id),
            )
            rows = cursor.fetchall()
        return tuple(
            AgentBinding(
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), row[5], row[6],
                tuple(json.loads(row[7] or "[]")),
            )
            for row in rows
        )

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
            created_at=row[12] if len(row) > 12 else None,
            sender_name=str(row[13]) if len(row) > 13 and row[13] is not None else None,
            team_member_id=str(row[14]) if len(row) > 14 and row[14] is not None else None,
        )

    def save_message(
        self,
        identity: IdentityContext,
        value: Any,
        *,
        session_id: str,
        run_id: str | None = None,
        content_ref: str,
        content_sha256: str,
        byte_length: int,
        content_type: str,
    ) -> StoredMessage:
        """锁定 Session 的消息序号，再以同一事务提交索引和正文引用。"""

        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT message_id,conversation_id,platform_id,tenant_id,sender_id,source,"
                "content_ref,content_sha256,byte_length,content_type,sequence_no,routing_depth,created_at "
                "FROM messages WHERE platform_id=%s AND tenant_id=%s AND message_id=%s FOR UPDATE",
                (identity.platform_id, identity.tenant_id, value.message_id),
            )
            previous_message = cursor.fetchone()
            if previous_message is not None:
                stored = self._stored_message(previous_message)
                if (
                    stored.conversation_id != value.conversation_id
                    or stored.source is not value.source
                    or stored.content_sha256 != content_sha256
                    or stored.byte_length != byte_length
                ):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Message ID 对应的内容或 Conversation 已改变")
                return stored
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
            # Agent 的显示身份由 Session 对应的 AgentBinding 联查恢复；sender_id 保持用户外键语义。
            sender_id = value.sender_id if value.source is MessageSource.USER else identity.user_id
            if run_id is not None and value.source is MessageSource.AGENT:
                cursor.execute(
                    "SELECT message_id, conversation_id, platform_id, tenant_id, sender_id, source, "
                    "content_ref, content_sha256, byte_length, content_type, sequence_no, routing_depth, created_at "
                    "FROM messages WHERE platform_id=%s AND tenant_id=%s AND run_id=%s "
                    "AND role='ASSISTANT' AND source='agent' LIMIT 1",
                    (identity.platform_id, identity.tenant_id, run_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return self._stored_message(existing)
            cursor.execute(
                "INSERT INTO messages "
                "(platform_id, tenant_id, message_id, session_id, run_id, sequence_no, role, status, "
                "content_ref, content_sha256, byte_length, content_type, sender_id, source, routing_depth, "
                "conversation_id, retention_policy) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,'conversation')",
                (
                    identity.platform_id,
                    identity.tenant_id,
                    value.message_id,
                    session_id,
                    run_id,
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
            cursor.execute(
                "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s",
                (identity.platform_id, identity.tenant_id, value.conversation_id),
            )
            cursor.execute(
                "SELECT created_at FROM messages WHERE platform_id=%s AND tenant_id=%s AND message_id=%s",
                (identity.platform_id, identity.tenant_id, value.message_id),
            )
            created_at = cursor.fetchone()[0]
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
            created_at,
            value.sender_name,
            value.team_member_id,
        )

    def get_message(self, identity: IdentityContext, message_id: str) -> StoredMessage:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT m.message_id,m.conversation_id,m.platform_id,m.tenant_id,m.sender_id,m.source,m.content_ref,"
                "m.content_sha256,m.byte_length,m.content_type,m.sequence_no,m.routing_depth,m.created_at,e.name,b.team_member_id "
                "FROM messages m LEFT JOIN agent_bindings b ON b.platform_id=m.platform_id AND b.tenant_id=m.tenant_id "
                "AND b.conversation_id=m.conversation_id AND b.session_id=m.session_id AND b.status='ACTIVE' AND m.source='agent' "
                "LEFT JOIN digital_employees e ON e.platform_id=b.platform_id AND e.tenant_id=b.tenant_id AND e.employee_id=b.employee_id "
                "WHERE m.platform_id=%s AND m.tenant_id=%s AND m.message_id=%s",
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
                "SELECT m.message_id,m.conversation_id,m.platform_id,m.tenant_id,m.sender_id,m.source,m.content_ref,"
                "m.content_sha256,m.byte_length,m.content_type,m.sequence_no,m.routing_depth,m.created_at,e.name,b.team_member_id "
                "FROM messages m LEFT JOIN agent_bindings b ON b.platform_id=m.platform_id AND b.tenant_id=m.tenant_id "
                "AND b.conversation_id=m.conversation_id AND b.session_id=m.session_id AND b.status='ACTIVE' AND m.source='agent' "
                "LEFT JOIN digital_employees e ON e.platform_id=b.platform_id AND e.tenant_id=b.tenant_id AND e.employee_id=b.employee_id "
                "WHERE m.platform_id=%s AND m.tenant_id=%s AND m.conversation_id=%s AND m.status='COMPLETE' "
                "ORDER BY m.sequence_no, m.message_id",
                (identity.platform_id, identity.tenant_id, conversation_id),
            )
            rows = cursor.fetchall()
        return tuple(self._stored_message(row) for row in rows)

    def list_conversation_summaries(
        self,
        identity: IdentityContext,
        employee_id: str,
        status: str,
        cursor: tuple[Any, str] | None,
        limit: int,
    ) -> tuple[ConversationSummaryRecord, ...]:
        """按当前用户参与权和员工绑定读取稳定游标聚合列表。"""

        cursor_filter = ""
        if cursor is not None:
            cursor_filter = "AND (c.updated_at < %s OR (c.updated_at=%s AND c.conversation_id < %s)) "
        query = (
            "SELECT c.conversation_id, c.platform_id, c.tenant_id, c.title, c.is_group, c.status, c.updated_at, "
            "b.employee_id, e.name, b.session_id, "
            "m.message_id, m.sender_id, m.source, m.content_ref, m.content_sha256, m.byte_length, "
            "m.content_type, m.sequence_no, m.routing_depth, m.created_at, "
            "lr.run_id, lr.state, "
            "(SELECT COUNT(*) FROM tool_approval_requests a "
            "WHERE a.platform_id=c.platform_id AND a.tenant_id=c.tenant_id "
            "AND a.conversation_id=c.conversation_id AND a.state='PENDING') "
            "FROM conversations c "
            "JOIN conversation_participants cp ON cp.platform_id=c.platform_id AND cp.tenant_id=c.tenant_id "
            "AND cp.conversation_id=c.conversation_id AND cp.user_id=%s "
            "JOIN agent_bindings b ON b.platform_id=c.platform_id AND b.tenant_id=c.tenant_id "
            "AND b.conversation_id=c.conversation_id AND b.employee_id=%s AND b.status='ACTIVE' "
            "JOIN digital_employees e ON e.platform_id=b.platform_id AND e.tenant_id=b.tenant_id "
            "AND e.employee_id=b.employee_id "
            "LEFT JOIN ("
            "SELECT ranked.* FROM ("
            "SELECT m0.*, ROW_NUMBER() OVER (PARTITION BY m0.platform_id, m0.tenant_id, m0.conversation_id "
            "ORDER BY m0.sequence_no DESC, m0.message_id DESC) AS message_rank "
            "FROM messages m0 WHERE m0.status='COMPLETE' AND m0.conversation_id IS NOT NULL"
            ") ranked WHERE ranked.message_rank=1"
            ") m ON m.platform_id=c.platform_id AND m.tenant_id=c.tenant_id "
            "AND m.conversation_id=c.conversation_id "
            "LEFT JOIN runs lr ON lr.platform_id=b.platform_id AND lr.tenant_id=b.tenant_id "
            "AND lr.session_id=b.session_id AND lr.run_id=("
            "SELECT r0.run_id FROM runs r0 WHERE r0.platform_id=b.platform_id "
            "AND r0.tenant_id=b.tenant_id AND r0.session_id=b.session_id "
            "ORDER BY r0.created_at DESC, r0.run_id DESC LIMIT 1) "
            "WHERE c.platform_id=%s AND c.tenant_id=%s AND c.status=%s "
            "AND c.conversation_mode='SINGLE_AGENT' "
            + cursor_filter
            + "ORDER BY c.updated_at DESC, c.conversation_id DESC LIMIT %s"
        )
        # SELECT 中参与者和员工绑定参数位于 WHERE 前，重排成 SQL 的占位符顺序。
        parameters = [
            identity.user_id,
            employee_id,
            identity.platform_id,
            identity.tenant_id,
            status,
            *([cursor[0], cursor[0], cursor[1]] if cursor is not None else []),
            limit,
        ]
        with self._reader() as (_, query_cursor):
            query_cursor.execute(query, tuple(parameters))
            rows = query_cursor.fetchall()
        return tuple(self._conversation_summary_from_row(row) for row in rows)

    @staticmethod
    def _conversation_summary_from_row(row: tuple[Any, ...]) -> ConversationSummaryRecord:
        conversation = Conversation(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            bool(row[4]),
            str(row[5]),
            row[6],
        )
        last_message = None
        if row[10] is not None:
            last_message = StoredMessage(
                message_id=str(row[10]),
                conversation_id=conversation.conversation_id,
                platform_id=conversation.platform_id,
                tenant_id=conversation.tenant_id,
                sender_id=str(row[11]),
                source=MessageSource(str(row[12])),
                content_ref=str(row[13]),
                content_sha256=str(row[14]),
                byte_length=int(row[15]),
                content_type=str(row[16]),
                sequence_no=int(row[17]),
                routing_depth=int(row[18]),
                created_at=row[19],
            )
        return ConversationSummaryRecord(
            conversation=conversation,
            updated_at=row[6],
            employee_id=str(row[7]),
            employee_name=str(row[8]),
            session_id=str(row[9]),
            last_message=last_message,
            last_run_id=str(row[20]) if row[20] is not None else None,
            last_run_status=str(row[21]) if row[21] is not None else None,
            pending_approval_count=int(row[22] or 0),
        )
