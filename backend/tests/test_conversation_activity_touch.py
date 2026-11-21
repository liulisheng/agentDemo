"""Run 状态转换必须在同一事务推进所属 Conversation 更新时间。"""

from __future__ import annotations

from agent_platform.domain.identity import IdentityContext
from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rowcount = 0

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.statements.append(statement)
        if statement.lstrip().startswith(("UPDATE", "INSERT", "DELETE")):
            self.rowcount = 1

    def fetchone(self) -> tuple[object, ...]:
        statement = self.statements[-1]
        if "SELECT state, fencing_token, lease_expires_at, session_id FROM runs" in statement:
            return "QUEUED", 0, None, "session-1"
        if "SELECT status FROM sessions" in statement:
            return ("ACTIVE",)
        return "QUEUED", 0, None

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self) -> None:
        self.query = _Cursor()
        self.committed = False

    def cursor(self) -> _Cursor:
        return self.query

    def start_transaction(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_claiming_a_run_touches_its_bound_conversation_in_the_same_transaction() -> None:
    connection = _Connection()
    repository = MySqlExecutionRepository(lambda: connection)
    identity = IdentityContext("platform-1", "tenant-1", "worker-1", frozenset(), "worker")

    assert repository.acquire_lease(identity, "run-1", "worker-1") == 1

    assert connection.committed
    assert any(
        "UPDATE conversations" in statement and "agent_bindings" in statement
        for statement in connection.query.statements
    )
