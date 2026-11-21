"""在用户可见 Conversation 中准备并提交一个异步员工 Run。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversations import ConversationService
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.conversations import AgentBinding, Message, MessageSource
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import RunRecord


@dataclass(frozen=True, slots=True)
class ConversationTurnSubmission:
    """一次 turn 入队结果；重放返回原有 Run 和 Message 标识。"""

    run: RunRecord
    message: Message
    created: bool


class ConversationTurnService:
    """协调会话绑定、RunSnapshot、正文对象和联合 Message/Run 提交。"""

    def __init__(
        self,
        conversations: ConversationService,
        control_plane: ControlPlaneService,
    ) -> None:
        self._conversations = conversations
        self._control_plane = control_plane
        self._memory_lock = Lock()
        self._memory_submissions: dict[
            tuple[str, str, str, str], ConversationTurnSubmission
        ] = {}

    def submit(
        self,
        identity: IdentityContext,
        conversation_id: str,
        text: str,
        idempotency_key: str,
        *,
        employee_id: str | None = None,
    ) -> ConversationTurnSubmission:
        """用固定员工 Session 和请求摘要提交 QUEUED Run，不等待 Runtime。"""

        if not text.strip():
            raise DomainError("INVALID_ARGUMENT", "turn text 不能为空")
        if not idempotency_key.strip():
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 不能为空")
        if len(idempotency_key) > 255:
            raise DomainError("INVALID_ARGUMENT", "idempotency_key 超过长度上限")
        binding = self._conversations.resolve_active_binding(
            identity, conversation_id, employee_id
        )
        run = self._control_plane.prepare_conversation_turn(
            identity, binding.session_id, conversation_id, text
        )
        persistence = self._control_plane.persistence
        if persistence is None:
            return self._submit_fake(identity, conversation_id, binding, text, idempotency_key, run)
        submit = getattr(persistence, "submit_conversation_turn", None)
        if not callable(submit):
            raise DomainError(
                "INVALID_STATE",
                "持久化 turn 需要支持 Message/Run 联合事务的仓储",
            )
        message = Message(
            generate_database_id(),
            conversation_id,
            identity.platform_id,
            identity.tenant_id,
            identity.user_id,
            text,
            MessageSource.USER,
        )
        content_ref, content_sha256, byte_length, content_type = (
            self._conversations.store_message_body(identity, message)
        )
        persisted_run, stored_message, created = submit(
            identity,
            conversation_id,
            run,
            message,
            idempotency_key,
            content_ref=content_ref,
            content_sha256=content_sha256,
            byte_length=byte_length,
            content_type=content_type,
        )
        persisted_message = Message(
            stored_message.message_id,
            conversation_id,
            identity.platform_id,
            identity.tenant_id,
            identity.user_id,
            text,
            MessageSource.USER,
        )
        self._conversations.remember_message(identity, persisted_message)
        self._conversations.note_run(
            identity,
            conversation_id,
            binding.employee_id,
            persisted_run.run_id,
            persisted_run.status.value.upper(),
        )
        return ConversationTurnSubmission(persisted_run, persisted_message, created)

    def _submit_fake(
        self,
        identity: IdentityContext,
        conversation_id: str,
        binding: AgentBinding,
        text: str,
        idempotency_key: str,
        prepared_run: RunRecord,
    ) -> ConversationTurnSubmission:
        session_id = binding.session_id
        employee_id = binding.employee_id
        key = (
            identity.platform_id,
            identity.tenant_id,
            session_id,
            idempotency_key,
        )
        with self._memory_lock:
            previous = self._memory_submissions.get(key)
            if previous is not None:
                if previous.run.prompt_digest != prepared_run.prompt_digest:
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容已改变"
                    )
                return ConversationTurnSubmission(
                    previous.run, previous.message, created=False
                )
            queued_run = self._control_plane.queue_prepared_run(
                identity, prepared_run, idempotency_key
            )
            message = self._conversations.ingest(
                identity,
                conversation_id,
                identity.user_id,
                text,
                source=MessageSource.USER.value,
                session_id=session_id,
            )
            result = ConversationTurnSubmission(queued_run, message, True)
            self._memory_submissions[key] = result
            self._conversations.note_run(
                identity,
                conversation_id,
                employee_id,
                queued_run.run_id,
                queued_run.status.value.upper(),
            )
            return result
