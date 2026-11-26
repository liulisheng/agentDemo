"""V011/V012 团队编排、任务、Attempt 和顺序事件的 MySQL 仓储。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.orchestration import (
    OrchestrationRun,
    TaskAttempt,
    TaskDependency,
    TaskEvent,
    TaskNode,
)
from agent_platform.ports.orchestration_repository import OrchestrationRepository, TaskPage


class MySqlOrchestrationRepository(OrchestrationRepository):
    """所有编排状态都以 platform/tenant 条件写入，事件序号通过父编排行锁串行分配。"""

    def __init__(self, connection_factory) -> None:
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
    def _scope(identity: IdentityContext) -> tuple[str, str]:
        return identity.platform_id, identity.tenant_id

    @staticmethod
    def _not_found(resource: str) -> DomainError:
        return DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": resource})

    @staticmethod
    def _json(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(value)

    def save_orchestration(self, identity: IdentityContext, value: OrchestrationRun) -> None:
        if (value.platform_id, value.tenant_id) != self._scope(identity):
            raise DomainError("AUTHORIZATION_DENIED", "编排不属于当前租户")
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO orchestration_runs "
                "(platform_id,tenant_id,orchestration_id,conversation_id,team_id,team_version,plan_revision,status,approval_mode,idempotency_key,counters_json,created_by,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE plan_revision=VALUES(plan_revision),status=VALUES(status),approval_mode=VALUES(approval_mode),updated_at=VALUES(updated_at)",
                (
                    identity.platform_id, identity.tenant_id, value.orchestration_id,
                    value.conversation_id, value.team_id, value.team_version,
                    value.plan_revision, value.status.value, value.approval_mode,
                    value.idempotency_key or value.orchestration_id, "{}", identity.user_id,
                    value.created_at.replace(tzinfo=None), value.updated_at.replace(tzinfo=None),
                ),
            )

    @staticmethod
    def _orchestration(row: tuple[Any, ...], identity: IdentityContext) -> OrchestrationRun:
        return OrchestrationRun(
            str(row[0]), identity.platform_id, identity.tenant_id, str(row[1]), str(row[2]),
            int(row[3]), int(row[4]), str(row[5]), str(row[6]), row[7], row[8], row[9],
        )

    def get_orchestration(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT orchestration_id,conversation_id,team_id,team_version,plan_revision,status,approval_mode,idempotency_key,created_at,updated_at "
                "FROM orchestration_runs WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s",
                (*self._scope(identity), orchestration_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("orchestration")
        return self._orchestration(row, identity)

    def get_orchestration_by_idempotency(self, identity: IdentityContext, conversation_id: str, idempotency_key: str) -> OrchestrationRun | None:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT orchestration_id,conversation_id,team_id,team_version,plan_revision,status,approval_mode,idempotency_key,created_at,updated_at "
                "FROM orchestration_runs WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s AND idempotency_key=%s",
                (*self._scope(identity), conversation_id, idempotency_key),
            )
            row = cursor.fetchone()
        return self._orchestration(row, identity) if row is not None else None

    def list_orchestrations(self, identity: IdentityContext, conversation_id: str) -> tuple[OrchestrationRun, ...]:
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT orchestration_id,conversation_id,team_id,team_version,plan_revision,status,approval_mode,idempotency_key,created_at,updated_at "
                "FROM orchestration_runs WHERE platform_id=%s AND tenant_id=%s AND conversation_id=%s "
                "ORDER BY created_at DESC,orchestration_id DESC",
                (*self._scope(identity), conversation_id),
            )
            rows = cursor.fetchall()
        return tuple(self._orchestration(row, identity) for row in rows)

    def save_task(self, identity: IdentityContext, value: TaskNode) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO orchestration_tasks "
                "(platform_id,tenant_id,task_id,orchestration_id,client_key,title,instruction_ref,parent_task_id,required_flag,required_capabilities_json,preferred_engine_name,preferred_member_id,assigned_member_id,assignment_source,status,attempt,max_attempts,output_ref,error_code,created_at,started_at,finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title),instruction_ref=VALUES(instruction_ref),required_flag=VALUES(required_flag),required_capabilities_json=VALUES(required_capabilities_json),preferred_engine_name=VALUES(preferred_engine_name),preferred_member_id=VALUES(preferred_member_id),assigned_member_id=VALUES(assigned_member_id),assignment_source=VALUES(assignment_source),status=VALUES(status),attempt=VALUES(attempt),max_attempts=VALUES(max_attempts),output_ref=VALUES(output_ref),error_code=VALUES(error_code),started_at=VALUES(started_at),finished_at=VALUES(finished_at)",
                (
                    identity.platform_id, identity.tenant_id, value.task_id, value.orchestration_id,
                    value.client_key, value.title, value.instruction_ref, value.required,
                    json.dumps(list(value.required_capabilities), ensure_ascii=False),
                    value.preferred_engine_name, value.preferred_member_id, value.assigned_member_id,
                    value.assignment_source.value if value.assignment_source else None, value.status.value,
                    value.attempt, value.max_attempts, value.output_ref, value.error_code,
                    value.created_at.replace(tzinfo=None),
                    value.started_at.replace(tzinfo=None) if value.started_at else None,
                    value.finished_at.replace(tzinfo=None) if value.finished_at else None,
                ),
            )

    @staticmethod
    def _task(row: tuple[Any, ...]) -> TaskNode:
        capabilities = MySqlOrchestrationRepository._json(row[8], [])
        return TaskNode(
            task_id=str(row[0]), orchestration_id=str(row[1]), client_key=str(row[2]),
            title=str(row[3]), instruction_ref=str(row[4] or ""), required=bool(row[5]),
            required_capabilities=tuple(str(value) for value in capabilities),
            preferred_engine_name=row[6], preferred_member_id=row[7], assigned_member_id=row[9],
            assignment_source=row[10], status=str(row[11]), attempt=int(row[12]),
            max_attempts=int(row[13]), output_ref=row[14], error_code=row[15],
            created_at=row[16], started_at=row[17], finished_at=row[18],
        )

    _TASK_COLUMNS = (
        "task_id,orchestration_id,client_key,title,instruction_ref,required_flag,preferred_engine_name,preferred_member_id,required_capabilities_json,assigned_member_id,assignment_source,status,attempt,max_attempts,output_ref,error_code,created_at,started_at,finished_at"
    )

    def get_task(self, identity: IdentityContext, task_id: str) -> TaskNode:
        with self._reader() as (_, cursor):
            cursor.execute(
                f"SELECT {self._TASK_COLUMNS} FROM orchestration_tasks WHERE platform_id=%s AND tenant_id=%s AND task_id=%s",
                (*self._scope(identity), task_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise self._not_found("task")
        return self._task(row)

    def list_tasks(self, identity: IdentityContext, orchestration_id: str, *, cursor: str | None = None, limit: int = 50) -> TaskPage:
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 100 之间")
        self.get_orchestration(identity, orchestration_id)
        with self._reader() as (_, db_cursor):
            query = f"SELECT {self._TASK_COLUMNS} FROM orchestration_tasks WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s"
            params: tuple[Any, ...] = (*self._scope(identity), orchestration_id)
            if cursor is not None:
                query += " AND task_id>%s"
                params += (cursor,)
            db_cursor.execute(query + " ORDER BY task_id LIMIT %s", params + (limit + 1,))
            rows = db_cursor.fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        tasks = tuple(self._task(row) for row in selected)
        return TaskPage(tasks, tasks[-1].task_id if has_more and tasks else None)

    def save_dependency(self, identity: IdentityContext, orchestration_id: str, value: TaskDependency) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO orchestration_task_dependencies (platform_id,tenant_id,task_id,depends_on_task_id,dependency_condition) "
                "SELECT %s,%s,%s,%s,%s FROM DUAL WHERE EXISTS (SELECT 1 FROM orchestration_runs WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s) "
                "ON DUPLICATE KEY UPDATE dependency_condition=VALUES(dependency_condition)",
                (*self._scope(identity), value.task_id, value.depends_on_task_id, value.condition, *self._scope(identity), orchestration_id),
            )
            if cursor.rowcount == 0:
                raise self._not_found("orchestration")

    def list_dependencies(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskDependency, ...]:
        self.get_orchestration(identity, orchestration_id)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT task_id,depends_on_task_id,dependency_condition FROM orchestration_task_dependencies "
                "WHERE platform_id=%s AND tenant_id=%s AND task_id IN (SELECT task_id FROM orchestration_tasks WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s) "
                "ORDER BY task_id,depends_on_task_id",
                (*self._scope(identity), *self._scope(identity), orchestration_id),
            )
            rows = cursor.fetchall()
        return tuple(TaskDependency(str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def create_attempt(self, identity: IdentityContext, value: TaskAttempt) -> tuple[TaskAttempt, bool]:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "INSERT INTO orchestration_task_attempts (platform_id,tenant_id,attempt_id,task_id,attempt_no,member_id,worker_id,worker_pool,fencing_token,run_id,status,idempotency_key,error_code) "
                "VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE attempt_id=attempt_id",
                (*self._scope(identity), value.attempt_id, value.task_id, value.attempt_no,
                 value.worker_id, value.worker_pool, value.fencing_token, value.run_id, value.status,
                 value.idempotency_key, value.error_code),
            )
            created = cursor.rowcount == 1
            cursor.execute(
                "SELECT attempt_id,task_id,attempt_no,idempotency_key,status,worker_id,fencing_token,run_id,error_code,worker_pool "
                "FROM orchestration_task_attempts WHERE platform_id=%s AND tenant_id=%s AND task_id=%s AND idempotency_key=%s",
                (*self._scope(identity), value.task_id, value.idempotency_key),
            )
            row = cursor.fetchone()
        if row is None:
            raise DomainError("RESOURCE_CONFLICT", "任务 Attempt 幂等键冲突")
        stored = self._attempt(row)
        if stored.attempt_no != value.attempt_no:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Attempt 幂等键对应的序号已改变")
        return stored, created

    @staticmethod
    def _attempt(row: tuple[Any, ...]) -> TaskAttempt:
        return TaskAttempt(str(row[0]), str(row[1]), int(row[2]), str(row[3]), str(row[4]), row[5], int(row[6]), row[7], row[8], row[9])

    def save_attempt(self, identity: IdentityContext, value: TaskAttempt) -> None:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE orchestration_task_attempts SET status=%s,worker_id=%s,worker_pool=%s,fencing_token=%s,run_id=%s,error_code=%s "
                "WHERE platform_id=%s AND tenant_id=%s AND attempt_id=%s AND task_id=%s",
                (value.status, value.worker_id, value.worker_pool, value.fencing_token, value.run_id, value.error_code, *self._scope(identity), value.attempt_id, value.task_id),
            )
            if cursor.rowcount == 0:
                raise self._not_found("task_attempt")

    def list_attempts(self, identity: IdentityContext, task_id: str) -> tuple[TaskAttempt, ...]:
        self.get_task(identity, task_id)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT attempt_id,task_id,attempt_no,idempotency_key,status,worker_id,fencing_token,run_id,error_code,worker_pool "
                "FROM orchestration_task_attempts WHERE platform_id=%s AND tenant_id=%s AND task_id=%s ORDER BY attempt_no",
                (*self._scope(identity), task_id),
            )
            rows = cursor.fetchall()
        return tuple(self._attempt(row) for row in rows)

    def claim_attempt(self, identity: IdentityContext, attempt_id: str, worker_id: str) -> TaskAttempt:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT attempt_id,task_id,attempt_no,idempotency_key,status,worker_id,fencing_token,run_id,error_code,worker_pool "
                "FROM orchestration_task_attempts WHERE platform_id=%s AND tenant_id=%s AND attempt_id=%s FOR UPDATE",
                (*self._scope(identity), attempt_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise self._not_found("task_attempt")
            current = self._attempt(row)
            updated = TaskAttempt(current.attempt_id, current.task_id, current.attempt_no, current.idempotency_key, "CLAIMED", worker_id, current.fencing_token + 1, current.run_id, current.error_code, current.worker_pool)
            cursor.execute(
                "UPDATE orchestration_task_attempts SET status=%s,worker_id=%s,fencing_token=%s,started_at=COALESCE(started_at,CURRENT_TIMESTAMP(6)) "
                "WHERE platform_id=%s AND tenant_id=%s AND attempt_id=%s",
                (updated.status, worker_id, updated.fencing_token, *self._scope(identity), attempt_id),
            )
        return updated

    def complete_attempt(self, identity: IdentityContext, attempt_id: str, fencing_token: int, status: str) -> TaskAttempt:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "UPDATE orchestration_task_attempts SET status=%s,finished_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND attempt_id=%s AND fencing_token=%s",
                (status, *self._scope(identity), attempt_id, fencing_token),
            )
            if cursor.rowcount != 1:
                raise DomainError("LEASE_FENCING_CONFLICT", "任务尝试租约已失效")
            cursor.execute(
                "SELECT attempt_id,task_id,attempt_no,idempotency_key,status,worker_id,fencing_token,run_id,error_code,worker_pool "
                "FROM orchestration_task_attempts WHERE platform_id=%s AND tenant_id=%s AND attempt_id=%s",
                (*self._scope(identity), attempt_id),
            )
            row = cursor.fetchone()
        return self._attempt(row)

    def append_event(self, identity: IdentityContext, event: TaskEvent) -> TaskEvent:
        with self._transaction() as (_, cursor):
            cursor.execute(
                "SELECT orchestration_id FROM orchestration_runs WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s FOR UPDATE",
                (*self._scope(identity), event.orchestration_id),
            )
            if cursor.fetchone() is None:
                raise self._not_found("orchestration")
            cursor.execute(
                "SELECT COALESCE(MAX(sequence_no),0)+1 FROM orchestration_task_events WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s",
                (*self._scope(identity), event.orchestration_id),
            )
            sequence = int(cursor.fetchone()[0])
            cursor.execute(
                "INSERT INTO orchestration_task_events (platform_id,tenant_id,event_id,orchestration_id,task_id,attempt_id,actor_type,actor_id,event_type,payload_json,sequence_no,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (*self._scope(identity), event.event_id, event.orchestration_id, event.task_id,
                 event.attempt_id, event.actor_type, event.actor_id, event.event_type,
                 json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")), sequence,
                 event.created_at.replace(tzinfo=None)),
            )
        return TaskEvent(event.event_id, event.orchestration_id, event.event_type, event.task_id,
                         event.attempt_id, event.actor_type, event.actor_id, event.payload,
                         sequence, event.created_at)

    def list_events(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskEvent, ...]:
        self.get_orchestration(identity, orchestration_id)
        with self._reader() as (_, cursor):
            cursor.execute(
                "SELECT event_id,orchestration_id,event_type,task_id,attempt_id,actor_type,actor_id,payload_json,sequence_no,created_at "
                "FROM orchestration_task_events WHERE platform_id=%s AND tenant_id=%s AND orchestration_id=%s ORDER BY sequence_no",
                (*self._scope(identity), orchestration_id),
            )
            rows = cursor.fetchall()
        return tuple(
            TaskEvent(str(row[0]), str(row[1]), str(row[2]), row[3], row[4], str(row[5]), row[6],
                      self._json(row[7], {}), int(row[8]), row[9])
            for row in rows
        )
