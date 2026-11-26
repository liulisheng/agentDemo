import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.conversations import ConversationService
from agent_platform.application.team_conversations import TeamConversationService
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError
from agent_platform.domain.teams import TeamMember, TeamMemberRole


def _member(member_id: str, role: TeamMemberRole) -> TeamMember:
    return TeamMember(
        member_id=member_id,
        employee_id=f"employee-{member_id}",
        employee_version=1,
        deployment_id=f"deployment-{member_id}",
        role=role,
        engine_name="fake",
    )


def _published_team(identity):
    teams = TeamService()
    draft = teams.create_draft(identity, "分析团队")
    teams.add_member(identity, draft.team_id, draft.version, _member("router", TeamMemberRole.ROUTER))
    teams.add_member(identity, draft.team_id, draft.version, _member("finalizer", TeamMemberRole.FINALIZER))
    teams.publish(identity, draft.team_id, draft.version)
    return teams, draft


def test_team_conversation_creates_member_bindings_and_sessions() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, draft = _published_team(identity)
    conversations = ConversationService()
    created_sessions: list[str] = []

    def create_session(_identity, member):
        session_id = f"session-{member.member_id}"
        created_sessions.append(session_id)
        return session_id

    result = TeamConversationService(conversations, teams).create_team_conversation(
        identity, "季度分析", draft.team_id, draft.version, create_session
    )

    assert result.conversation.conversation_mode.value == "TEAM"
    assert len(result.agent_bindings) == 2
    assert created_sessions == ["session-router", "session-finalizer"]
    assert teams.get_binding(identity, result.conversation.conversation_id).team_id == draft.team_id
    assert conversations.resolve_member_binding(identity, result.conversation.conversation_id, "router").session_id == "session-router"


def test_continue_as_team_creates_new_conversation_and_preserves_source() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, draft = _published_team(identity)
    conversations = ConversationService()
    source = conversations.create_conversation(identity, "原单员工任务", is_group=False)
    service = TeamConversationService(conversations, teams)

    result = service.continue_as_team(
        identity,
        source.conversation_id,
        draft.team_id,
        draft.version,
        lambda _identity, member: f"session-{member.member_id}",
    )

    assert result.conversation.conversation_id != source.conversation_id
    assert result.conversation.title == "原单员工任务 · 团队协作"
    assert conversations.get_conversation(identity, source.conversation_id).conversation_mode.value == "SINGLE_AGENT"


def test_continue_as_team_rejects_existing_team_conversation() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    teams, draft = _published_team(identity)
    conversations = ConversationService()
    source = TeamConversationService(conversations, teams).create_team_conversation(
        identity,
        "已有团队任务",
        draft.team_id,
        draft.version,
        lambda _identity, member: f"session-{member.member_id}",
    )

    with pytest.raises(DomainError, match="SINGLE_AGENT"):
        TeamConversationService(conversations, teams).continue_as_team(
            identity,
            source.conversation.conversation_id,
            draft.team_id,
            draft.version,
            lambda _identity, member: f"session-{member.member_id}",
        )
