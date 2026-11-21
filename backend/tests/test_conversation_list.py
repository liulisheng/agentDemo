"""员工工作台 Conversation 列表与稳定游标契约测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.conversations import ConversationService
from agent_platform.domain.common import DomainError
from agent_platform.domain.conversations import Conversation, MessageSource
from agent_platform.ports.conversation_repository import StoredMessage


class FakeConversationRepository:
    def __init__(self, rows: tuple[object, ...]) -> None:
        self.rows = rows
        self.calls: list[tuple[object, ...]] = []

    def list_conversation_summaries(
        self,
        identity,
        employee_id: str,
        status: str,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> tuple[object, ...]:
        self.calls.append((identity, employee_id, status, cursor, limit))
        rows = [
            row
            for row in self.rows
            if row.conversation.status == status and row.employee_id == employee_id
        ]
        rows.sort(key=lambda row: (row.updated_at, row.conversation.conversation_id), reverse=True)
        if cursor is not None:
            rows = [
                row
                for row in rows
                if (row.updated_at, row.conversation.conversation_id) < cursor
            ]
        return tuple(rows[:limit])


class FakeObjectStore:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.read_scopes: list[tuple[str, str]] = []

    def read(self, object_ref: str, platform_id: str, tenant_id: str) -> bytes:
        self.read_scopes.append((platform_id, tenant_id))
        return self.contents[object_ref]


def _record(
    conversation_id: str,
    updated_at: datetime,
    *,
    message_id: str,
    content_ref: str,
    run_id: str | None,
    run_status: str | None,
    approvals: int,
    employee_id: str = "employee-ds",
) -> object:
    message = StoredMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        platform_id="platform-local",
        tenant_id="tenant-local",
        sender_id="user-local",
        source=MessageSource.USER,
        content_ref=content_ref,
        content_sha256="a" * 64,
        byte_length=256,
        content_type="text/plain; charset=utf-8",
        sequence_no=4,
        routing_depth=0,
    )
    return SimpleNamespace(
        conversation=Conversation(
            conversation_id,
            "platform-local",
            "tenant-local",
            f"标题-{conversation_id}",
            False,
            "ACTIVE",
        ),
        updated_at=updated_at,
        employee_id=employee_id,
        employee_name="海豚调度数仓开发",
        session_id=f"session-{conversation_id}",
        last_message=message,
        last_run_id=run_id,
        last_run_status=run_status,
        pending_approval_count=approvals,
    )


def test_employee_conversation_list_hydrates_authorized_preview_and_pages_stably() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    now = datetime(2026, 11, 19, 8, 30, tzinfo=UTC)
    rows = (
        _record(
            "conv-new",
            now,
            message_id="message-new",
            content_ref="object:new",
            run_id="run-new",
            run_status="AWAITING_APPROVAL",
            approvals=1,
        ),
        _record(
            "conv-old",
            now - timedelta(seconds=1),
            message_id="message-old",
            content_ref="object:old",
            run_id="run-old",
            run_status="SUCCEEDED",
            approvals=0,
        ),
        _record(
            "conv-other-employee",
            now + timedelta(seconds=5),
            message_id="message-other",
            content_ref="object:other",
            run_id="run-other",
            run_status="FAILED",
            approvals=0,
            employee_id="employee-other",
        ),
    )
    repository = FakeConversationRepository(rows)
    object_store = FakeObjectStore(
        {
            "object:new": ("  " + "数" * 150 + "\n请确认 OFFLINE 预览。  ").encode(),
            "object:old": "旧对话摘要".encode(),
            "object:other": "其他员工的摘要".encode(),
        }
    )
    service = ConversationService(repository, object_store)

    first = service.list_for_employee(identity, "employee-ds", limit=1)

    assert len(first.items) == 1
    assert first.items[0].conversation_id == "conv-new"
    assert first.items[0].employee_id == "employee-ds"
    assert first.items[0].last_message.message_id == "message-new"
    assert first.items[0].last_message.preview == "数" * 120 + "…"
    assert first.items[0].last_run_id == "run-new"
    assert first.items[0].last_run_status == "AWAITING_APPROVAL"
    assert first.items[0].pending_approval_count == 1
    assert first.next_cursor
    assert object_store.read_scopes == [(identity.platform_id, identity.tenant_id)]

    second = service.list_for_employee(
        identity, "employee-ds", cursor=first.next_cursor, limit=1
    )

    assert [item.conversation_id for item in second.items] == ["conv-old"]
    assert second.next_cursor is None
    assert repository.calls[0] == (identity, "employee-ds", "ACTIVE", None, 2)
    assert repository.calls[1][1:] == (
        "employee-ds",
        "ACTIVE",
        (now, "conv-new"),
        2,
    )


def test_employee_conversation_list_rejects_invalid_cursor_and_limit() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = ConversationService(FakeConversationRepository(()), FakeObjectStore({}))

    with pytest.raises(DomainError) as invalid_cursor:
        service.list_for_employee(identity, "employee-ds", cursor="not-a-cursor")
    assert invalid_cursor.value.code == "INVALID_CURSOR"

    with pytest.raises(DomainError) as invalid_limit:
        service.list_for_employee(identity, "employee-ds", limit=101)
    assert invalid_limit.value.code == "INVALID_ARGUMENT"
