"""MySQL Conversation 列表的租户、参与者和聚合查询契约。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_platform.domain.identity import IdentityContext
from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository


class _Cursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row
        self.statement = ""
        self.parameters: tuple[object, ...] | None = None
        self.closed = False

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchall(self) -> list[tuple[object, ...]]:
        return [self.row]

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.query = _Cursor(row)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self.query

    def close(self) -> None:
        self.closed = True


def _row() -> tuple[object, ...]:
    return (
        "conversation-1",
        "platform-1",
        "tenant-1",
        "数仓助手",
        False,
        "ACTIVE",
        datetime(2026, 11, 19, 8, 30, tzinfo=UTC),
        "employee-1",
        "海豚调度员工",
        "session-1",
        "message-1",
        "user-1",
        "user",
        "object:message-1",
        "a" * 64,
        24,
        "text/plain; charset=utf-8",
        3,
        0,
        datetime(2026, 11, 19, 8, 29, tzinfo=UTC),
        "run-1",
        "AWAITING_APPROVAL",
        1,
    )


def test_conversation_summary_query_scopes_participant_employee_and_cursor() -> None:
    connection = _Connection(_row())
    repository = MySqlConversationRepository(lambda: connection)
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())
    cursor = (datetime(2026, 11, 19, 8, 31, tzinfo=UTC), "conversation-9")

    result = repository.list_conversation_summaries(
        identity, "employee-1", "ACTIVE", cursor, 25
    )

    sql = connection.query.statement
    assert "conversation_participants" in sql
    assert "agent_bindings" in sql
    assert "tool_approval_requests" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "c.platform_id=%s AND c.tenant_id=%s" in sql
    assert "cp.user_id=%s" in sql
    assert "b.employee_id=%s" in sql
    assert "c.updated_at < %s" in sql
    assert connection.query.parameters == (
        "user-1",
        "employee-1",
        "platform-1",
        "tenant-1",
        "ACTIVE",
        cursor[0],
        cursor[0],
        cursor[1],
        25,
    )
    assert result[0].conversation.conversation_id == "conversation-1"
    assert result[0].employee_id == "employee-1"
    assert result[0].session_id == "session-1"
    assert result[0].last_message is not None
    assert result[0].last_message.content_ref == "object:message-1"
    assert result[0].last_run_id == "run-1"
    assert result[0].last_run_status == "AWAITING_APPROVAL"
    assert result[0].pending_approval_count == 1
    assert connection.query.closed
    assert connection.closed


def test_conversation_summary_query_uses_tenant_scope_without_cursor() -> None:
    connection = _Connection(_row())
    repository = MySqlConversationRepository(lambda: connection)
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())

    repository.list_conversation_summaries(identity, "employee-1", "ACTIVE", None, 10)

    assert connection.query.parameters == (
        "user-1",
        "employee-1",
        "platform-1",
        "tenant-1",
        "ACTIVE",
        10,
    )
