"""Router 计划、任务 DAG 和编排状态服务。"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.orchestration import (
    AssignmentSource,
    OrchestrationRun,
    OrchestrationStatus,
    TaskDependency,
    TaskEvent,
    TaskNode,
    TaskStatus,
)
from agent_platform.domain.teams import TeamStatus
from agent_platform.ports.orchestration_repository import OrchestrationRepository


def _normalize_engine_name(value: str) -> str:
    normalized = value.casefold().replace("-", "_")
    return "deepseek_harness" if normalized == "deepseek" else normalized


class OrchestrationService:
    """在仓储端口上执行团队计划校验、确认和任务依赖解锁。"""

    def __init__(self, repository: OrchestrationRepository, teams: TeamService) -> None:
        self.repository = repository
        self.teams = teams
        self._idempotency: dict[tuple[str, str, str, str], OrchestrationRun] = {}

    def create_planning(
        self,
        identity: IdentityContext,
        conversation_id: str,
        team_id: str,
        team_version: int,
        idempotency_key: str,
        prompt_sha256: str | None = None,
        input_message_id: str | None = None,
    ) -> OrchestrationRun:
        """先持久化 PLANNING 聚合，使异步 Router Run 可跨请求与进程恢复。"""

        if not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 不能为空")
        version = self.teams.get(identity, team_id, team_version)
        if version.status is not TeamStatus.PUBLISHED:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "只有已发布团队版本可以创建编排")
        key = self._scope(identity, conversation_id, idempotency_key)
        existing = self._idempotency.get(key) or self.repository.get_orchestration_by_idempotency(
            identity, conversation_id, idempotency_key
        )
        if existing is not None:
            self._idempotency[key] = existing
            return existing
        orchestration = OrchestrationRun(
            generate_database_id(), identity.platform_id, identity.tenant_id,
            conversation_id, team_id, team_version, status=OrchestrationStatus.PLANNING,
            approval_mode=version.approval_mode, idempotency_key=idempotency_key,
        )
        self.repository.save_orchestration(identity, orchestration)
        self.repository.append_event(
            identity,
            TaskEvent.new(
                orchestration.orchestration_id, "plan_requested",
                payload={
                    "plan_revision": 1,
                    **({"prompt_sha256": prompt_sha256} if prompt_sha256 else {}),
                    **({"input_message_id": input_message_id} if input_message_id else {}),
                },
            ),
        )
        self._idempotency[key] = orchestration
        return orchestration

    @staticmethod
    def _scope(identity: IdentityContext, conversation_id: str, key: str) -> tuple[str, str, str, str]:
        return identity.platform_id, identity.tenant_id, conversation_id, key

    @staticmethod
    def _stable_task_id(identity: IdentityContext, orchestration_id: str, client_key: str) -> str:
        """从父编排和 client_key 派生稳定 ID，允许服务重启后补齐半写入计划。"""

        digest = hashlib.sha256(
            f"{identity.platform_id}\0{identity.tenant_id}\0{orchestration_id}\0{client_key}".encode()
        ).digest()
        return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:26]

    def create_from_plan(
        self,
        identity: IdentityContext,
        conversation_id: str,
        team_id: str,
        team_version: int,
        idempotency_key: str,
        plan: Sequence[Mapping[str, object]],
    ) -> OrchestrationRun:
        if not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 不能为空")
        version = self.teams.get(identity, team_id, team_version)
        if version.status is not TeamStatus.PUBLISHED:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "只有已发布团队版本可以创建编排")
        if not plan or len(plan) > 100:
            raise DomainError("INVALID_ARGUMENT", "任务计划数量必须在 1 到 100 之间")
        active_members = {member.member_id: member for member in version.members if member.status == "ACTIVE"}
        client_keys = [str(item.get("client_key", "")).strip() for item in plan]
        if any(not key for key in client_keys) or len(client_keys) != len(set(client_keys)):
            raise DomainError("PLAN_INVALID", "任务 client_key 必须非空且唯一")
        key_set = set(client_keys)
        dependency_map: dict[str, tuple[str, ...]] = {}
        for item, client_key in zip(plan, client_keys):
            required_capabilities = {str(value) for value in item.get("required_capabilities", ()) or ()}
            preferred_member_id = str(item["preferred_member_id"]) if item.get("preferred_member_id") else None
            preferred_engine_name = str(item["preferred_engine"]) if item.get("preferred_engine") else None
            if preferred_member_id is not None:
                member = active_members.get(preferred_member_id)
                if member is None:
                    raise DomainError("PLAN_INVALID", "任务计划引用了不存在或不可用的成员")
                if required_capabilities.difference(member.capabilities):
                    raise DomainError("ENGINE_CAPABILITY_UNSUPPORTED", "任务计划分配的成员不满足能力要求")
                if preferred_engine_name is not None and _normalize_engine_name(preferred_engine_name) != _normalize_engine_name(member.engine_name):
                    raise DomainError("ENGINE_MODEL_INCOMPATIBLE", "任务计划选择的引擎与成员固定引擎不一致")
            elif preferred_engine_name is not None and _normalize_engine_name(preferred_engine_name) not in {
                _normalize_engine_name(member.engine_name) for member in active_members.values()
            }:
                raise DomainError("ENGINE_PROVIDER_UNAVAILABLE", "任务计划引用了团队未配置的 EngineProvider")
            dependencies = tuple(str(value) for value in item.get("depends_on", ()) or ())
            if any(value not in key_set for value in dependencies):
                raise DomainError("PLAN_INVALID", "任务依赖引用不存在的任务")
            dependency_map[client_key] = dependencies
        self._assert_acyclic(dependency_map)
        previous = self._idempotency.get(self._scope(identity, conversation_id, idempotency_key))
        if previous is None:
            previous = self.repository.get_orchestration_by_idempotency(identity, conversation_id, idempotency_key)
        target_status = (
            OrchestrationStatus.AWAITING_PLAN_APPROVAL
            if version.approval_mode == "CONFIRM"
            else OrchestrationStatus.READY
        )
        if previous is not None and previous.status is not OrchestrationStatus.PLANNING:
            self._assert_idempotent_plan(identity, previous, plan)
            self._idempotency[self._scope(identity, conversation_id, idempotency_key)] = previous
            return previous
        existing_tasks = (
            self.repository.list_tasks(identity, previous.orchestration_id, limit=100).items
            if previous is not None
            else ()
        )
        existing_by_key = {task.client_key: task for task in existing_tasks}
        proposed_by_key = {str(item.get("client_key", "")).strip(): item for item in plan}
        if any(key not in proposed_by_key for key in existing_by_key):
            raise DomainError("IDEMPOTENCY_CONFLICT", "PLANNING 编排已含有与 Router 计划不一致的任务")
        for key, task in existing_by_key.items():
            item = proposed_by_key[key]
            if (
                task.title != str(item.get("title", key))
                or task.instruction_ref != str(item.get("instruction_ref", ""))
                or task.required != bool(item.get("required", True))
                or task.required_capabilities != tuple(sorted({str(value) for value in item.get("required_capabilities", ()) or ()}))
                or task.preferred_engine_name != (str(item["preferred_engine"]) if item.get("preferred_engine") else None)
                or task.preferred_member_id != (str(item["preferred_member_id"]) if item.get("preferred_member_id") else None)
            ):
                raise DomainError("IDEMPOTENCY_CONFLICT", "PLANNING 编排的部分任务与 Router 计划不一致")
        orchestration = previous or OrchestrationRun(
            generate_database_id(), identity.platform_id, identity.tenant_id, conversation_id,
            team_id, team_version, status=OrchestrationStatus.PLANNING,
            approval_mode=version.approval_mode, idempotency_key=idempotency_key,
        )
        if previous is None:
            self.repository.save_orchestration(identity, orchestration)
            self.repository.append_event(
                identity,
                TaskEvent.new(orchestration.orchestration_id, "plan_created", payload={"plan_revision": orchestration.plan_revision}),
            )
        task_ids: dict[str, str] = {key: task.task_id for key, task in existing_by_key.items()}
        for item, client_key in zip(plan, client_keys):
            if client_key in task_ids:
                continue
            task_id = self._stable_task_id(identity, orchestration.orchestration_id, client_key)
            task_ids[client_key] = task_id
            self.repository.save_task(
                identity,
                TaskNode(
                    task_id,
                    orchestration.orchestration_id,
                    client_key,
                    str(item.get("title", client_key)),
                    instruction_ref=str(item.get("instruction_ref", "")),
                    required=bool(item.get("required", True)),
                    required_capabilities=tuple(str(value) for value in item.get("required_capabilities", ()) or ()),
                    preferred_engine_name=(str(item["preferred_engine"]) if item.get("preferred_engine") else None),
                    preferred_member_id=(str(item["preferred_member_id"]) if item.get("preferred_member_id") else None),
                ),
            )
        for client_key, dependencies in dependency_map.items():
            for dependency in dependencies:
                self.repository.save_dependency(
                    identity,
                    orchestration.orchestration_id,
                    TaskDependency(task_ids[client_key], task_ids[dependency]),
                )
        if target_status is OrchestrationStatus.READY:
            for client_key, dependencies in dependency_map.items():
                if not dependencies:
                    task = self.repository.get_task(identity, task_ids[client_key])
                    if task.status is TaskStatus.PROPOSED:
                        self.repository.save_task(identity, replace(task, status=TaskStatus.READY))
        completed = orchestration.transition(target_status)
        self.repository.save_orchestration(identity, completed)
        self._idempotency[self._scope(identity, conversation_id, idempotency_key)] = completed
        return completed

    def fail_planning(self, identity: IdentityContext, orchestration_id: str, error_code: str) -> OrchestrationRun:
        """Worker 计划 Run 明确失败时保留失败状态和稳定错误类别。"""

        current = self.get(identity, orchestration_id)
        if current.status is not OrchestrationStatus.PLANNING:
            return current
        failed = current.transition(OrchestrationStatus.FAILED)
        self.repository.save_orchestration(identity, failed)
        self.repository.append_event(
            identity,
            TaskEvent.new(orchestration_id, "router_plan_failed", payload={"error_code": error_code}),
        )
        self._idempotency[self._scope(identity, current.conversation_id, current.idempotency_key or "")] = failed
        return failed

    def _assert_idempotent_plan(
        self, identity: IdentityContext, existing: OrchestrationRun, plan: Sequence[Mapping[str, object]]
    ) -> None:
        """服务重启后的同键重放必须与已持久计划一致，避免返回旧计划却覆盖新内容。"""

        existing_tasks = self.repository.list_tasks(identity, existing.orchestration_id, limit=100).items
        existing_by_key = {task.client_key: task for task in existing_tasks}
        existing_by_id = {task.task_id: task for task in existing_tasks}
        proposed_by_key = {str(item.get("client_key", "")).strip(): item for item in plan}
        same_tasks = set(existing_by_key) == set(proposed_by_key) and all(
            existing_by_key[key].title == str(item.get("title", key))
            and existing_by_key[key].instruction_ref == str(item.get("instruction_ref", ""))
            and existing_by_key[key].required == bool(item.get("required", True))
            and existing_by_key[key].required_capabilities == tuple(sorted({str(value) for value in item.get("required_capabilities", ()) or ()}))
            and existing_by_key[key].preferred_engine_name == (str(item["preferred_engine"]) if item.get("preferred_engine") else None)
            and existing_by_key[key].preferred_member_id == (str(item["preferred_member_id"]) if item.get("preferred_member_id") else None)
            for key, item in proposed_by_key.items()
        )
        actual_dependencies = {
            (existing_by_id[edge.task_id].client_key, existing_by_id[edge.depends_on_task_id].client_key)
            for edge in self.repository.list_dependencies(identity, existing.orchestration_id)
        }
        proposed_dependencies = {
            (str(item.get("client_key", "")).strip(), str(dependency))
            for item in plan
            for dependency in (item.get("depends_on", ()) or ())
        }
        if not same_tasks or actual_dependencies != proposed_dependencies:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同的团队任务计划")

    def get(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        return self.repository.get_orchestration(identity, orchestration_id)

    def find_by_idempotency(self, identity: IdentityContext, conversation_id: str, idempotency_key: str) -> OrchestrationRun | None:
        """从持久仓储恢复同一 Conversation 的幂等编排。"""

        value = self._idempotency.get(self._scope(identity, conversation_id, idempotency_key))
        return value or self.repository.get_orchestration_by_idempotency(identity, conversation_id, idempotency_key)

    def approve_plan(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        orchestration = self.get(identity, orchestration_id)
        if orchestration.status is not OrchestrationStatus.AWAITING_PLAN_APPROVAL:
            raise DomainError("INVALID_STATE", "当前编排不在等待计划确认状态")
        updated = orchestration.transition(OrchestrationStatus.READY)
        self.repository.save_orchestration(identity, updated)
        self.repository.append_event(
            identity,
            TaskEvent.new(orchestration_id, "plan_approved", payload={"plan_revision": updated.plan_revision}),
        )
        for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items:
            if not self.repository.list_dependencies(identity, orchestration_id):
                self.repository.save_task(identity, replace(task, status=TaskStatus.READY))
                continue
            dependencies = [
                item
                for item in self.repository.list_dependencies(identity, orchestration_id)
                if item.task_id == task.task_id
            ]
            if not dependencies:
                self.repository.save_task(identity, replace(task, status=TaskStatus.READY))
        return updated

    def start(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        """将已确认计划转入运行态，并追加一个可观测的启动事件。"""

        orchestration = self.get(identity, orchestration_id)
        if orchestration.status is OrchestrationStatus.RUNNING:
            return orchestration
        if orchestration.status is not OrchestrationStatus.READY:
            raise DomainError("INVALID_STATE", "只有 READY 编排可以启动")
        updated = orchestration.transition(OrchestrationStatus.RUNNING)
        self.repository.save_orchestration(identity, updated)
        self.repository.append_event(identity, TaskEvent.new(orchestration_id, "orchestration_started"))
        return updated

    def transition_task(
        self, identity: IdentityContext, task_id: str, target: TaskStatus | str
    ) -> TaskNode:
        task = self.repository.get_task(identity, task_id)
        target_status = TaskStatus(target)
        allowed = {
            TaskStatus.PROPOSED: {TaskStatus.READY, TaskStatus.CANCELLED, TaskStatus.BLOCKED},
            TaskStatus.READY: {TaskStatus.ASSIGNED, TaskStatus.CLAIMED, TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED},
            TaskStatus.ASSIGNED: {TaskStatus.CLAIMED, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
            TaskStatus.CLAIMED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
            TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.WAITING_APPROVAL},
            TaskStatus.FAILED: {TaskStatus.READY, TaskStatus.CANCELLED},
            TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.CANCELLED},
            TaskStatus.WAITING_APPROVAL: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
        }
        if target_status not in allowed.get(task.status, set()):
            raise DomainError("INVALID_STATE", f"任务不允许从 {task.status} 转为 {target_status}")
        now = datetime.now(UTC)
        terminal = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}
        updated = replace(
            task,
            status=target_status,
            started_at=task.started_at or (now if target_status is TaskStatus.RUNNING else None),
            finished_at=now if target_status in terminal else None,
        )
        self.repository.save_task(identity, updated)
        self.repository.append_event(
            identity,
            TaskEvent.new(task.orchestration_id, "task_state_changed", task_id=task.task_id, payload={"status": target_status.value}),
        )
        if target_status is TaskStatus.SUCCEEDED:
            self._release_dependents(identity, task.orchestration_id)
        elif target_status in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}:
            self._release_dependents(identity, task.orchestration_id)
            self._block_dependents(identity, task.orchestration_id)
        self._aggregate_orchestration(identity, task.orchestration_id)
        return updated

    def retry_task(self, identity: IdentityContext, task_id: str) -> TaskNode:
        task = self.repository.get_task(identity, task_id)
        if task.status is not TaskStatus.FAILED:
            raise DomainError("INVALID_STATE", "只有失败任务可以重试")
        if task.attempt >= task.max_attempts:
            raise DomainError("RETRY_EXHAUSTED", "任务已达到最大重试次数")
        updated = replace(task, status=TaskStatus.READY, attempt=task.attempt + 1, error_code=None, started_at=None, finished_at=None)
        self.repository.save_task(identity, updated)
        self.repository.append_event(
            identity,
            TaskEvent.new(task.orchestration_id, "task_retried", task_id=task.task_id, payload={"attempt": updated.attempt}),
        )
        orchestration = self.get(identity, task.orchestration_id)
        if orchestration.status in {OrchestrationStatus.FAILED, OrchestrationStatus.PARTIAL_FAILED}:
            orchestration = orchestration.transition(OrchestrationStatus.READY)
            self.repository.save_orchestration(identity, orchestration)
        self._aggregate_orchestration(identity, task.orchestration_id)
        return updated

    def timeout_task(self, identity: IdentityContext, task_id: str) -> TaskNode:
        task = self.repository.get_task(identity, task_id)
        if task.status is not TaskStatus.RUNNING:
            raise DomainError("INVALID_STATE", "只有运行中的任务可以超时")
        self.repository.save_task(identity, replace(task, error_code="RUNTIME_TIMEOUT"))
        return self.transition_task(identity, task_id, TaskStatus.FAILED)

    def cancel(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        orchestration = self.get(identity, orchestration_id)
        if orchestration.status in {OrchestrationStatus.SUCCEEDED, OrchestrationStatus.FAILED, OrchestrationStatus.CANCELLED}:
            raise DomainError("INVALID_STATE", "编排已经进入终态")
        for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items:
            if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}:
                self.repository.save_task(identity, replace(task, status=TaskStatus.CANCELLED))
        updated = replace(orchestration, status=OrchestrationStatus.CANCELLED)
        self.repository.save_orchestration(identity, updated)
        self.repository.append_event(
            identity,
            TaskEvent.new(orchestration_id, "orchestration_cancelled"),
        )
        return updated

    def pause(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        orchestration = self.get(identity, orchestration_id)
        updated = orchestration.transition(OrchestrationStatus.PAUSED)
        self.repository.save_orchestration(identity, updated)
        return updated

    def resume(self, identity: IdentityContext, orchestration_id: str) -> OrchestrationRun:
        orchestration = self.get(identity, orchestration_id)
        updated = orchestration.transition(OrchestrationStatus.READY)
        self.repository.save_orchestration(identity, updated)
        return updated

    def assign_task(
        self,
        identity: IdentityContext,
        task_id: str,
        member_id: str,
        *,
        source: AssignmentSource | str = AssignmentSource.USER,
    ) -> TaskNode:
        task = self.repository.get_task(identity, task_id)
        orchestration = self.get(identity, task.orchestration_id)
        team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
        member = next((item for item in team.members if item.member_id == member_id and item.status == "ACTIVE"), None)
        if member is None:
            raise DomainError("RESOURCE_NOT_FOUND", "团队成员不存在", {"resource": "team_member"})
        if task.status not in {TaskStatus.READY, TaskStatus.ASSIGNED}:
            raise DomainError("INVALID_STATE", "当前任务不允许重新分配")
        if set(task.required_capabilities).difference(member.capabilities):
            raise DomainError("ENGINE_CAPABILITY_UNSUPPORTED", "成员不满足任务能力要求")
        assignment_source = AssignmentSource(source)
        updated = replace(
            task,
            assigned_member_id=member_id,
            assignment_source=assignment_source,
            status=TaskStatus.ASSIGNED,
        )
        self.repository.save_task(identity, updated)
        self.repository.append_event(
            identity,
            TaskEvent.new(task.orchestration_id, "task_assigned", task_id=task.task_id, payload={"member_id": member_id, "source": assignment_source.value}),
        )
        return updated

    def _release_dependents(self, identity: IdentityContext, orchestration_id: str) -> None:
        tasks = {task.task_id: task for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items}
        for task in tasks.values():
            if task.status not in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}:
                continue
            dependencies = [
                item
                for item in self.repository.list_dependencies(identity, orchestration_id)
                if item.task_id == task.task_id
            ]
            if dependencies and all(self._dependency_satisfied(item, tasks[item.depends_on_task_id]) for item in dependencies):
                self.repository.save_task(identity, replace(task, status=TaskStatus.READY))

    def _block_dependents(self, identity: IdentityContext, orchestration_id: str) -> None:
        """将依赖 SUCCEEDED 的下游节点递归阻塞，避免计划失败后留下永远 PROPOSED 的任务。"""

        tasks = {task.task_id: task for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items}
        dependencies = self.repository.list_dependencies(identity, orchestration_id)
        changed = True
        while changed:
            changed = False
            for task in tasks.values():
                if task.status not in {TaskStatus.PROPOSED, TaskStatus.READY, TaskStatus.ASSIGNED}:
                    continue
                edges = [edge for edge in dependencies if edge.task_id == task.task_id]
                blocked = any(
                    edge.condition == "SUCCEEDED"
                    and tasks[edge.depends_on_task_id].status in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}
                    for edge in edges
                )
                if blocked:
                    updated = replace(task, status=TaskStatus.BLOCKED, error_code="DEPENDENCY_FAILED", finished_at=datetime.now(UTC))
                    self.repository.save_task(identity, updated)
                    self.repository.append_event(
                        identity,
                        TaskEvent.new(orchestration_id, "task_blocked", task_id=task.task_id, payload={"error_code": "DEPENDENCY_FAILED"}),
                    )
                    tasks[task.task_id] = updated
                    changed = True

    @staticmethod
    def _dependency_satisfied(edge: TaskDependency, source: TaskNode) -> bool:
        if edge.condition == "SUCCEEDED":
            return source.status is TaskStatus.SUCCEEDED
        return source.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED, TaskStatus.BLOCKED}

    def _aggregate_orchestration(self, identity: IdentityContext, orchestration_id: str) -> None:
        orchestration = self.get(identity, orchestration_id)
        tasks = self.repository.list_tasks(identity, orchestration_id, limit=100).items
        if not tasks or any(task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED, TaskStatus.BLOCKED} for task in tasks):
            return
        required_failed = any(task.required and task.status in {TaskStatus.FAILED, TaskStatus.BLOCKED} for task in tasks)
        any_failed = any(task.status is TaskStatus.FAILED for task in tasks)
        target = OrchestrationStatus.FAILED if required_failed else (
            OrchestrationStatus.PARTIAL_FAILED if any_failed else OrchestrationStatus.SUCCEEDED
        )
        if orchestration.status in {OrchestrationStatus.RUNNING, OrchestrationStatus.READY, OrchestrationStatus.PAUSED, OrchestrationStatus.CANCELLING}:
            self.repository.save_orchestration(identity, replace(orchestration, status=target))

    @staticmethod
    def _assert_acyclic(dependencies: Mapping[str, tuple[str, ...]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise DomainError("TASK_GRAPH_CYCLE", "任务依赖关系存在环")
            if node in visited:
                return
            visiting.add(node)
            for parent in dependencies.get(node, ()):
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        for node in dependencies:
            visit(node)
