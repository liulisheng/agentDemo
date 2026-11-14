"""ExecutionProfile 绑定和用户、租户、平台策略解析。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class ExecutionProfileBinding:
    """把一个 Profile 绑定到明确的作用域，作用域自身不来自请求体。"""

    scope_type: str
    scope_id: str
    profile_id: str
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        if self.scope_type not in {"user", "tenant", "platform", "system"}:
            raise ValueError("unsupported profile binding scope")
        if not self.scope_id.strip() or not self.profile_id.strip():
            raise ValueError("profile binding identifiers must not be blank")


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    """Profile 解析结果及其来源，便于 Run 审计和回放。"""

    profile_id: str
    resolved_by_scope: str


class PolicyResolver:
    """按 User > Tenant > Platform > System 固定顺序解析 Profile。"""

    _precedence = ("user", "tenant", "platform", "system")

    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], ExecutionProfileBinding] = {}

    def bind(self, scope_type: str, scope_id: str, profile_id: str) -> ExecutionProfileBinding:
        binding = ExecutionProfileBinding(scope_type, scope_id, profile_id)
        self._bindings[(scope_type, scope_id)] = binding
        return binding

    def resolve(self, identity: IdentityContext) -> ResolvedPolicy:
        candidates = {
            "user": identity.user_id,
            "tenant": identity.tenant_id,
            "platform": identity.platform_id,
            "system": "default",
        }
        for scope in self._precedence:
            binding = self._bindings.get((scope, candidates[scope]))
            if binding is not None and binding.status == "ACTIVE":
                return ResolvedPolicy(binding.profile_id, scope)
        raise DomainError("DEPENDENCY_VALIDATION_ERROR", "没有可用的 ExecutionProfile")
