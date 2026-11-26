"""将团队群聊消息交给 Router Agent 生成计划并保存为团队编排。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from threading import Lock

from agent_platform.application.conversations import ConversationService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import ConversationMode, Message, MessageSource
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.orchestration import OrchestrationRun, TaskEvent
from agent_platform.domain.teams import TeamMember


@dataclass(frozen=True, slots=True)
class TeamTurnSubmission:
    """Router 计划生成后返回的 Conversation 与 Orchestration 摘要。"""

    orchestration: OrchestrationRun
    router_run_id: str
    summary: str
    task_count: int
    created: bool


class TeamTurnService:
    """固定调用已发布团队 Router 成员，并验证模型返回的任务计划。"""

    def __init__(
        self,
        conversations: ConversationService,
        teams: TeamService,
        orchestrations: OrchestrationService,
        control_plane: object,
        task_executor: object,
    ) -> None:
        self.conversations = conversations
        self.teams = teams
        self.orchestrations = orchestrations
        self.control_plane = control_plane
        self.task_executor = task_executor
        self._locks: dict[tuple[str, str, str], Lock] = {}
        self._guard = Lock()

    def submit(
        self, identity: IdentityContext, conversation_id: str, text: str, idempotency_key: str
    ) -> TeamTurnSubmission:
        if not text.strip() or not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "消息正文和幂等键不能为空")
        if len(idempotency_key) > 255:
            raise DomainError("INVALID_ARGUMENT", "幂等键超过长度上限")
        key = (identity.platform_id, identity.tenant_id, conversation_id + ":" + idempotency_key)
        with self._guard:
            lock = self._locks.setdefault(key, Lock())
        with lock:
            existing = self.orchestrations.find_by_idempotency(identity, conversation_id, idempotency_key)
            if existing is not None:
                if existing.status.value == "PLANNING":
                    self.reconcile_pending(identity, conversation_id)
                    existing = self.orchestrations.get(identity, existing.orchestration_id)
                tasks = self.orchestrations.repository.list_tasks(identity, existing.orchestration_id, limit=100).items
                events = self.orchestrations.repository.list_events(identity, existing.orchestration_id)
                prompt_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                previous_digest = next(
                    (str(event.payload["prompt_sha256"]) for event in events if event.payload.get("prompt_sha256")),
                    None,
                )
                if previous_digest is not None and previous_digest != prompt_digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同的团队消息")
                router_run_id = next(
                    (str(event.payload["router_run_id"]) for event in events if event.payload.get("router_run_id")),
                    "",
                )
                summary = (
                    "Router 计划正在由 Runtime Worker 生成。"
                    if existing.status.value == "PLANNING"
                    else f"已恢复计划，共 {len(tasks)} 个任务。"
                    if existing.status.value not in {"FAILED", "CANCELLED"}
                    else "Router 计划已结束，请检查失败事件后重试。"
                )
                if existing.status.value != "PLANNING" or router_run_id:
                    return TeamTurnSubmission(existing, router_run_id, summary, len(tasks), False)

            conversation = self.conversations.get_conversation(identity, conversation_id)
            if conversation.conversation_mode is not ConversationMode.TEAM:
                raise DomainError("CONVERSATION_MODE_MISMATCH", "只有 TEAM Conversation 可以提交团队任务")
            binding = self.teams.get_binding(identity, conversation_id)
            team = self.teams.get(identity, binding.team_id, binding.team_version)
            router = next((member for member in team.members if member.member_id == team.router_member_id), None)
            if router is None or router.status != "ACTIVE":
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "团队尚未配置 ACTIVE Router Agent")
            agent_binding = self.conversations.resolve_member_binding(identity, conversation_id, router.member_id)
            session = self.control_plane.get_session(identity, agent_binding.session_id)
            if self._normalize_engine(str(session.engine_name)) != self._normalize_engine(router.engine_name):
                raise DomainError("ENGINE_MODEL_INCOMPATIBLE", "Router Agent 引擎与团队固定 Session 不一致")

            members = [
                {
                    "member_id": member.member_id,
                    "role": member.role.value,
                    "engine_name": member.engine_name,
                    "capabilities": list(member.capabilities),
                    "max_concurrency": member.max_concurrency,
                }
                for member in team.members if member.status == "ACTIVE"
            ]
            history = self.conversations.list_messages(identity, conversation_id)
            display_message_id = self._stable_message_id(
                identity.platform_id, identity.tenant_id, conversation_id, idempotency_key
            )
            history = [message for message in history if message.message_id != display_message_id]
            router_prompt = self._router_prompt(text, members, history)
            execution_repository = getattr(self.control_plane, "persistence", None)
            if execution_repository is not None and hasattr(execution_repository, "submit_worker_run"):
                return self._submit_queued_router_run(
                    identity, conversation_id, text, idempotency_key, binding.team_id,
                    binding.team_version, router, agent_binding, router_prompt,
                )
            router_run = self.control_plane.create_run(
                identity, agent_binding.session_id, router_prompt,
                idempotency_key=f"team-router:{conversation_id}:{idempotency_key}"[-255:],
            )
            if router_run.status.value != "succeeded":
                raise DomainError("PLAN_GENERATION_FAILED", "Router Agent 未能生成有效任务计划")
            try:
                summary, plan = self._parse_plan(router_run.result_text)
            except DomainError:
                if not self._is_simulation(router.engine_name):
                    raise
                summary = "模拟模式：当前 EngineProvider 未生成结构化拆分，已将本次请求作为一个任务交给 Router 成员。"
                plan = [{
                    "client_key": "simulated-request",
                    "title": "处理用户请求",
                    "instruction_ref": text,
                    "depends_on": [],
                    "required_capabilities": [],
                    "preferred_member_id": router.member_id,
                }]
            plan = self._bind_plan_to_members(plan, team.members)
            orchestration = self.orchestrations.create_from_plan(
                identity, conversation_id, binding.team_id, binding.team_version, idempotency_key, plan
            )
            self.conversations.ingest(
                identity, conversation_id, identity.user_id, text,
                source=MessageSource.USER.value, session_id=agent_binding.session_id,
                message_id=self._stable_message_id(identity.platform_id, identity.tenant_id, conversation_id, idempotency_key),
            )
            self.conversations.ingest(
                identity, conversation_id, router.employee_id,
                summary or f"已将需求拆分为 {len(plan)} 个任务，等待确认后执行。",
                source=MessageSource.AGENT.value, session_id=agent_binding.session_id,
                message_id=router_run.run_id, run_id=router_run.run_id,
                sender_name=agent_binding.employee_name, team_member_id=router.member_id,
            )
            self.orchestrations.repository.append_event(
                identity,
                TaskEvent.new(
                    orchestration.orchestration_id,
                    "router_plan_generated",
                    actor_type="AGENT",
                    actor_id=router.member_id,
                    payload={"router_run_id": router_run.run_id, "engine_name": router.engine_name, "task_count": len(plan), "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
                ),
            )
            if orchestration.status.value == "READY":
                self.task_executor.start_ready(identity, orchestration.orchestration_id)
            return TeamTurnSubmission(orchestration, router_run.run_id, summary, len(plan), True)

    def _submit_queued_router_run(
        self,
        identity: IdentityContext,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        team_id: str,
        team_version: int,
        router: TeamMember,
        agent_binding: object,
        router_prompt: str,
    ) -> TeamTurnSubmission:
        """将 Router Run 排入受信 Worker 队列；API 进程不持有模型凭据。"""

        display_message_id = self._stable_message_id(
            identity.platform_id, identity.tenant_id, conversation_id, idempotency_key
        )
        prompt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.conversations.ingest(
            identity, conversation_id, identity.user_id, text,
            source=MessageSource.USER.value, session_id=agent_binding.session_id,
            message_id=display_message_id,
        )
        orchestration = self.orchestrations.create_planning(
            identity, conversation_id, team_id, team_version, idempotency_key,
            prompt_sha256=prompt_sha256, input_message_id=display_message_id,
        )
        return self._queue_router_run(
            identity, conversation_id, text, idempotency_key, orchestration,
            router, agent_binding, router_prompt,
        )

    def replan(self, identity: IdentityContext, orchestration_id: str) -> TeamTurnSubmission:
        """为已有编排创建新的独立计划；旧计划和运行中 Task 保持原样。"""

        current = self.orchestrations.get(identity, orchestration_id)
        if current.status.value in {"PLANNING", "CANCELLED"}:
            raise DomainError("INVALID_STATE", "当前计划正在生成或已取消，不能重新规划")
        events = self.orchestrations.repository.list_events(identity, orchestration_id)
        requested = next((event for event in events if event.event_type == "plan_requested"), None)
        input_message_id = str(requested.payload.get("input_message_id", "")) if requested else ""
        user_messages = [
            message for message in self.conversations.list_messages(identity, current.conversation_id)
            if message.source is MessageSource.USER
        ]
        original = next((message for message in user_messages if message.message_id == input_message_id), None)
        if original is None:
            prompt_digest = next(
                (str(event.payload["prompt_sha256"]) for event in events if event.payload.get("prompt_sha256")),
                None,
            )
            if prompt_digest is not None:
                original = next(
                    (message for message in user_messages if hashlib.sha256(message.text.encode("utf-8")).hexdigest() == prompt_digest),
                    None,
                )
        if original is None and user_messages:
            original = user_messages[-1]
        if original is None:
            raise DomainError("RESOURCE_NOT_FOUND", "找不到原始用户需求，无法重新规划")
        new_request = f"请基于原始需求重新规划，并保留仍有效的上下文：\n{original.text}"
        return self.submit(identity, current.conversation_id, new_request, generate_database_id())

    def _queue_router_run(
        self,
        identity: IdentityContext,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        orchestration: OrchestrationRun,
        router: TeamMember,
        agent_binding: object,
        router_prompt: str,
    ) -> TeamTurnSubmission:
        """补投或首次投递 Router Run；内部 SYSTEM 输入与可见用户消息分离。"""

        router_key = f"team-router:{conversation_id}:{idempotency_key}"[-255:]
        try:
            run = self.control_plane.prepare_conversation_turn(
                identity, agent_binding.session_id, conversation_id, router_prompt
            )
            run = replace(run, orchestration_id=orchestration.orchestration_id)
            input_message = Message(
                self._stable_message_id(identity.platform_id, identity.tenant_id, conversation_id, f"{idempotency_key}:router-input"),
                conversation_id, identity.platform_id, identity.tenant_id, identity.user_id,
                router_prompt, MessageSource.SYSTEM,
            )
            input_ref = None
            try:
                input_ref = self.conversations.store_message_body(identity, input_message)
                stored_run, created = self.control_plane.persistence.submit_worker_run(
                    identity,
                    conversation_id,
                    run,
                    router_key,
                    input_message,
                    input_content_ref=input_ref[0],
                    input_content_sha256=input_ref[1],
                    input_byte_length=input_ref[2],
                    input_content_type=input_ref[3],
                )
            except Exception:
                if self.conversations.object_store is not None and input_ref is not None:
                    self.conversations.object_store.delete(input_ref[0], identity.platform_id, identity.tenant_id)
                raise
            if not created and self.conversations.object_store is not None:
                self.conversations.object_store.delete(input_ref[0], identity.platform_id, identity.tenant_id)
            self.conversations.remember_message(identity, input_message)
            existing_events = self.orchestrations.repository.list_events(identity, orchestration.orchestration_id)
            if not any(
                event.event_type == "router_run_submitted"
                and event.payload.get("router_run_id") == stored_run.run_id
                for event in existing_events
            ):
                self.orchestrations.repository.append_event(
                    identity,
                    TaskEvent.new(
                        orchestration.orchestration_id,
                        "router_run_submitted",
                        actor_type="AGENT",
                        actor_id=router.member_id,
                        payload={
                            "router_run_id": stored_run.run_id,
                            "engine_name": router.engine_name,
                            "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        },
                    ),
                )
        except Exception as error:
            code = error.code if isinstance(error, DomainError) else "ROUTER_RUN_SUBMISSION_FAILED"
            self.orchestrations.fail_planning(identity, orchestration.orchestration_id, code)
            raise
        return TeamTurnSubmission(
            orchestration, stored_run.run_id, "Router 计划已提交，等待 Runtime Worker 执行。", 0, created
        )

    def reconcile_pending(self, identity: IdentityContext, conversation_id: str) -> None:
        """按 Worker Run 终态恢复 Router 计划；重复轮询不会创建重复任务或消息。"""

        for orchestration in self.orchestrations.repository.list_orchestrations(identity, conversation_id):
            if orchestration.status.value != "PLANNING":
                continue
            events = self.orchestrations.repository.list_events(identity, orchestration.orchestration_id)
            team = self.teams.get(identity, orchestration.team_id, orchestration.team_version)
            router = next((member for member in team.members if member.member_id == team.router_member_id), None)
            if router is None:
                self.orchestrations.fail_planning(identity, orchestration.orchestration_id, "ROUTER_MEMBER_UNAVAILABLE")
                continue
            binding = self.conversations.resolve_member_binding(identity, conversation_id, router.member_id)
            plan_requested = next((event for event in events if event.event_type == "plan_requested"), None)
            run_id = next(
                (str(event.payload["router_run_id"]) for event in events if event.payload.get("router_run_id")),
                None,
            )
            if run_id is None:
                persistence = getattr(self.control_plane, "persistence", None)
                router_key = f"team-router:{conversation_id}:{orchestration.idempotency_key or ''}"[-255:]
                run = persistence.get_run_by_idempotency(identity, binding.session_id, router_key) if persistence is not None and hasattr(persistence, "get_run_by_idempotency") else None
                if run is not None:
                    run_id = run.run_id
                    self.orchestrations.repository.append_event(
                        identity,
                        TaskEvent.new(orchestration.orchestration_id, "router_run_submitted", actor_type="SYSTEM", payload={"router_run_id": run_id, "engine_name": router.engine_name, "prompt_sha256": (plan_requested.payload.get("prompt_sha256") if plan_requested else None)}),
                    )
                else:
                    input_message_id = (
                        str(plan_requested.payload.get("input_message_id", ""))
                        if plan_requested and plan_requested.payload.get("input_message_id")
                        else self._stable_message_id(identity.platform_id, identity.tenant_id, conversation_id, orchestration.idempotency_key or "")
                    )
                    user_message = next((message for message in self.conversations.list_messages(identity, conversation_id) if message.message_id == input_message_id and message.source is MessageSource.USER), None)
                    if user_message is None:
                        continue
                    members = [
                        {"member_id": member.member_id, "role": member.role.value, "engine_name": member.engine_name, "capabilities": list(member.capabilities), "max_concurrency": member.max_concurrency}
                        for member in team.members if member.status == "ACTIVE"
                    ]
                    history = [message for message in self.conversations.list_messages(identity, conversation_id) if message.message_id != input_message_id]
                    self._queue_router_run(
                        identity, conversation_id, user_message.text, orchestration.idempotency_key or "",
                        orchestration, router, binding, self._router_prompt(user_message.text, members, history),
                    )
                    continue
            try:
                run = self.control_plane.get_run(identity, run_id)
            except DomainError as error:
                if error.code == "RESOURCE_NOT_FOUND":
                    self.orchestrations.fail_planning(identity, orchestration.orchestration_id, "ROUTER_RUN_UNAVAILABLE")
                    continue
                raise
            if run.status.value in {"queued", "starting", "running", "awaiting_approval", "cancelling"}:
                continue
            if run.status.value != "succeeded":
                error_code = run.error_category or f"ROUTER_RUN_{run.status.value.upper()}"
                failed = self.orchestrations.fail_planning(identity, orchestration.orchestration_id, error_code)
                self.conversations.ingest(
                    identity, conversation_id, router.employee_id,
                    f"Router Agent 执行失败（{error_code}）。请检查成员运行配置后重试。",
                    source=MessageSource.AGENT.value, session_id=binding.session_id,
                    message_id=self._stable_message_id(identity.platform_id, identity.tenant_id, conversation_id, f"router-failed:{run_id}"),
                    sender_name=binding.employee_name, team_member_id=router.member_id,
                )
                self.orchestrations.repository.append_event(
                    identity,
                    TaskEvent.new(failed.orchestration_id, "router_run_failed", actor_type="WORKER", payload={"run_id": run_id, "worker_id": run.worker_id, "error_code": error_code}),
                )
                continue
            try:
                summary, plan = self._parse_plan(run.result_text)
            except DomainError:
                if not self._is_simulation(router.engine_name):
                    self.orchestrations.fail_planning(identity, orchestration.orchestration_id, "PLAN_INVALID")
                    continue
                input_message_id = str(plan_requested.payload.get("input_message_id", "")) if plan_requested else ""
                original_message = next(
                    (message for message in self.conversations.list_messages(identity, conversation_id)
                     if message.message_id == input_message_id and message.source is MessageSource.USER),
                    None,
                )
                summary = "模拟模式：当前 EngineProvider 未生成结构化拆分，已将本次请求作为一个任务交给 Router 成员。"
                plan = [{
                    "client_key": "simulated-request", "title": "处理用户请求",
                    "instruction_ref": original_message.text if original_message is not None else "处理已提交的团队请求",
                    "depends_on": [], "required_capabilities": [],
                    "preferred_member_id": router.member_id,
                }]
            try:
                plan = self._bind_plan_to_members(plan, team.members)
                completed = self.orchestrations.create_from_plan(
                    identity, conversation_id, orchestration.team_id, orchestration.team_version,
                    orchestration.idempotency_key or "", plan,
                )
            except DomainError as error:
                failed = self.orchestrations.fail_planning(identity, orchestration.orchestration_id, error.code)
                self.conversations.ingest(
                    identity, conversation_id, router.employee_id,
                    f"Router 计划校验失败（{error.code}）。请调整成员或任务要求后重新提交。",
                    source=MessageSource.AGENT.value, session_id=binding.session_id,
                    message_id=self._stable_message_id(identity.platform_id, identity.tenant_id, conversation_id, f"router-plan-invalid:{run_id}"),
                    sender_name=binding.employee_name, team_member_id=router.member_id,
                )
                continue
            self.conversations.ingest(
                identity, conversation_id, router.employee_id, summary,
                source=MessageSource.AGENT.value, session_id=binding.session_id,
                message_id=run.run_id, run_id=run.run_id,
                sender_name=binding.employee_name, team_member_id=router.member_id,
            )
            self.orchestrations.repository.append_event(
                identity,
                TaskEvent.new(
                    completed.orchestration_id, "router_plan_generated", actor_type="WORKER",
                    payload={"router_run_id": run.run_id, "worker_id": run.worker_id, "engine_name": router.engine_name, "task_count": len(plan)},
                ),
            )
            if completed.status.value == "READY":
                self.task_executor.start_ready(identity, completed.orchestration_id)

    @staticmethod
    def _router_prompt(text: str, members: Sequence[Mapping[str, object]], history: Sequence[object] = ()) -> str:
        roster = json.dumps(list(members), ensure_ascii=False, separators=(",", ":"))
        history_text = json.dumps(
            [
                {"source": message.source.value, "text": message.text}
                for message in history[-12:]
                if message.source in {MessageSource.USER, MessageSource.AGENT}
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "你是 Agent 团队 Router。请把用户请求拆分成可并行执行的最小任务，说明任务依赖，"
            "并优先按成员能力分配。只输出一个 JSON 对象，不要 Markdown 或代码围栏。"
            "JSON 格式：{\"summary\":\"给用户看的计划摘要\",\"tasks\":["
            "{\"client_key\":\"稳定英文键\",\"title\":\"任务名称\",\"instruction_ref\":\"完整执行指令\","
            "\"depends_on\":[\"上游 client_key\"],\"required_capabilities\":[\"reasoning/tool_calls/workspace/mcp/approvals\"],"
            "\"preferred_member_id\":\"成员 ID\"}]}。任务最多 20 个；没有依赖时返回空数组。"
            f"\n可用成员：{roster}\n最近对话：{history_text}\n本次请求：\n{text}"
        )

    @staticmethod
    def _parse_plan(result_text: str) -> tuple[str, list[dict[str, object]]]:
        text = result_text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise DomainError("PLAN_INVALID", "Router Agent 响应中没有 JSON 任务计划")
        try:
            document = json.loads(text[start : end + 1])
        except json.JSONDecodeError as error:
            raise DomainError("PLAN_INVALID", "Router Agent 返回的任务计划格式无效") from error
        if not isinstance(document, dict) or not isinstance(document.get("tasks"), list):
            raise DomainError("PLAN_INVALID", "Router Agent 任务计划缺少 tasks 数组")
        if not 1 <= len(document["tasks"]) <= 20:
            raise DomainError("PLAN_INVALID", "Router Agent 任务数量必须在 1 到 20 之间")
        tasks: list[dict[str, object]] = []
        for item in document["tasks"]:
            if not isinstance(item, dict):
                raise DomainError("PLAN_INVALID", "Router Agent 任务必须是对象")
            for field in ("client_key", "title", "instruction_ref"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise DomainError("PLAN_INVALID", f"Router Agent 任务缺少有效的 {field}")
            dependencies = item.get("depends_on", [])
            capabilities = item.get("required_capabilities", [])
            if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
                raise DomainError("PLAN_INVALID", "Router Agent 依赖必须是字符串数组")
            if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
                raise DomainError("PLAN_INVALID", "Router Agent 能力要求必须是字符串数组")
            preferred_member_id = item.get("preferred_member_id")
            if preferred_member_id is not None and not isinstance(preferred_member_id, str):
                raise DomainError("PLAN_INVALID", "Router Agent 成员标识必须是字符串")
            tasks.append({
                "client_key": item.get("client_key", ""),
                "title": item.get("title", ""),
                "instruction_ref": item.get("instruction_ref", ""),
                "depends_on": dependencies,
                "required_capabilities": capabilities,
                "preferred_member_id": preferred_member_id,
            })
        summary = document.get("summary", "")
        return (summary.strip() if isinstance(summary, str) else "", tasks)

    @staticmethod
    def _bind_plan_to_members(plan: list[dict[str, object]], members: tuple[TeamMember, ...]) -> list[dict[str, object]]:
        by_id = {member.member_id: member for member in members if member.status == "ACTIVE"}
        for item in plan:
            member_id = item.get("preferred_member_id")
            if member_id is None:
                continue
            member = by_id.get(str(member_id))
            if member is None:
                raise DomainError("PLAN_INVALID", "Router 计划分配了不存在或不可用的团队成员")
            required = {str(value) for value in item.get("required_capabilities", ()) or ()}
            if required.difference(member.capabilities):
                raise DomainError("ENGINE_CAPABILITY_UNSUPPORTED", "Router 计划分配的成员不满足任务能力要求")
            item["preferred_engine"] = member.engine_name
        return plan

    @staticmethod
    def _normalize_engine(value: str) -> str:
        normalized = value.strip().casefold().replace("-", "_")
        return "deepseek_harness" if normalized == "deepseek" else normalized

    @staticmethod
    def _stable_message_id(platform_id: str, tenant_id: str, conversation_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{platform_id}:{tenant_id}:{conversation_id}:{idempotency_key}".encode()).digest()[:16]
        return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:26]

    def _is_simulation(self, engine_name: str) -> bool:
        registry = getattr(self.control_plane, "engine_providers", None)
        if registry is None:
            return self._normalize_engine(engine_name) == "fake"
        try:
            return bool(registry.resolve(engine_name).simulation)
        except DomainError:
            return self._normalize_engine(engine_name) == "fake"
