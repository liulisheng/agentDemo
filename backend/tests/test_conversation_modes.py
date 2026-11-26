import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.conversations import ConversationService
from agent_platform.domain.conversations import Conversation, ConversationMode


def test_conversation_defaults_to_single_agent_mode() -> None:
    value = Conversation("conversation-1", "platform", "tenant", "单员工")

    assert value.conversation_mode is ConversationMode.SINGLE_AGENT


def test_team_mode_requires_group_conversation() -> None:
    with pytest.raises(ValueError, match="TEAM conversation must be a group"):
        Conversation(
            "conversation-1",
            "platform",
            "tenant",
            "团队",
            False,
            conversation_mode=ConversationMode.TEAM,
        )


def test_conversation_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        Conversation(
            "conversation-1",
            "platform",
            "tenant",
            "未知模式",
            conversation_mode="UNKNOWN",
        )


def test_service_creates_explicit_team_mode() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    value = ConversationService().create_conversation(
        identity,
        "团队对话",
        is_group=True,
        conversation_mode=ConversationMode.TEAM,
    )

    assert value.conversation_mode is ConversationMode.TEAM
