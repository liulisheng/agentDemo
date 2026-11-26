"""编排对象的租户范围内存仓储，供 Fake/API 验证和后续 MySQL 适配复用。"""

from __future__ import annotations

from dataclasses import replace

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.orchestration import (
    OrchestrationRun,
    TaskAttempt,
    TaskDependency,
    TaskEvent,
    TaskNode,
)
from agent_platform.ports.orchestration_repository import TaskPage


class InMemoryOrchestrationRepository:
    """所有 key 都包含 platform/tenant，避免 Fake 模式出现裸 ID 越权。"""

    def __init__(self) -> None:
        self._orchestrations: dict[tuple[str, str, str], OrchestrationRun] = {}
        self._tasks: dict[tuple[str, str, str], TaskNode] = {}
        self._dependencies: dict[tuple[str, str, str], list[TaskDependency]] = {}
        self._attempts: dict[tuple[str, str, str], TaskAttempt] = {}
        self._attempts_by_idempotency: dict[tuple[str, str, str, str], TaskAttempt] = {}
        self._events: dict[tuple[str, str, str], list[TaskEvent]] = {}

    @staticmethod
    def _scope(identity: IdentityContext, resource_id: str) -> tuple[str, str, str]:
        return identity.platform_id, identity.tenant_id, resource_id

    def save_orchestration(self, identity: IdentityContext, value: OrchestrationRun) -> None:
        if (value.platform_id, value.tenant_id) != (identity.platform_id, identity.tenant_id):
            raise DomainError("AUTHORIZATION_DENIED", "编排不属于当前租户")
        self._orchestrations[self._scope(identity, value.orchestration_id)] = value

    def get_orchestration(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        try:
            return self._orchestrations[self._scope(identity, orchestration_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "编排不存在", {"resource": "orchestration"}) from error

    def get_orchestration_by_idempotency(self, identity: IdentityContext, conversation_id: str, idempotency_key: str) -> OrchestrationRun | None:
        return next(
            (value for value in self._orchestrations.values()
             if (value.platform_id, value.tenant_id) == (identity.platform_id, identity.tenant_id)
             and value.conversation_id == conversation_id
             and value.idempotency_key == idempotency_key),
            None,
        )

    def list_orchestrations(self, identity: IdentityContext, conversation_id: str) -> tuple[OrchestrationRun, ...]:
        return tuple(
            sorted(
                (
                    value
                    for (platform_id, tenant_id, _), value in self._orchestrations.items()
                    if platform_id == identity.platform_id
                    and tenant_id == identity.tenant_id
                    and value.conversation_id == conversation_id
                ),
                key=lambda value: (value.created_at, value.orchestration_id),
                reverse=True,
            )
        )

    def save_task(self, identity: IdentityContext, value: TaskNode) -> None:
        orchestration = self.get_orchestration(identity, value.orchestration_id)
        self._tasks[self._scope(identity, value.task_id)] = value
        if orchestration.platform_id != identity.platform_id:
            raise DomainError("AUTHORIZATION_DENIED", "任务不属于当前平台")

    def get_task(self, identity: IdentityContext, task_id: str) -> TaskNode:
        try:
            return self._tasks[self._scope(identity, task_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "任务不存在", {"resource": "task"}) from error

    def save_dependency(self, identity: IdentityContext, orchestration_id: str, value: TaskDependency) -> None:
        self.get_orchestration(identity, orchestration_id)
        self.get_task(identity, value.task_id)
        self.get_task(identity, value.depends_on_task_id)
        key = self._scope(identity, orchestration_id)
        dependencies = self._dependencies.setdefault(key, [])
        if value not in dependencies:
            dependencies.append(value)

    def list_dependencies(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskDependency, ...]:
        self.get_orchestration(identity, orchestration_id)
        return tuple(self._dependencies.get(self._scope(identity, orchestration_id), ()))

    def create_attempt(self, identity: IdentityContext, value: TaskAttempt) -> tuple[TaskAttempt, bool]:
        self.get_task(identity, value.task_id)
        key = (*self._scope(identity, value.task_id), value.idempotency_key)
        previous = self._attempts_by_idempotency.get(key)
        if previous is not None:
            return previous, False
        stored = value
        self._attempts[self._scope(identity, value.attempt_id)] = stored
        self._attempts_by_idempotency[key] = stored
        return stored, True

    def save_attempt(self, identity: IdentityContext, value: TaskAttempt) -> None:
        current = self._attempts.get(self._scope(identity, value.attempt_id))
        if current is None or current.task_id != value.task_id:
            raise DomainError("RESOURCE_NOT_FOUND", "任务尝试不存在", {"resource": "task_attempt"})
        self._attempts[self._scope(identity, value.attempt_id)] = value
        self._attempts_by_idempotency[(*self._scope(identity, value.task_id), value.idempotency_key)] = value

    def list_attempts(self, identity: IdentityContext, task_id: str) -> tuple[TaskAttempt, ...]:
        self.get_task(identity, task_id)
        return tuple(
            sorted(
                (attempt for (platform_id, tenant_id, _), attempt in self._attempts.items()
                 if platform_id == identity.platform_id and tenant_id == identity.tenant_id and attempt.task_id == task_id),
                key=lambda attempt: attempt.attempt_no,
            )
        )

    def claim_attempt(self, identity: IdentityContext, attempt_id: str, worker_id: str) -> TaskAttempt:
        key = self._scope(identity, attempt_id)
        try:
            current = self._attempts[key]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "任务尝试不存在", {"resource": "task_attempt"}) from error
        updated = replace(current, status="CLAIMED", worker_id=worker_id, fencing_token=current.fencing_token + 1)
        self._attempts[key] = updated
        self._attempts_by_idempotency[(*self._scope(identity, current.task_id), current.idempotency_key)] = updated
        return updated

    def complete_attempt(self, identity: IdentityContext, attempt_id: str, fencing_token: int, status: str) -> TaskAttempt:
        key = self._scope(identity, attempt_id)
        try:
            current = self._attempts[key]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "任务尝试不存在", {"resource": "task_attempt"}) from error
        if current.fencing_token != fencing_token:
            raise DomainError("LEASE_FENCING_CONFLICT", "任务尝试租约已失效")
        updated = replace(current, status=status)
        self._attempts[key] = updated
        self._attempts_by_idempotency[(*self._scope(identity, current.task_id), current.idempotency_key)] = updated
        return updated

    def list_tasks(
        self,
        identity: IdentityContext,
        orchestration_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TaskPage:
        if limit < 1 or limit > 100:
            raise DomainError("INVALID_ARGUMENT", "limit 必须在 1 到 100 之间")
        self.get_orchestration(identity, orchestration_id)
        rows = sorted(
            (
                task
                for (platform_id, tenant_id, _), task in self._tasks.items()
                if platform_id == identity.platform_id
                and tenant_id == identity.tenant_id
                and task.orchestration_id == orchestration_id
            ),
            key=lambda task: task.task_id,
        )
        if cursor is not None:
            rows = [task for task in rows if task.task_id > cursor]
        selected = rows[:limit]
        return TaskPage(tuple(selected), selected[-1].task_id if len(rows) > limit else None)

    def append_event(self, identity: IdentityContext, event: TaskEvent) -> TaskEvent:
        self.get_orchestration(identity, event.orchestration_id)
        key = self._scope(identity, event.orchestration_id)
        events = self._events.setdefault(key, [])
        stored = replace(event, sequence_no=len(events) + 1)
        events.append(stored)
        return stored

    def list_events(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskEvent, ...]:
        self.get_orchestration(identity, orchestration_id)
        return tuple(self._events.get(self._scope(identity, orchestration_id), ()))
