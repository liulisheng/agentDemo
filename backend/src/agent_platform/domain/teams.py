"""Agent 团队、版本、成员和 Conversation 绑定领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from agent_platform.domain.common import DomainError, _require_text


class TeamStatus(StrEnum):
    """团队及版本的生命周期状态。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class TeamMemberRole(StrEnum):
    """成员在团队编排中的职责。"""

    MEMBER = "MEMBER"
    ROUTER = "ROUTER"
    FINALIZER = "FINALIZER"


@dataclass(frozen=True, slots=True)
class TeamMember:
    """团队版本中的数字员工和固定运行能力快照。"""

    member_id: str
    employee_id: str
    employee_version: int
    deployment_id: str
    role: TeamMemberRole | str = TeamMemberRole.MEMBER
    engine_name: str = ""
    model_route_id: str | None = None
    capabilities: tuple[str, ...] = ()
    max_concurrency: int = 1
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        for name in ("member_id", "employee_id", "deployment_id", "engine_name"):
            _require_text(getattr(self, name), name)
        if self.employee_version < 1:
            raise ValueError("employee_version must be positive")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        object.__setattr__(self, "role", TeamMemberRole(self.role))
        object.__setattr__(self, "capabilities", tuple(sorted(set(self.capabilities))))


@dataclass(frozen=True, slots=True)
class TeamVersion:
    """不可变的团队配置版本；发布后不得原地修改。"""

    team_id: str
    platform_id: str
    tenant_id: str
    version: int
    name: str
    members: tuple[TeamMember, ...] = ()
    router_member_id: str | None = None
    finalizer_member_id: str | None = None
    orchestration_mode: str = "HYBRID"
    approval_mode: str = "CONFIRM"
    status: TeamStatus | str = TeamStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    max_concurrency: int = 3
    delivery_policy: str = "CONFIRM"

    def __post_init__(self) -> None:
        for name in ("team_id", "platform_id", "tenant_id", "name"):
            _require_text(getattr(self, name), name)
        if self.version < 1:
            raise ValueError("team version must be positive")
        if self.orchestration_mode not in {"USER", "ROUTER", "HYBRID"}:
            raise ValueError("unsupported orchestration_mode")
        if self.approval_mode not in {"CONFIRM", "AUTO", "MANUAL"}:
            raise ValueError("unsupported approval_mode")
        if not 1 <= self.max_concurrency <= 100:
            raise ValueError("max_concurrency must be between 1 and 100")
        if self.delivery_policy not in {"CONFIRM", "ATTACHMENTS", "MANUAL"}:
            raise ValueError("unsupported delivery_policy")
        member_ids = [member.member_id for member in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("team member ids must be unique")
        for selected_id in (self.router_member_id, self.finalizer_member_id):
            if selected_id is not None and selected_id not in member_ids:
                raise ValueError("selected team role must reference a member")
        object.__setattr__(self, "status", TeamStatus(self.status))
        object.__setattr__(self, "members", tuple(self.members))

    def add_member(self, member: TeamMember) -> TeamVersion:
        self.assert_mutable()
        if any(item.member_id == member.member_id for item in self.members):
            raise DomainError("RESOURCE_CONFLICT", "团队成员已存在")
        updates: dict[str, object] = {"members": (*self.members, member)}
        if member.role is TeamMemberRole.ROUTER:
            updates["router_member_id"] = member.member_id
        if member.role is TeamMemberRole.FINALIZER:
            updates["finalizer_member_id"] = member.member_id
        return replace(self, **updates)

    def remove_member(self, member_id: str) -> TeamVersion:
        self.assert_mutable()
        remaining = tuple(member for member in self.members if member.member_id != member_id)
        if len(remaining) == len(self.members):
            raise DomainError("RESOURCE_NOT_FOUND", "团队成员不存在", {"resource": "team_member"})
        return replace(
            self,
            members=remaining,
            router_member_id=None if self.router_member_id == member_id else self.router_member_id,
            finalizer_member_id=None if self.finalizer_member_id == member_id else self.finalizer_member_id,
        )

    def set_roles(self, router_member_id: str, finalizer_member_id: str) -> TeamVersion:
        """更新草稿的 Router/Finalizer 指针，并保持成员角色快照一致。"""

        self.assert_mutable()
        if router_member_id == finalizer_member_id:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Router Agent 和 Finalizer 必须是不同成员")
        member_ids = {member.member_id for member in self.members}
        if router_member_id not in member_ids or finalizer_member_id not in member_ids:
            raise DomainError("RESOURCE_NOT_FOUND", "Router Agent 或 Finalizer 成员不存在")
        updated_members = tuple(
            replace(member, role=(TeamMemberRole.ROUTER if member.member_id == router_member_id else TeamMemberRole.FINALIZER if member.member_id == finalizer_member_id else TeamMemberRole.MEMBER))
            for member in self.members
        )
        return replace(self, members=updated_members, router_member_id=router_member_id, finalizer_member_id=finalizer_member_id)

    def publish(self) -> TeamVersion:
        self.assert_mutable()
        if not self.members:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "团队至少需要一个成员")
        if self.router_member_id is None or self.finalizer_member_id is None:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "团队必须指定路由 Agent 和汇总 Agent")
        return replace(self, status=TeamStatus.PUBLISHED)

    def assert_mutable(self) -> None:
        if self.status is not TeamStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或归档的团队版本不可修改")


@dataclass(frozen=True, slots=True)
class Team:
    """团队稳定标识和当前版本指针。"""

    team_id: str
    platform_id: str
    tenant_id: str
    name: str
    status: TeamStatus | str = TeamStatus.DRAFT
    current_version: int = 1

    def __post_init__(self) -> None:
        for name in ("team_id", "platform_id", "tenant_id", "name"):
            _require_text(getattr(self, name), name)
        object.__setattr__(self, "status", TeamStatus(self.status))


@dataclass(frozen=True, slots=True)
class ConversationTeamBinding:
    """Conversation 对已发布 TeamVersion 的固定绑定。"""

    conversation_id: str
    team_id: str
    team_version: int
    orchestration_mode: str = "HYBRID"
    approval_mode: str = "CONFIRM"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for name in ("conversation_id", "team_id"):
            _require_text(getattr(self, name), name)
        if self.team_version < 1:
            raise ValueError("team_version must be positive")


@dataclass(frozen=True, slots=True)
class TeamAuditEvent:
    """团队配置和绑定操作的租户审计事件。"""

    event_id: str
    platform_id: str
    tenant_id: str
    actor_user_id: str
    event_type: str
    resource_type: str
    resource_id: str
    payload: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
