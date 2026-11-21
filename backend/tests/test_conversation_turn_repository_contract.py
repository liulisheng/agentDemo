"""Conversation turn 的 Message/Run 联合事务与幂等仓储契约。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime

import pytest

from agent_platform.domain.common import DomainError
from agent_platform.domain.conversations import Message, MessageSource
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, RunSnapshot
from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository


class _Cursor:
    def __init__(self, *, run_rowcount: int = 1, existing_message: tuple[object, ...] | None = None) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.rowcount = 0
        self._run_rowcount = run_rowcount
        self._existing_message = existing_message

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.statements.append((statement, parameters))
        if statement.startswith("INSERT INTO runs"):
            self.rowcount = self._run_rowcount
        elif statement.startswith(("INSERT INTO messages", "UPDATE")):
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchone(self) -> tuple[object, ...] | None:
        statement = self.statements[-1][0]
        if "FROM conversations c" in statement:
            return "ACTIVE", "employee-1", 1, 1, "ACTIVE", 5
        if "SELECT run_id, request_digest FROM runs" in statement:
            return "run-1", "b" * 64
        if "FROM messages WHERE" in statement:
            return self._existing_message
        return None

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, query: _Cursor) -> None:
        self.query = query
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _Cursor:
        return self.query

    def start_transaction(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


def _request() -> tuple[IdentityContext, RunRecord, Message]:
    identity = IdentityContext("platform-1", "tenant-1", "user-1", frozenset())
    run = RunRecord(
        run_id="run-1",
        session_id="session-1",
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        trace_id="trace-1",
        prompt_digest="b" * 64,
        snapshot=RunSnapshot(
            employee_id="employee-1",
            employee_version=1,
            skill_version_ids=(),
            tool_version_ids=(),
            execution_profile_id="profile-1",
        ),
        status=RuntimeStatus.QUEUED,
        submitted_by_user_id=identity.user_id,
    )
    message = Message(
        "message-1",
        "conversation-1",
        identity.platform_id,
        identity.tenant_id,
        identity.user_id,
        "用户私密正文不进入 MySQL",
        MessageSource.USER,
    )
    return identity, run, message


def _stored_message_row() -> tuple[object, ...]:
    return (
        "message-1",
        "conversation-1",
        "platform-1",
        "tenant-1",
        "user-1",
        "user",
        "object:message-1",
        hashlib.sha256("用户私密正文不进入 MySQL".encode()).hexdigest(),
        len("用户私密正文不进入 MySQL".encode()),
        "text/plain; charset=utf-8",
        6,
        0,
        datetime(2026, 11, 19),
    )


def _repository(query: _Cursor, *, failure_injector=None) -> tuple[MySqlExecutionRepository, _Connection]:
    connection = _Connection(query)
    repository = MySqlExecutionRepository(
        lambda: connection,
        failure_injector=failure_injector,
    )
    _identity, run, _ = _request()
    repository.get_run = lambda _identity, run_id: replace(run, run_id=run_id)  # type: ignore[method-assign]
    return repository, connection


def _submit(repository: MySqlExecutionRepository):
    identity, run, message = _request()
    return repository.submit_conversation_turn(
        identity,
        "conversation-1",
        run,
        message,
        "turn-key",
        content_ref="object:message-1",
        content_sha256=hashlib.sha256(message.text.encode("utf-8")).hexdigest(),
        byte_length=len(message.text.encode("utf-8")),
        content_type="text/plain; charset=utf-8",
    )


def test_turn_submission_commits_run_message_and_activity_in_one_transaction() -> None:
    query = _Cursor()
    repository, connection = _repository(query)

    run, message, created = _submit(repository)

    assert created
    assert run.status is RuntimeStatus.QUEUED
    assert message.message_id == "message-1"
    statements = [statement for statement, _ in query.statements]
    run_insert = next(index for index, statement in enumerate(statements) if statement.startswith("INSERT INTO runs"))
    message_insert = next(index for index, statement in enumerate(statements) if statement.startswith("INSERT INTO messages"))
    assert run_insert < message_insert
    assert any("UPDATE conversations SET updated_at" in statement for statement in statements)
    assert connection.committed
    assert not connection.rolled_back
    assert "用户私密正文不进入 MySQL" not in repr(query.statements)


def test_turn_submission_replays_the_existing_message_without_another_insert() -> None:
    query = _Cursor(run_rowcount=0, existing_message=_stored_message_row())
    repository, connection = _repository(query)

    run, message, created = _submit(repository)

    assert not created
    assert run.run_id == "run-1"
    assert message.content_ref == "object:message-1"
    assert not any(statement.startswith("INSERT INTO messages") for statement, _ in query.statements)
    assert connection.committed


def test_turn_submission_rejects_a_reused_idempotency_key_with_changed_snapshot_digest() -> None:
    query = _Cursor(run_rowcount=0, existing_message=_stored_message_row())
    repository, connection = _repository(query)
    identity, run, message = _request()
    run = replace(run, prompt_digest="c" * 64)

    with pytest.raises(DomainError) as conflict:
        repository.submit_conversation_turn(
            identity,
            "conversation-1",
            run,
            message,
            "turn-key",
            content_ref="object:message-1",
            content_sha256=hashlib.sha256(message.text.encode()).hexdigest(),
            byte_length=len(message.text.encode()),
            content_type="text/plain; charset=utf-8",
        )

    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert not any(
        statement.startswith("INSERT INTO messages") for statement, _ in query.statements
    )
    assert connection.rolled_back
    assert not connection.committed


def test_turn_submission_rolls_back_both_rows_when_message_commit_fails() -> None:
    query = _Cursor()

    def fail_after_message(stage: str) -> None:
        if stage == "after_turn_message_insert":
            raise RuntimeError("injected message transaction failure")

    repository, connection = _repository(query, failure_injector=fail_after_message)

    with pytest.raises(RuntimeError, match="injected message transaction failure"):
        _submit(repository)

    assert any(statement.startswith("INSERT INTO runs") for statement, _ in query.statements)
    assert any(statement.startswith("INSERT INTO messages") for statement, _ in query.statements)
    assert connection.rolled_back
    assert not connection.committed


def test_turn_submission_rejects_message_object_digest_mismatch_before_sql() -> None:
    query = _Cursor()
    repository, _ = _repository(query)
    identity, run, message = _request()

    with pytest.raises(DomainError) as mismatch:
        repository.submit_conversation_turn(
            identity,
            "conversation-1",
            run,
            message,
            "turn-key",
            content_ref="object:message-1",
            content_sha256="c" * 64,
            byte_length=len(message.text.encode("utf-8")),
            content_type="text/plain; charset=utf-8",
        )

    assert mismatch.value.code == "INVALID_ARGUMENT"
    assert query.statements == []
