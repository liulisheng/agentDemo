"""独立 Run Worker 的 MySQL claim、租约和输入引用 SQL 契约。"""

from __future__ import annotations

from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord, RunSnapshot
from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository


class _Cursor:
    def __init__(
        self,
        *,
        queued_row=None,
        expired_row=None,
        input_ref=None,
        session_row=("ACTIVE",),
        cancellation_row=None,
    ) -> None:
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.rowcount = 0
        self.queued_row = queued_row
        self.expired_row = expired_row
        self.input_ref = input_ref
        self.session_row = session_row
        self.cancellation_row = cancellation_row
        self.now_checks = 0

    def execute(self, statement: str, parameters=None) -> None:
        self.statements.append((statement, parameters))
        self.rowcount = int(statement.lstrip().startswith(("UPDATE", "INSERT", "DELETE")))

    def fetchone(self):
        statement = self.statements[-1][0]
        if "state IN ('STARTING','RUNNING','CANCELLING')" in statement:
            return self.expired_row
        if "state='QUEUED'" in statement and statement.lstrip().startswith("SELECT"):
            return self.queued_row
        if "FROM messages" in statement:
            return self.input_ref
        if "SELECT status FROM sessions" in statement:
            return self.session_row
        if "FROM sessions" in statement:
            return self.session_row
        if "SELECT 1 FROM session_runtime_leases" in statement:
            return (1,)
        if "SELECT session_id FROM runs" in statement:
            return ("session-1",)
        if "submitted_by_user_id" in statement and "FOR UPDATE" in statement:
            return self.cancellation_row
        if "SELECT 1 FROM runs" in statement:
            return (1,)
        return None

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.query = cursor
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


def _worker(tenant_id: str = "tenant-1") -> IdentityContext:
    return IdentityContext(
        "platform-1", tenant_id, "run-worker-1", frozenset(), auth_source="worker"
    )


def test_claim_selects_one_platform_run_with_skip_locked_and_fences_it() -> None:
    cursor = _Cursor(queued_row=("tenant-1", "run-1", "session-1", 6))
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    claim = repository.claim_next_queued_run("platform-1", "run-worker-1", lease_seconds=30)

    assert claim is not None
    assert (claim.platform_id, claim.tenant_id, claim.run_id) == (
        "platform-1", "tenant-1", "run-1"
    )
    assert claim.worker_id == "run-worker-1"
    assert claim.fencing_token == 7
    select = next(
        statement for statement, _ in cursor.statements
        if statement.lstrip().startswith("SELECT") and "state='QUEUED'" in statement
    )
    assert "WHERE r.platform_id=%s AND r.state='QUEUED'" in select
    assert "ORDER BY r.created_at, r.run_id" in select
    assert "FOR UPDATE SKIP LOCKED" in select
    update, parameters = next(
        (statement, parameters) for statement, parameters in cursor.statements
        if "UPDATE runs SET state='STARTING'" in statement
    )
    assert "fencing_token=fencing_token+1" in update
    assert parameters == (
        "run-worker-1", 30, "platform-1", "tenant-1", "run-1", 6,
    )
    assert connection.committed


def test_start_claimed_run_requires_the_current_owner_and_token() -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    started = repository.start_claimed_run(
        _worker(), "run-1", "run-worker-1", 9
    )

    assert started
    statement, parameters = next(
        (statement, parameters)
        for statement, parameters in cursor.statements
        if "UPDATE runs SET state='RUNNING'" in statement
    )
    assert "UPDATE runs SET state='RUNNING'" in statement
    assert "state='STARTING' AND worker_id=%s AND fencing_token=%s" in statement
    assert "lease_expires_at>CURRENT_TIMESTAMP(6)" in statement
    assert parameters == (
        "platform-1", "tenant-1", "run-1", "run-worker-1", 9,
    )


def test_run_lease_check_requires_owner_token_and_future_expiry() -> None:
    cursor = _Cursor()
    repository = MySqlExecutionRepository(lambda: _Connection(cursor))

    valid = repository.is_run_lease_current(
        _worker(), "run-1", "run-worker-1", 9
    )

    assert valid
    statement, parameters = cursor.statements[0]
    assert "state='RUNNING'" in statement
    assert "r.worker_id=%s AND r.fencing_token=%s" in statement
    assert "lease_expires_at>CURRENT_TIMESTAMP(6)" in statement
    assert parameters == (
        "platform-1", "tenant-1", "run-1", "run-worker-1", 9, "run-worker-1",
    )


def test_run_input_ref_is_loaded_from_the_same_tenant_user_message() -> None:
    cursor = _Cursor(input_ref=("message-1", "object:message-1", "a" * 64, 37))
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    input_ref = repository.get_run_input_ref(_worker(), "run-1")

    assert input_ref.message_id == "message-1"
    assert input_ref.content_ref == "object:message-1"
    assert input_ref.content_sha256 == "a" * 64
    assert input_ref.byte_length == 37
    statement, parameters = cursor.statements[0]
    assert "FROM messages" in statement
    assert "platform_id=%s AND tenant_id=%s AND run_id=%s" in statement
    assert "role='USER' AND status='COMPLETE'" in statement
    assert parameters == ("platform-1", "tenant-1", "run-1")


def test_loaded_session_restores_its_persisted_session_trace_span() -> None:
    cursor = _Cursor(session_row=(
        "session-1", "platform-1", "tenant-1", "deployment-1", "employee-1", 3,
        "profile-1", 2, None, "trace-1", "runtime-session-1", "ACTIVE",
        '{"trace_id":"trace-1","session_span_id":"session-span-1"}', None,
        1, "DEPLOYMENT_OVERRIDE", "SYSTEM_DEFAULT", "fake",
    ))
    repository = MySqlExecutionRepository(lambda: _Connection(cursor))

    session = repository.get_session(_worker(), "session-1")

    assert session.span_id == "session-span-1"


def test_heartbeat_requires_worker_id_when_provided_and_current_fencing_token() -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    renewed = repository.heartbeat(
        _worker(), "run-1", 17, worker_id="run-worker-1", lease_seconds=30
    )

    assert renewed
    statement, parameters = cursor.statements[0]
    assert "state IN ('STARTING','RUNNING','CANCELLING')" in statement
    assert "fencing_token=%s" in statement
    assert "worker_id=%s" in statement
    assert parameters == (30, "platform-1", "tenant-1", "run-1", 17, "run-worker-1")


def test_marking_unknown_increments_fencing_token_to_reject_late_result() -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    marked = repository.mark_unknown(_worker(), "run-1", 17)

    assert marked
    statement, parameters = cursor.statements[0]
    assert "UPDATE runs SET state='UNKNOWN'" in statement
    assert "fencing_token=fencing_token+1" in statement
    assert "state IN ('STARTING','RUNNING','CANCELLING')" in statement
    assert parameters == ("platform-1", "tenant-1", "run-1", 17)


def test_cancellation_moves_queued_to_cancelled_and_running_to_cancelling() -> None:
    requester = IdentityContext("platform-1", "tenant-1", "admin-local", frozenset())
    queued_cursor = _Cursor(cancellation_row=("QUEUED", "admin-local"))
    queued_repository = MySqlExecutionRepository(lambda: _Connection(queued_cursor))
    queued_state = queued_repository.request_run_cancellation(requester, "run-queued")

    assert queued_state == "CANCELLED"
    queued_update = next(
        statement for statement, _ in queued_cursor.statements
        if "UPDATE runs SET state='CANCELLED'" in statement
    )
    assert "state='QUEUED'" in queued_update

    running_cursor = _Cursor(cancellation_row=("RUNNING", "admin-local"))
    running_repository = MySqlExecutionRepository(lambda: _Connection(running_cursor))
    running_state = running_repository.request_run_cancellation(requester, "run-running")

    assert running_state == "CANCELLING"
    running_update = next(
        statement for statement, _ in running_cursor.statements
        if "UPDATE runs SET state='CANCELLING'" in statement
    )
    assert "state IN ('STARTING','RUNNING')" in running_update


def test_worker_completion_accepts_cancelling_and_attributes_usage_to_run_submitter() -> None:
    cursor = _Cursor()
    repository = MySqlExecutionRepository(lambda: _Connection(cursor))
    worker = _worker()
    run = RunRecord(
        run_id="run-1",
        session_id="session-1",
        platform_id=worker.platform_id,
        tenant_id=worker.tenant_id,
        trace_id="trace-1",
        prompt_digest="d" * 64,
        snapshot=RunSnapshot(
            employee_id="employee-1",
            employee_version=1,
            skill_version_ids=(),
            tool_version_ids=(),
            execution_profile_id="profile-1",
        ),
        status=RuntimeStatus.CANCELLED,
        submitted_by_user_id="original-submitter",
    )

    completed = repository.complete_run(
        worker, run, 4, (), UsageRecord(1, 0, 1)
    )

    assert completed
    run_update = next(
        statement for statement, _ in cursor.statements
        if "UPDATE runs SET state=%s" in statement
    )
    assert "state IN ('RUNNING','CANCELLING')" in run_update
    usage_parameters = next(
        parameters for statement, parameters in cursor.statements
        if "INSERT INTO usage_ledger" in statement
    )
    assert usage_parameters[5] == "original-submitter"


def test_claim_recovers_expired_starting_or_running_run_to_unknown_without_requeue() -> None:
    cursor = _Cursor(expired_row=("tenant-1", "run-expired", "session-expired", 12))
    connection = _Connection(cursor)
    repository = MySqlExecutionRepository(lambda: connection)

    claim = repository.claim_next_queued_run("platform-1", "run-worker-2")

    assert claim is None
    select = next(
        statement for statement, _ in cursor.statements
        if statement.lstrip().startswith("SELECT")
        and "state IN ('STARTING','RUNNING','CANCELLING')" in statement
    )
    assert "lease_expires_at<=CURRENT_TIMESTAMP(6)" in select
    update = next(
        statement for statement, _ in cursor.statements
        if "UPDATE runs SET state='UNKNOWN'" in statement
    )
    assert "fencing_token=fencing_token+1" in update
    assert not any("state='QUEUED'" in statement for statement, _ in cursor.statements)
    assert connection.committed
