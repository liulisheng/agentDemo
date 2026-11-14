"""无副作用 Fake Channel，验证入站消息规范化和外部 ID 幂等。"""

from __future__ import annotations

from uuid import uuid4

from agent_platform.domain.conversations import InboundEvent, Message, MessageSource
from agent_platform.domain.identity import IdentityContext


class FakeChannelAdapter:
    """把 Webhook 输入映射成租户范围的 Message。"""

    def __init__(self) -> None:
        self._messages: dict[tuple[str, str, str], Message] = {}
        self._events: dict[str, InboundEvent] = {}

    def receive(
        self,
        identity: IdentityContext,
        channel_name: str,
        external_message_id: str,
        conversation_id: str,
        sender_id: str,
        text: str,
    ) -> InboundEvent:
        key = (identity.tenant_id, channel_name, external_message_id)
        message = self._messages.get(key)
        if message is None:
            message = Message(
                message_id=f"message-{uuid4().hex}",
                conversation_id=conversation_id,
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                sender_id=sender_id,
                text=text,
                source=MessageSource.CHANNEL,
                external_message_id=external_message_id,
            )
            self._messages[key] = message
        event = self._events.get(message.message_id)
        if event is None:
            event = InboundEvent(f"event-{uuid4().hex}", channel_name, external_message_id, message.message_id)
            self._events[message.message_id] = event
        return event

    def messages(self, identity: IdentityContext) -> tuple[Message, ...]:
        return tuple(message for message in self._messages.values() if message.tenant_id == identity.tenant_id)
