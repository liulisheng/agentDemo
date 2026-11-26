"""团队草稿、版本发布和 Conversation 绑定服务。"""

from __future__ import annotations

from dataclasses import replace

from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.teams import (
    ConversationTeamBinding,
    Team,
    TeamAuditEvent,
    TeamMember,
    TeamStatus,
    TeamVersion,
)
from agent_platform.ports.team_repository import TeamRepository


class TeamService:
    """维护租户团队草稿、不可变版本和 Conversation 固定绑定。"""

    def __init__(self, repository: TeamRepository | None = None) -> None:
        self.repository = repository
        self._teams: dict[tuple[str, str, str], Team] = {}
        self._versions: dict[tuple[str, str, str, int], TeamVersion] = {}
        self._bindings: dict[tuple[str, str, str], ConversationTeamBinding] = {}
        self._audit_events: list[TeamAuditEvent] = []

    @staticmethod
    def _key(identity: IdentityContext, team_id: str) -> tuple[str, str, str]:
        return identity.platform_id, identity.tenant_id, team_id

    @staticmethod
    def _require_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以管理团队")

    def create_draft(self, identity: IdentityContext, name: str) -> TeamVersion:
        self._require_admin(identity)
        team_id = generate_database_id()
        team = Team(team_id, identity.platform_id, identity.tenant_id, name)
        version = TeamVersion(team_id, identity.platform_id, identity.tenant_id, 1, name)
        self._teams[self._key(identity, team_id)] = team
        self._versions[(*self._key(identity, team_id), 1)] = version
        if self.repository is not None:
            self.repository.save_team(identity, team)
            self.repository.save_version(identity, version)
        self._audit(identity, "TEAM_DRAFT_CREATED", "team", team_id, {"version": 1})
        return version

    def get(self, identity: IdentityContext, team_id: str, version: int) -> TeamVersion:
        if self.repository is not None:
            return self.repository.get_version(identity, team_id, version)
        key = (*self._key(identity, team_id), version)
        value = self._versions.get(key)
        if value is None:
            raise DomainError("RESOURCE_NOT_FOUND", "团队版本不存在", {"resource": "team_version"})
        return value

    def get_current(self, identity: IdentityContext, team_id: str) -> TeamVersion:
        if self.repository is not None:
            return self.repository.get_current(identity, team_id)
        team = self._teams.get(self._key(identity, team_id))
        if team is None:
            raise DomainError("RESOURCE_NOT_FOUND", "团队不存在", {"resource": "team"})
        return self.get(identity, team_id, team.current_version)

    def list_current(self, identity: IdentityContext) -> tuple[TeamVersion, ...]:
        if self.repository is not None:
            return self.repository.list_current(identity)
        values = []
        for (platform_id, tenant_id, _), team in self._teams.items():
            if (platform_id, tenant_id) != (identity.platform_id, identity.tenant_id):
                continue
            values.append(self.get(identity, team.team_id, team.current_version))
        return tuple(sorted(values, key=lambda value: value.name))

    def list_versions(self, identity: IdentityContext) -> tuple[TeamVersion, ...]:
        """返回租户可配置或可绑定的全部 TeamVersion，保留旧发布版本选择能力。"""

        if self.repository is not None:
            return self.repository.list_versions(identity)
        values = [
            version
            for (platform_id, tenant_id, _team_id, _version), version in self._versions.items()
            if platform_id == identity.platform_id and tenant_id == identity.tenant_id
        ]
        return tuple(sorted(values, key=lambda value: (value.name, -value.version)))

    def add_member(
        self, identity: IdentityContext, team_id: str, version: int, member: TeamMember
    ) -> TeamVersion:
        self._require_admin(identity)
        current = self.get(identity, team_id, version)
        if member.status != "ACTIVE":
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "只有 ACTIVE 成员可以加入团队")
        if member.member_id == "":
            raise DomainError("INVALID_ARGUMENT", "member_id 不能为空")
        updated = current.add_member(member)
        self._versions[(*self._key(identity, team_id), version)] = updated
        if self.repository is not None:
            self.repository.save_version(identity, updated)
        self._audit(identity, "TEAM_MEMBER_ADDED", "team_version", team_id, {"version": version, "member_id": member.member_id})
        return updated

    def remove_member(self, identity: IdentityContext, team_id: str, version: int, member_id: str) -> TeamVersion:
        self._require_admin(identity)
        current = self.get(identity, team_id, version)
        updated = current.remove_member(member_id)
        self._versions[(*self._key(identity, team_id), version)] = updated
        if self.repository is not None:
            self.repository.save_version(identity, updated)
        self._audit(identity, "TEAM_MEMBER_REMOVED", "team_version", team_id, {"version": version, "member_id": member_id})
        return updated

    def fork_revision(self, identity: IdentityContext, team_id: str, source_version: int) -> TeamVersion:
        """从已发布版本复制可编辑草稿；既有 Conversation 继续绑定来源版本。"""

        self._require_admin(identity)
        source = self.get(identity, team_id, source_version)
        if source.status is not TeamStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只能从已发布团队版本创建配置草稿")
        next_version = source_version + 1
        try:
            existing = self.get(identity, team_id, next_version)
        except DomainError as error:
            if error.code != "RESOURCE_NOT_FOUND":
                raise
        else:
            if existing.status is TeamStatus.DRAFT:
                return existing
            raise DomainError("TEAM_VERSION_CONFLICT", "下一团队版本已被占用")
        draft = TeamVersion(
            team_id=source.team_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            version=next_version,
            name=source.name,
            members=source.members,
            router_member_id=source.router_member_id,
            finalizer_member_id=source.finalizer_member_id,
            orchestration_mode=source.orchestration_mode,
            approval_mode=source.approval_mode,
            status=TeamStatus.DRAFT,
            max_concurrency=source.max_concurrency,
            delivery_policy=source.delivery_policy,
        )
        team = Team(team_id, identity.platform_id, identity.tenant_id, source.name, TeamStatus.DRAFT, next_version)
        self._teams[self._key(identity, team_id)] = team
        self._versions[(*self._key(identity, team_id), next_version)] = draft
        if self.repository is not None:
            self.repository.save_version(identity, draft)
            self.repository.save_team(identity, team)
        self._audit(identity, "TEAM_REVISION_CREATED", "team_version", team_id, {"source_version": source_version, "version": next_version})
        return draft

    def update_policy(
        self, identity: IdentityContext, team_id: str, version: int,
        orchestration_mode: str, approval_mode: str,
        max_concurrency: int | None = None, delivery_policy: str | None = None,
    ) -> TeamVersion:
        """只允许管理员修改草稿策略；已发布版本保持对现有 Conversation 不变。"""

        self._require_admin(identity)
        current = self.get(identity, team_id, version)
        current.assert_mutable()
        updated = replace(
            current,
            orchestration_mode=orchestration_mode,
            approval_mode=approval_mode,
            max_concurrency=current.max_concurrency if max_concurrency is None else max_concurrency,
            delivery_policy=current.delivery_policy if delivery_policy is None else delivery_policy,
        )
        self._versions[(*self._key(identity, team_id), version)] = updated
        if self.repository is not None:
            self.repository.save_version(identity, updated)
        self._audit(identity, "TEAM_POLICY_UPDATED", "team_version", team_id, {
            "version": version,
            "orchestration_mode": orchestration_mode,
            "approval_mode": approval_mode,
            "max_concurrency": updated.max_concurrency,
            "delivery_policy": updated.delivery_policy,
        })
        return updated

    def update_roles(self, identity: IdentityContext, team_id: str, version: int, router_member_id: str, finalizer_member_id: str) -> TeamVersion:
        self._require_admin(identity)
        current = self.get(identity, team_id, version)
        updated = current.set_roles(router_member_id, finalizer_member_id)
        self._versions[(*self._key(identity, team_id), version)] = updated
        if self.repository is not None:
            self.repository.save_version(identity, updated)
        self._audit(identity, "TEAM_ROLES_UPDATED", "team_version", team_id, {
            "version": version,
            "router_member_id": router_member_id,
            "finalizer_member_id": finalizer_member_id,
        })
        return updated

    def publish(
        self, identity: IdentityContext, team_id: str, version: int, *, expected_version: int | None = None
    ) -> TeamVersion:
        self._require_admin(identity)
        team = self._teams.get(self._key(identity, team_id))
        current_version = team.current_version if team is not None else version
        if expected_version is not None and expected_version != current_version:
            raise DomainError("TEAM_VERSION_CONFLICT", "团队配置版本已变化")
        current = self.get(identity, team_id, version)
        published = current.publish()
        self._versions[(*self._key(identity, team_id), version)] = published
        team = team or Team(team_id, identity.platform_id, identity.tenant_id, current.name)
        self._teams[self._key(identity, team_id)] = replace(team, status=TeamStatus.PUBLISHED, current_version=version)
        if self.repository is not None:
            self.repository.save_version(identity, published)
            self.repository.save_team(identity, self._teams[self._key(identity, team_id)])
        self._audit(identity, "TEAM_VERSION_PUBLISHED", "team_version", team_id, {"version": version})
        return published

    def bind_conversation(
        self,
        identity: IdentityContext,
        conversation_id: str,
        team_id: str,
        version: int,
    ) -> ConversationTeamBinding:
        current = self.get(identity, team_id, version)
        if current.status is not TeamStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "未发布的团队版本不能绑定 Conversation")
        key = (identity.platform_id, identity.tenant_id, conversation_id)
        binding = ConversationTeamBinding(
            conversation_id,
            team_id,
            version,
            current.orchestration_mode,
            current.approval_mode,
        )
        self._bindings[key] = binding
        if self.repository is not None:
            self.repository.save_binding(identity, binding)
        self._audit(identity, "CONVERSATION_TEAM_BOUND", "conversation", conversation_id, {"team_id": team_id, "version": version})
        return binding

    def get_binding(
        self, identity: IdentityContext, conversation_id: str
    ) -> ConversationTeamBinding:
        if self.repository is not None:
            return self.repository.get_binding(identity, conversation_id)
        try:
            return self._bindings[(identity.platform_id, identity.tenant_id, conversation_id)]
        except KeyError as error:
            raise DomainError("RESOURCE_NOT_FOUND", "Conversation 团队绑定不存在") from error

    def list_bindings(self, identity: IdentityContext, team_id: str) -> tuple[ConversationTeamBinding, ...]:
        if self.repository is not None:
            return self.repository.list_bindings(identity, team_id)
        return tuple(
            value
            for (platform_id, tenant_id, _), value in self._bindings.items()
            if platform_id == identity.platform_id and tenant_id == identity.tenant_id and value.team_id == team_id
        )

    def list_audit_events(self, identity: IdentityContext, team_id: str | None = None) -> tuple[TeamAuditEvent, ...]:
        return tuple(
            event
            for event in self._audit_events
            if event.platform_id == identity.platform_id
            and event.tenant_id == identity.tenant_id
            and (team_id is None or event.resource_id == team_id)
        )

    def _audit(self, identity: IdentityContext, event_type: str, resource_type: str, resource_id: str, payload: dict[str, object]) -> None:
        self._audit_events.append(
            TeamAuditEvent(
                generate_database_id(),
                identity.platform_id,
                identity.tenant_id,
                identity.user_id,
                event_type,
                resource_type,
                resource_id,
                payload,
            )
        )
