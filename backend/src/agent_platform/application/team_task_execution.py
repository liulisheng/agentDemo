"""将团队 READY 任务投递到绑定成员的 EngineProvider，并收敛 Run、Attempt 和产物状态。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import Condition, Lock, get_ident

from agent_platform.application.artifacts import ArtifactService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.teams import TeamService
from agent_platform.application.worker_pools import WorkerLease, WorkerPoolRegistry
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import Message, MessageSource
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord
from agent_platform.domain.orchestration import TaskAttempt, TaskEvent, TaskNode, TaskStatus
from agent_platform.ports.orchestration_repository import OrchestrationRepository


class TeamTaskExecutionService:
    """按租户并发策略调度 READY Task；MySQL 模式只入队，Fake 模式本地执行。"""

    def __init__(
        self,
        teams: TeamService,
        orchestrations: OrchestrationService,
        conversations: ConversationService,
        control_plane: object,
        artifacts: ArtifactService,
        *,
        max_workers: int = 8,
        worker_pools: WorkerPoolRegistry | None = None,
    ) -> None:
        if max_workers < 1 or max_workers > 64:
            raise ValueError("max_workers 必须处于 1 到 64 之间")
        self.teams = teams
        self.orchestrations = orchestrations
        self.conversations = conversations
        self.control_plane = control_plane
        self.artifacts = artifacts
        self.worker_pools = worker_pools
        self.repository: OrchestrationRepository = orchestrations.repository
        self._max_workers = max_workers
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._active: set[tuple[str, str, str]] = set()
        self._active_by_member: dict[tuple[str, str, str], int] = {}
        self._active_by_orchestration: dict[tuple[str, str, str], int] = {}
        self._lock = Lock()
        self._idle = Condition(self._lock)

    def start_ready(self, identity: IdentityContext, orchestration_id: str) -> tuple[Future[None], ...]:
        """调度当前 READY 任务；MySQL 重复调用复用持久 Attempt，Fake 重复调用去重本地队列。"""

        orchestration = self.orchestrations.get(identity, orchestration_id)
        if orchestration.status.value == "AWAITING_PLAN_APPROVAL":
            return ()
        if orchestration.status.value == "READY":
            self.orchestrations.start(identity, orchestration_id)
        if orchestration.status.value not in {"READY", "RUNNING"}:
            return ()
        execution_repository = getattr(self.control_plane, "persistence", None)
        if execution_repository is not None and hasattr(execution_repository, "submit_worker_run"):
            self._queue_ready_for_run_worker(identity, orchestration_id)
            return ()
        key_prefix = (identity.platform_id, identity.tenant_id)
        team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
        orchestration_key = (*key_prefix, orchestration_id)
        futures: list[Future[None]] = []
        for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items:
            if task.status not in {TaskStatus.READY, TaskStatus.ASSIGNED}:
                continue
            try:
                member = self._select_member(task, team.members)
            except DomainError:
                member = None
            key = (*key_prefix, task.task_id)
            member_key = (*key_prefix, member.member_id) if member is not None else None
            with self._lock:
                if key in self._active:
                    continue
                if self._active_by_orchestration.get(orchestration_key, 0) >= team.max_concurrency:
                    continue
                if member_key is not None and self._active_by_member.get(member_key, 0) >= member.max_concurrency:
                    continue
                self._active.add(key)
                self._active_by_orchestration[orchestration_key] = self._active_by_orchestration.get(orchestration_key, 0) + 1
                if member_key is not None:
                    self._active_by_member[member_key] = self._active_by_member.get(member_key, 0) + 1
            engine_name = self._normalize_engine(member.engine_name) if member is not None else "unavailable"
            futures.append(self._pool_for(engine_name).submit(self._execute_task, identity, orchestration_id, task.task_id, key, member))
        return tuple(futures)

    def _queue_ready_for_run_worker(self, identity: IdentityContext, orchestration_id: str) -> None:
        """只保存 QUEUED Run；实际 Runtime 调用由持有 SecretRef 的 Run Worker 执行。"""

        orchestration = self.orchestrations.get(identity, orchestration_id)
        team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
        tasks = self.repository.list_tasks(identity, orchestration_id, limit=100).items
        latest_attempts: dict[str, TaskAttempt] = {}
        active_count = 0
        active_by_member: dict[str, int] = {}
        for current_task in tasks:
            attempts = self.repository.list_attempts(identity, current_task.task_id)
            if not attempts:
                continue
            attempt = attempts[-1]
            latest_attempts[current_task.task_id] = attempt
            if attempt.status in {"QUEUED", "CLAIMED", "RUNNING"}:
                active_count += 1
                if current_task.assigned_member_id:
                    active_by_member[current_task.assigned_member_id] = active_by_member.get(current_task.assigned_member_id, 0) + 1

        for task in tasks:
            if task.status not in {TaskStatus.READY, TaskStatus.ASSIGNED}:
                continue
            previous_attempt = latest_attempts.get(task.task_id)
            if previous_attempt is not None and previous_attempt.run_id and previous_attempt.status in {"QUEUED", "CLAIMED", "RUNNING"}:
                continue
            if active_count >= team.max_concurrency:
                break
            try:
                member = self._select_member(task, team.members)
            except DomainError as error:
                self._fail_task(identity, task.task_id, error.code)
                continue
            if active_by_member.get(member.member_id, 0) >= member.max_concurrency:
                continue
            binding = self.conversations.resolve_member_binding(identity, orchestration.conversation_id, member.member_id)
            session = self.control_plane.get_session(identity, binding.session_id)
            engine_name = str(getattr(session, "engine_name", ""))
            if self._normalize_engine(engine_name) != self._normalize_engine(member.engine_name):
                self._fail_task(identity, task.task_id, "ENGINE_MODEL_INCOMPATIBLE")
                continue
            if task.assigned_member_id is None or task.status is TaskStatus.READY:
                task = self.orchestrations.assign_task(identity, task.task_id, member.member_id, source="ROUTER")
            attempt_no = task.attempt + 1
            worker_pool = f"{self._normalize_engine(engine_name)}-pool"
            candidate_attempt = TaskAttempt(
                generate_database_id(), task.task_id, attempt_no,
                f"{orchestration_id}:{task.task_id}:{attempt_no}",
                status="QUEUED", worker_pool=worker_pool,
            )
            prompt = self._task_prompt(identity, orchestration_id, task)
            try:
                run = self.control_plane.prepare_orchestration_run(
                    identity, binding.session_id, orchestration.conversation_id, prompt,
                    orchestration_id=orchestration_id, task_id=task.task_id, attempt_no=attempt_no,
                )
            except DomainError as error:
                self._fail_task(identity, task.task_id, error.code)
                self.repository.append_event(
                    identity,
                    TaskEvent.new(
                        orchestration_id, "task_dispatch_failed", task_id=task.task_id,
                        actor_type="SYSTEM", payload={"error_code": error.code, "engine_name": engine_name},
                    ),
                )
                continue
            attempt, _ = self.repository.create_attempt(identity, candidate_attempt)
            input_message = Message(
                generate_database_id(), orchestration.conversation_id, identity.platform_id,
                identity.tenant_id, identity.user_id, prompt, MessageSource.SYSTEM,
            )
            content_ref, content_sha256, byte_length, content_type = self.conversations.store_message_body(identity, input_message)
            try:
                queued_run, _ = self.control_plane.persistence.submit_worker_run(
                    identity,
                    orchestration.conversation_id,
                    run,
                    attempt.idempotency_key,
                    input_message,
                    input_content_ref=content_ref,
                    input_content_sha256=content_sha256,
                    input_byte_length=byte_length,
                    input_content_type=content_type,
                )
            except Exception:
                if self.conversations.object_store is not None:
                    self.conversations.object_store.delete(content_ref, identity.platform_id, identity.tenant_id)
                raise
            self.conversations.remember_message(identity, input_message)
            attempt = replace(
                attempt, run_id=queued_run.run_id, status=queued_run.status.value.upper(),
                worker_id=queued_run.worker_id, fencing_token=queued_run.fencing_token,
                worker_pool=worker_pool,
            )
            self.repository.save_attempt(identity, attempt)
            events = self.repository.list_events(identity, orchestration_id)
            if not any(
                event.event_type == "task_queued_for_worker"
                and event.payload.get("run_id") == queued_run.run_id
                for event in events
            ):
                self.repository.append_event(
                    identity,
                    TaskEvent.new(
                        orchestration_id, "task_queued_for_worker", task_id=task.task_id,
                        attempt_id=attempt.attempt_id, actor_type="SYSTEM",
                        payload={"run_id": queued_run.run_id, "member_id": member.member_id, "engine_name": engine_name, "worker_pool": worker_pool},
                    ),
                )
            active_count += 1
            active_by_member[member.member_id] = active_by_member.get(member.member_id, 0) + 1
            latest_attempts[task.task_id] = attempt

    def close(self) -> None:
        """停止接受新任务；当前 Runtime 调用继续收敛并写入持久状态。"""

        with self._lock:
            executors = tuple(self._executors.values())
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=False)

    def _pool_for(self, engine_name: str) -> ThreadPoolExecutor:
        """Fake 模式的进程内模拟池；MySQL 模式由 Run Worker engine filter 分池领取。"""

        with self._lock:
            executor = self._executors.get(engine_name)
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix=f"team-{engine_name}")
                self._executors[engine_name] = executor
            return executor

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """等待当前进程已接收的任务收敛，供关闭流程和集成测试观察。"""

        with self._idle:
            return self._idle.wait_for(lambda: not self._active, timeout=timeout)

    def reconcile_conversation(self, identity: IdentityContext, conversation_id: str) -> None:
        """恢复 Run/Task 的终态关联，并将租约失效的结果收敛为 UNKNOWN 错误。"""

        persistence = getattr(self.control_plane, "persistence", None)
        for orchestration in self.repository.list_orchestrations(identity, conversation_id):
            for task in self.repository.list_tasks(identity, orchestration.orchestration_id, limit=100).items:
                if task.status not in {TaskStatus.ASSIGNED, TaskStatus.CLAIMED, TaskStatus.RUNNING}:
                    continue
                attempts = self.repository.list_attempts(identity, task.task_id)
                if not attempts:
                    if task.status is not TaskStatus.ASSIGNED:
                        self._fail_task(identity, task.task_id, "TASK_EXECUTION_INTERRUPTED")
                    continue
                attempt = attempts[-1]
                if not attempt.run_id:
                    # ASSIGNED + QUEUED Attempt 表示 API 已写入分配但尚未完成 Run 入队；下方会幂等补投。
                    if task.status is not TaskStatus.ASSIGNED or attempt.status not in {"QUEUED", "FAILED"}:
                        self._fail_task(identity, task.task_id, "TASK_EXECUTION_INTERRUPTED")
                    continue
                try:
                    run = self.control_plane.get_run(identity, str(attempt.run_id))
                except DomainError:
                    self._fail_task(identity, task.task_id, "RUN_RECORD_UNAVAILABLE")
                    continue
                if run.status.value == "queued":
                    self.repository.save_attempt(identity, replace(attempt, status="QUEUED"))
                    continue
                if run.status.value in {"starting", "running", "awaiting_approval", "cancelling"}:
                    worker_status = "CLAIMED" if run.status.value == "starting" else run.status.value.upper()
                    self.repository.save_attempt(
                        identity,
                        replace(attempt, status=worker_status, worker_id=run.worker_id or attempt.worker_id, fencing_token=run.fencing_token),
                    )
                    current_task = self.repository.get_task(identity, task.task_id)
                    if run.status.value == "starting" and current_task.status is TaskStatus.ASSIGNED:
                        self.orchestrations.transition_task(identity, task.task_id, TaskStatus.CLAIMED)
                    elif run.status.value == "running" and current_task.status in {TaskStatus.ASSIGNED, TaskStatus.CLAIMED}:
                        self.orchestrations.transition_task(identity, task.task_id, TaskStatus.RUNNING)
                    if run.status.value == "running" and persistence is not None and hasattr(persistence, "is_run_lease_current"):
                        current_lease = persistence.is_run_lease_current(
                            identity, run.run_id, run.worker_id or "", run.fencing_token
                        )
                        if not current_lease:
                            if hasattr(persistence, "mark_unknown"):
                                persistence.mark_unknown(identity, run.run_id, run.fencing_token)
                            self.repository.save_attempt(identity, replace(attempt, status="UNKNOWN", error_code="RUNTIME_OUTCOME_UNKNOWN"))
                            self._fail_task(identity, task.task_id, "RUNTIME_OUTCOME_UNKNOWN")
                    continue
                if run.status.value in {"succeeded", "failed", "cancelled", "unknown"}:
                    self.repository.save_attempt(
                        identity,
                        replace(attempt, status=run.status.value.upper(), worker_id=run.worker_id or attempt.worker_id, fencing_token=run.fencing_token, error_code=run.error_category),
                    )
                    current_task = self.repository.get_task(identity, task.task_id)
                    if current_task.status is TaskStatus.CANCELLED:
                        continue
                    if run.status.value == "succeeded":
                        self._mark_task_running(identity, task.task_id)
                        team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
                        member = next((item for item in team.members if item.member_id == task.assigned_member_id), None)
                        self._complete_task(identity, orchestration.orchestration_id, task, run, run.snapshot.engine_name or "unknown", member)
                    elif run.status.value == "cancelled":
                        if current_task.status in {TaskStatus.READY, TaskStatus.ASSIGNED, TaskStatus.CLAIMED, TaskStatus.RUNNING}:
                            self.orchestrations.transition_task(identity, task.task_id, TaskStatus.CANCELLED)
                    else:
                        self._mark_task_running(identity, task.task_id)
                        self._fail_task(identity, task.task_id, run.error_category or f"RUN_{run.status.value.upper()}")
                    continue
                continue
            if orchestration.status.value in {"READY", "RUNNING"}:
                self.start_ready(identity, orchestration.orchestration_id)

    def _execute_task(
        self, identity: IdentityContext, orchestration_id: str, task_id: str,
        active_key: tuple[str, str, str], selected_member: object | None,
    ) -> None:
        lease: WorkerLease | None = None
        try:
            task = self.repository.get_task(identity, task_id)
            orchestration = self.orchestrations.get(identity, orchestration_id)
            if orchestration.status.value != "RUNNING" or task.status not in {TaskStatus.READY, TaskStatus.ASSIGNED}:
                return
            team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
            member = selected_member or self._select_member(task, team.members)
            binding = self.conversations.resolve_member_binding(
                identity, orchestration.conversation_id, member.member_id
            )
            session = self.control_plane.get_session(identity, binding.session_id)
            engine_name = str(getattr(session, "engine_name", ""))
            if self._normalize_engine(engine_name) != self._normalize_engine(member.engine_name):
                raise DomainError(
                    "ENGINE_MODEL_INCOMPATIBLE",
                    "团队成员配置的 EngineProvider 与固定 Session 不一致，请创建新的团队对话",
                )
            worker_id = f"team-executor-{get_ident()}"
            normalized_engine = self._normalize_engine(engine_name)
            if self.worker_pools is not None:
                self.worker_pools.enqueue(normalized_engine, task.task_id)
                lease = self.worker_pools.claim(normalized_engine, worker_id)

            if task.assigned_member_id is None:
                task = self.orchestrations.assign_task(
                    identity, task.task_id, member.member_id, source="ROUTER"
                )
            task = self.orchestrations.transition_task(identity, task.task_id, TaskStatus.RUNNING)
            attempt_no = task.attempt + 1
            worker_pool = f"{normalized_engine}-pool"
            attempt = TaskAttempt(
                generate_database_id(), task.task_id, attempt_no,
                f"{orchestration_id}:{task.task_id}:{attempt_no}",
                status="RUNNING", worker_id=worker_id, fencing_token=0,
                worker_pool=worker_pool,
            )
            stored_attempt, created = self.repository.create_attempt(identity, attempt)
            if not created:
                return
            self.repository.append_event(
                identity,
                TaskEvent.new(
                    orchestration_id, "task_run_started", task_id=task.task_id,
                    attempt_id=stored_attempt.attempt_id, actor_type="WORKER", actor_id=worker_id,
                    payload={"member_id": member.member_id, "engine_name": engine_name, "worker_pool": worker_pool},
                ),
            )
            prompt = self._task_prompt(identity, orchestration_id, task)

            def on_run_submitted(run_record: RunRecord, actual_worker_id: str, fencing_token: int) -> None:
                self.repository.save_attempt(
                    identity,
                    replace(stored_attempt, run_id=run_record.run_id, status="RUNNING", worker_id=actual_worker_id, fencing_token=fencing_token, worker_pool=worker_pool),
                )

            run = self.control_plane.run_orchestration_task(
                identity, binding.session_id, orchestration.conversation_id, prompt,
                orchestration_id=orchestration_id, task_id=task.task_id,
                attempt_no=attempt_no, idempotency_key=stored_attempt.idempotency_key,
                on_run_submitted=on_run_submitted,
                worker_id=worker_id,
            )
            current_task = self.repository.get_task(identity, task.task_id)
            attempt_status = "CANCELLED" if current_task.status is TaskStatus.CANCELLED else run.status.value.upper()
            self.repository.save_attempt(
                identity,
                replace(stored_attempt, status=attempt_status, run_id=run.run_id,
                        error_code=run.error_category, worker_pool=worker_pool),
            )
            if current_task.status is TaskStatus.CANCELLED:
                self.repository.append_event(
                    identity,
                    TaskEvent.new(
                        orchestration_id, "task_run_finished_after_cancel", task_id=task.task_id,
                        attempt_id=stored_attempt.attempt_id, actor_type="WORKER", actor_id=worker_id,
                        payload={"run_id": run.run_id, "status": "CANCELLED"},
                    ),
                )
                return
            self.repository.append_event(
                identity,
                TaskEvent.new(
                    orchestration_id, "task_run_finished", task_id=task.task_id,
                    attempt_id=stored_attempt.attempt_id, actor_type="WORKER", actor_id=worker_id,
                    payload={"run_id": run.run_id, "status": attempt_status, "engine_name": engine_name},
                ),
            )
            if attempt_status == "SUCCEEDED":
                self._complete_task(identity, orchestration_id, task, run, engine_name, member)
            else:
                self._fail_task(identity, task.task_id, run.error_category or "RUNTIME_FAILED")
        except DomainError as error:
            self._fail_task(identity, task_id, error.code)
        except Exception as error:  # noqa: BLE001 — 异常正文可能含凭据或用户数据，只保留稳定类别
            database_code = getattr(error, "errno", None)
            error_code = f"TASK_STORAGE_ERROR_{database_code}" if isinstance(database_code, int) else "TASK_EXECUTION_ERROR"
            self._fail_task(identity, task_id, error_code)
        finally:
            if lease is not None and self.worker_pools is not None:
                self.worker_pools.release(lease)
            with self._lock:
                self._active.discard(active_key)
                orchestration_key = (active_key[0], active_key[1], orchestration_id)
                active_count = self._active_by_orchestration.get(orchestration_key, 0) - 1
                if active_count <= 0:
                    self._active_by_orchestration.pop(orchestration_key, None)
                else:
                    self._active_by_orchestration[orchestration_key] = active_count
                if selected_member is not None:
                    member_key = (active_key[0], active_key[1], selected_member.member_id)
                    member_count = self._active_by_member.get(member_key, 0) - 1
                    if member_count <= 0:
                        self._active_by_member.pop(member_key, None)
                    else:
                        self._active_by_member[member_key] = member_count
                self._idle.notify_all()
            try:
                self.start_ready(identity, orchestration_id)
            except DomainError:
                # 计划已取消、暂停或进入终态时，不能再派发依赖任务。
                pass

    def _complete_task(
        self, identity: IdentityContext, orchestration_id: str, task: TaskNode, run: RunRecord,
        engine_name: str, member: object | None = None,
    ) -> None:
        current = self.repository.get_task(identity, task.task_id)
        if current.status in {TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.SKIPPED}:
            return
        output_ref = current.output_ref
        if run.result_text.strip() and not output_ref:
            orchestration = self.orchestrations.get(identity, orchestration_id)
            version = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
            artifact = self.artifacts.create_text(
                identity,
                self.orchestrations.get(identity, orchestration_id).conversation_id,
                orchestration_id,
                task.task_id,
                run.run_id,
                f"{task.client_key}-result.md",
                run.result_text,
                engine_name,
            )
            output_ref = artifact.artifact_id
            if version.delivery_policy == "ATTACHMENTS":
                self.artifacts.deliver(
                    identity, artifact.artifact_id, "web",
                    f"{orchestration_id}:{task.task_id}:automatic-web-delivery",
                )
        if run.result_text.strip() and member is not None:
            orchestration = self.orchestrations.get(identity, orchestration_id)
            binding = self.conversations.resolve_member_binding(
                identity, orchestration.conversation_id, member.member_id
            )
            self.conversations.ingest(
                identity,
                orchestration.conversation_id,
                member.employee_id,
                run.result_text,
                source="agent",
                session_id=binding.session_id,
                message_id=run.run_id,
                run_id=run.run_id,
                sender_name=binding.employee_name,
                team_member_id=member.member_id,
            )
        current = self.repository.get_task(identity, task.task_id)
        if output_ref != current.output_ref:
            self.repository.save_task(identity, replace(current, output_ref=output_ref))
        self.orchestrations.transition_task(identity, task.task_id, TaskStatus.SUCCEEDED)

    def _mark_task_running(self, identity: IdentityContext, task_id: str) -> None:
        """Worker 可能在两次 UI 轮询间完成；补记合法的领取/运行状态后再写终态。"""

        task = self.repository.get_task(identity, task_id)
        if task.status is TaskStatus.ASSIGNED:
            self.orchestrations.transition_task(identity, task_id, TaskStatus.CLAIMED)
            task = self.repository.get_task(identity, task_id)
        if task.status is TaskStatus.CLAIMED:
            self.orchestrations.transition_task(identity, task_id, TaskStatus.RUNNING)

    def _fail_task(self, identity: IdentityContext, task_id: str, error_code: str) -> None:
        try:
            task = self.repository.get_task(identity, task_id)
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SKIPPED}:
                return
            self.repository.save_task(identity, replace(task, error_code=error_code))
            if task.status in {TaskStatus.PROPOSED, TaskStatus.BLOCKED}:
                self.repository.save_task(identity, replace(task, status=TaskStatus.READY, error_code=error_code))
            self.orchestrations.transition_task(identity, task_id, TaskStatus.FAILED)
        except DomainError:
            return

    def _task_prompt(self, identity: IdentityContext, orchestration_id: str, task: TaskNode) -> str:
        dependencies = self.repository.list_dependencies(identity, orchestration_id)
        source_tasks = {item.task_id: item for item in self.repository.list_tasks(identity, orchestration_id, limit=100).items}
        context: list[str] = []
        for dependency in dependencies:
            if dependency.task_id != task.task_id:
                continue
            source = source_tasks[dependency.depends_on_task_id]
            if source.output_ref:
                try:
                    summary = self.artifacts.read_preview(identity, source.output_ref, max_bytes=32_000)
                except DomainError:
                    continue
                context.append(f"\n\n上游任务「{source.title}」的已授权结果：\n{summary}")
        return task.instruction_ref + "".join(context)

    def cancel_task(self, identity: IdentityContext, task_id: str) -> TaskNode:
        """请求取消底层 Run，再把任务置为 CANCELLED；不把不支持取消的引擎谎报为已停止。"""

        task = self.repository.get_task(identity, task_id)
        if task.status in {TaskStatus.RUNNING, TaskStatus.CLAIMED, TaskStatus.ASSIGNED}:
            self._request_run_cancel(identity, task_id)
        return self.orchestrations.transition_task(identity, task_id, TaskStatus.CANCELLED)

    def request_cancel_orchestration(self, identity: IdentityContext, orchestration_id: str) -> None:
        """先向运行中 Run 发出取消请求，再由聚合服务取消所有未终态任务。"""

        for task in self.repository.list_tasks(identity, orchestration_id, limit=100).items:
            if task.status in {TaskStatus.RUNNING, TaskStatus.CLAIMED, TaskStatus.ASSIGNED}:
                self._request_run_cancel(identity, task.task_id)

    def _request_run_cancel(self, identity: IdentityContext, task_id: str) -> bool:
        attempts = self.repository.list_attempts(identity, task_id)
        if not attempts or not attempts[-1].run_id:
            return False
        run_id = str(attempts[-1].run_id)
        try:
            run = self.control_plane.get_run(identity, run_id)
            if run.status.value in {"succeeded", "failed", "cancelled", "unknown"}:
                return run.status.value == "cancelled"
            persistence = self.control_plane.persistence
            if persistence is not None and hasattr(persistence, "request_run_cancellation"):
                try:
                    persistence.request_run_cancellation(identity, run_id)
                except DomainError:
                    # 任务协调器授权已完成；Run 提交者不同时由运行时能力决定能否实际停止。
                    pass
            session = self.control_plane.get_session(identity, run.session_id)
            runtime = self.control_plane._runtime_for_run(session, run)
            if not runtime.capabilities.cancellation or not session.runtime_session_ref:
                return False
            return bool(runtime.cancel(session.runtime_session_ref))
        except DomainError:
            return False

    @staticmethod
    def _select_member(task: TaskNode, members: tuple[object, ...]):
        candidates = [
            member for member in members
            if member.status == "ACTIVE"
            and set(task.required_capabilities).issubset(set(member.capabilities))
            and (task.assigned_member_id or task.preferred_member_id) in {None, member.member_id}
            and (task.preferred_engine_name is None or TeamTaskExecutionService._normalize_engine(member.engine_name) == TeamTaskExecutionService._normalize_engine(task.preferred_engine_name))
        ]
        if not candidates:
            raise DomainError("ENGINE_CAPABILITY_UNSUPPORTED", "没有可执行该任务的 ACTIVE 团队成员")
        return min(candidates, key=lambda item: (item.role.value != "MEMBER", item.member_id))

    @staticmethod
    def _normalize_engine(value: str) -> str:
        normalized = value.strip().casefold().replace("-", "_")
        return "deepseek_harness" if normalized == "deepseek" else normalized
