"""一次性 Tool Approval Worker 租约续租和过期 UNKNOWN 回收契约。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_platform.domain.identity import IdentityContext
from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository


class _Cursor:
    def __init__(self, *, expired_row: tuple[object, ...] | None = None) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.rowcount = 0
        self.expired_row = expired_row
        self.now = datetime.now(UTC)

    def execute(self, statement: str, parameters: tuple[object, ...] | None = None) -> None:
        self.statements.append((statement, parameters))
        self.rowcount = 1 if statement.lstrip().startswith(("UPDATE", "INSERT")) else 0

    def fetchone(self):
        statement = self.statements[-1][0]
        if statement == "SELECT CURRENT_TIMESTAMP(6)":
            return (self.now,)
        if "state='RUNNING'" in statement and "lease_expires_at<=CURRENT_TIMESTAMP(6)" in statement:
            return self.expired_row
        return None

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, query: _Cursor) -> None:
        self.query = query
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


def _worker() -> IdentityContext:
    return IdentityContext(
        "platform-1", "tenant-1", "worker-1", frozenset(), auth_source="worker"
    )


def _expired_approval(now: datetime) -> tuple[object, ...]:
    return (
        "platform-1",
        "tenant-1",
        "approval-1",
        "run-1",
        "session-1",
        "conversation-1",
        "user-1",
        "span-1",
        "tool-1",
        1,
        "profile-1",
        1,
        "object:arguments",
        "a" * 64,
        "object:preview",
        "b" * 64,
        "approve-1",
        "RUNNING",
        now + timedelta(minutes=5),
        "worker-old",
        8,
        now - timedelta(seconds=1),
        "user-1",
        None,
        now - timedelta(seconds=30),
        now - timedelta(seconds=30),
        None,
        None,
    )


def test_approval_heartbeat_is_tenant_and_fencing_scoped() -> None:
    connection = _Connection(_Cursor())
    repository = MySqlMcpRepository(lambda: connection)

    renewed = repository.heartbeat_tool_approval(
        _worker(), "approval-1", "worker-1", 9, lease_seconds=20
    )

    assert renewed
    statement, parameters = connection.query.statements[0]
    assert "UPDATE tool_approval_requests SET lease_expires_at" in statement
    assert "worker_id=%s AND fencing_token=%s" in statement
    assert "lease_expires_at>CURRENT_TIMESTAMP(6)" in statement
    assert parameters == (20, "platform-1", "tenant-1", "approval-1", "worker-1", 9)
    assert connection.committed


def test_claim_recovers_an_expired_write_lease_to_unknown_without_reclaiming_it() -> None:
    cursor = _Cursor()
    cursor.expired_row = _expired_approval(cursor.now)
    connection = _Connection(cursor)
    repository = MySqlMcpRepository(lambda: connection)

    claimed = repository.claim_approved_tool_approval(_worker(), "worker-new")

    assert claimed is None
    sql = [statement for statement, _ in cursor.statements]
    expired_claim = next(statement for statement in sql if "state='RUNNING'" in statement)
    assert "lease_expires_at<=CURRENT_TIMESTAMP(6)" in expired_claim
    approval_update = next(
        statement for statement in sql if "UPDATE tool_approval_requests SET state='UNKNOWN'" in statement
    )
    assert "fencing_token=fencing_token+1" in approval_update
    assert any("UPDATE runs SET state='UNKNOWN'" in statement for statement in sql)
    assert any("UPDATE platform_trace_spans" in statement for statement in sql)
    assert connection.committed
