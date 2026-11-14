from agent_platform.adapters.channel.fake import FakeChannelAdapter
from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.conversations import ConversationService


def test_duplicate_channel_message_is_idempotent() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    channel = FakeChannelAdapter()
    first = channel.receive(identity, "web", "external-1", "conversation-1", "member-local", "hello")
    second = channel.receive(identity, "web", "external-1", "conversation-1", "member-local", "hello")
    assert first.message_id == second.message_id
    assert len(channel.messages(identity)) == 1


def test_group_message_requires_mention_or_command_and_prevents_agent_loop() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = ConversationService()
    conversation = service.create_conversation(identity, "群聊", is_group=True)
    service.bind_agent(identity, conversation.conversation_id, "employee-a", "行政助手", "session-a")
    service.bind_agent(identity, conversation.conversation_id, "employee-b", "业务助手", "session-b")

    ordinary = service.ingest(identity, conversation.conversation_id, "member-local", "普通消息", source="user")
    assert service.route(identity, ordinary.message_id) == ()

    mentioned = service.ingest(identity, conversation.conversation_id, "member-local", "@行政助手 请处理", source="user")
    assert service.route(identity, mentioned.message_id) == ("session-a",)

    agent_message = service.ingest(identity, conversation.conversation_id, "employee-a", "回复", source="agent", routing_depth=1)
    assert service.route(identity, agent_message.message_id) == ()


def test_delivery_retry_keeps_one_logical_delivery() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = ConversationService()
    conversation = service.create_conversation(identity, "单聊", is_group=False)
    delivery = service.deliver(identity, conversation.conversation_id, "hello", "idem-1")
    retry = service.retry_delivery(identity, delivery.delivery_id)
    assert retry.delivery_id == delivery.delivery_id
    assert retry.retry_count == 1
