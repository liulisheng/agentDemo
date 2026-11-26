"""创建团队 Conversation 并为成员准备独立 Session 绑定。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent_platform.application.conversations import ConversationService
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError
from agent_platform.domain.conversations import AgentBinding, Conversation, ConversationMode
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.teams import ConversationTeamBinding, TeamMember

SessionFactory = Callable[[IdentityContext, TeamMember], str]


@dataclass(frozen=True, slots=True)
class TeamConversationCreation:
    """团队 Conversation 创建结果及成员 Session 绑定。"""

    conversation: Conversation
    team_binding: ConversationTeamBinding
    agent_bindings: tuple[AgentBinding, ...]


class TeamConversationService:
    """协调 Conversation、发布团队版本和成员 Session 的创建边界。"""

    def __init__(self, conversations: ConversationService, teams: TeamService) -> None:
        self.conversations = conversations
        self.teams = teams

    def create_team_conversation(
        self,
        identity: IdentityContext,
        title: str,
        team_id: str,
        team_version: int,
        session_factory: SessionFactory,
    ) -> TeamConversationCreation:
        version = self.teams.get(identity, team_id, team_version)
        # 先准备并验证所有成员 Session；如果部署无效，不留下一个无法执行的空 Conversation。
        session_ids = {
            member.member_id: session_factory(identity, member)
            for member in version.members
        }
        conversation = self.conversations.create_conversation(
            identity,
            title,
            is_group=True,
            conversation_mode=ConversationMode.TEAM,
        )
        binding = self.teams.bind_conversation(
            identity,
            conversation.conversation_id,
            team_id,
            team_version,
        )
        agent_bindings: list[AgentBinding] = []
        for member in version.members:
            agent_bindings.append(
                self.conversations.bind_agent(
                    identity,
                    conversation.conversation_id,
                    member.employee_id,
                    member.member_id,
                    session_ids[member.member_id],
                    team_member_id=member.member_id,
                    team_member_role=member.role.value,
                    capabilities_snapshot=member.capabilities,
                )
            )
        return TeamConversationCreation(conversation, binding, tuple(agent_bindings))

    def continue_as_team(
        self,
        identity: IdentityContext,
        source_conversation_id: str,
        team_id: str,
        team_version: int,
        session_factory: SessionFactory,
    ) -> TeamConversationCreation:
        source = self.conversations.get_conversation(identity, source_conversation_id)
        if source.conversation_mode is not ConversationMode.SINGLE_AGENT:
            raise DomainError(
                "INVALID_STATE",
                "只有 SINGLE_AGENT Conversation 可以继续为团队协作",
            )
        return self.create_team_conversation(
            identity,
            f"{source.title} · 团队协作",
            team_id,
            team_version,
            session_factory,
        )
