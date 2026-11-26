"""编排父运行、任务节点和事件的持久化端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.orchestration import (
    OrchestrationRun,
    TaskAttempt,
    TaskDependency,
    TaskEvent,
    TaskNode,
)


@dataclass(frozen=True, slots=True)
class TaskPage:
    """按任务 ID 稳定分页的租户任务页。"""

    items: tuple[TaskNode, ...]
    next_cursor: str | None


class OrchestrationRepository(Protocol):
    """跨进程保存编排、任务和追加事件的最小端口。"""

    def save_orchestration(self, identity: IdentityContext, value: OrchestrationRun) -> None: ...

    def get_orchestration(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun: ...

    def get_orchestration_by_idempotency(self, identity: IdentityContext, conversation_id: str, idempotency_key: str) -> OrchestrationRun | None: ...

    def list_orchestrations(self, identity: IdentityContext, conversation_id: str) -> tuple[OrchestrationRun, ...]: ...

    def save_task(self, identity: IdentityContext, value: TaskNode) -> None: ...

    def get_task(self, identity: IdentityContext, task_id: str) -> TaskNode: ...

    def list_tasks(
        self,
        identity: IdentityContext,
        orchestration_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TaskPage: ...

    def save_dependency(self, identity: IdentityContext, orchestration_id: str, value: TaskDependency) -> None: ...

    def list_dependencies(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskDependency, ...]: ...

    def create_attempt(self, identity: IdentityContext, value: TaskAttempt) -> tuple[TaskAttempt, bool]: ...

    def save_attempt(self, identity: IdentityContext, value: TaskAttempt) -> None: ...

    def list_attempts(self, identity: IdentityContext, task_id: str) -> tuple[TaskAttempt, ...]: ...

    def claim_attempt(self, identity: IdentityContext, attempt_id: str, worker_id: str) -> TaskAttempt: ...

    def complete_attempt(self, identity: IdentityContext, attempt_id: str, fencing_token: int, status: str) -> TaskAttempt: ...

    def append_event(self, identity: IdentityContext, event: TaskEvent) -> TaskEvent: ...

    def list_events(self, identity: IdentityContext, orchestration_id: str) -> tuple[TaskEvent, ...]: ...
