import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.teams import TeamService
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.teams import TeamMember, TeamMemberRole, TeamStatus


def _member(member_id: str = "member-1", role: TeamMemberRole = TeamMemberRole.MEMBER) -> TeamMember:
    return TeamMember(
        member_id=member_id,
        employee_id=f"employee-{member_id}",
        employee_version=1,
        deployment_id=f"deployment-{member_id}",
        role=role,
        engine_name="deepseek_harness",
        capabilities=("reasoning",),
    )


def test_admin_can_publish_team_version_but_published_version_is_immutable() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = TeamService()
    draft = service.create_draft(identity, "分析团队")
    service.add_member(identity, draft.team_id, draft.version, _member("router", TeamMemberRole.ROUTER))
    service.add_member(identity, draft.team_id, draft.version, _member("finalizer", TeamMemberRole.FINALIZER))

    published = service.publish(identity, draft.team_id, draft.version)

    assert published.status is TeamStatus.PUBLISHED
    with pytest.raises(DomainError, match="不可修改"):
        service.add_member(identity, draft.team_id, draft.version, _member("late"))


def test_unpublished_team_cannot_bind_conversation() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = TeamService()
    draft = service.create_draft(identity, "分析团队")

    with pytest.raises(DomainError, match="未发布"):
        service.bind_conversation(identity, "conversation-1", draft.team_id, draft.version)


def test_member_cannot_publish_team() -> None:
    admin = FakeIdentityProvider().resolve_identity("admin-local")
    member = FakeIdentityProvider().resolve_identity("member-local")
    service = TeamService()
    draft = service.create_draft(admin, "分析团队")

    with pytest.raises(DomainError, match="管理员"):
        service.publish(member, draft.team_id, draft.version)


def test_viewer_cannot_manage_team() -> None:
    admin = FakeIdentityProvider().resolve_identity("admin-local")
    viewer = IdentityContext("plt-local", "tenant-a", "viewer-local", frozenset({"viewer"}))
    service = TeamService()
    draft = service.create_draft(admin, "分析团队")

    with pytest.raises(DomainError, match="管理员"):
        service.publish(viewer, draft.team_id, draft.version)


def test_team_service_rejects_cross_tenant_member_context() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other_tenant = IdentityContext("plt-local", "tenant-b", "admin-other", frozenset({"admin", "member"}))
    service = TeamService()
    draft = service.create_draft(identity, "分析团队")

    with pytest.raises(DomainError, match="团队版本不存在"):
        service.get(other_tenant, draft.team_id, draft.version)


def test_team_policy_can_be_changed_only_on_draft_version() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = TeamService()
    draft = service.create_draft(identity, "分析团队")

    updated = service.update_policy(identity, draft.team_id, draft.version, "ROUTER", "AUTO")

    assert updated.orchestration_mode == "ROUTER"
    assert updated.approval_mode == "AUTO"
    service.add_member(identity, draft.team_id, draft.version, _member("router", TeamMemberRole.ROUTER))
    service.add_member(identity, draft.team_id, draft.version, _member("finalizer", TeamMemberRole.FINALIZER))
    service.publish(identity, draft.team_id, draft.version)
    with pytest.raises(DomainError, match="不可修改"):
        service.update_policy(identity, draft.team_id, draft.version, "HYBRID", "CONFIRM")


def test_forked_team_revision_keeps_existing_conversation_pinned_to_old_version() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = TeamService()
    draft = service.create_draft(identity, "分析团队")
    service.add_member(identity, draft.team_id, draft.version, _member("router", TeamMemberRole.ROUTER))
    service.add_member(identity, draft.team_id, draft.version, _member("finalizer", TeamMemberRole.FINALIZER))
    published = service.publish(identity, draft.team_id, draft.version)
    binding = service.bind_conversation(identity, "conversation-1", draft.team_id, published.version)

    next_draft = service.fork_revision(identity, draft.team_id, published.version)
    service.update_policy(identity, next_draft.team_id, next_draft.version, "ROUTER", "AUTO")

    assert next_draft.version == published.version + 1
    assert next_draft.status is TeamStatus.DRAFT
    assert service.get_binding(identity, binding.conversation_id).team_version == published.version
    assert service.get(identity, draft.team_id, published.version).orchestration_mode == "HYBRID"
    assert [value.version for value in service.list_versions(identity) if value.team_id == draft.team_id] == [2, 1]
