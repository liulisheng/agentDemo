"""作用域化引擎与模型默认绑定的领域值。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeDefaultScope(StrEnum):
    """全局运行默认允许持久化的作用域。"""

    USER = "user"
    TENANT = "tenant"
    PLATFORM = "platform"


@dataclass(frozen=True, slots=True)
class EngineProfileDefaultBinding:
    """把一个精确 ExecutionProfile 版本绑定到用户、租户或平台默认。"""

    binding_id: str
    platform_id: str
    tenant_id: str
    scope_type: RuntimeDefaultScope
    scope_id: str
    profile_id: str
    profile_version: int
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_type", RuntimeDefaultScope(self.scope_type))
        for name in (
            "binding_id",
            "platform_id",
            "tenant_id",
            "scope_id",
            "profile_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.scope_type is RuntimeDefaultScope.TENANT and self.scope_id != self.tenant_id:
            raise ValueError("tenant scope_id must match tenant_id")
        if self.scope_type is RuntimeDefaultScope.PLATFORM and self.scope_id != self.platform_id:
            raise ValueError("platform scope_id must match platform_id")
        if self.profile_version < 1:
            raise ValueError("profile_version must be positive")
        if self.status not in {"ACTIVE", "DISABLED"}:
            raise ValueError("unsupported ExecutionProfile binding status")


@dataclass(frozen=True, slots=True)
class ModelRouteDefaultBinding:
    """把一个租户授权的 ModelRoute 绑定到独立运行默认作用域。"""

    binding_id: str
    platform_id: str
    tenant_id: str
    scope_type: RuntimeDefaultScope
    scope_id: str
    route_id: str
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_type", RuntimeDefaultScope(self.scope_type))
        for name in (
            "binding_id",
            "platform_id",
            "tenant_id",
            "scope_id",
            "route_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.scope_type is RuntimeDefaultScope.TENANT and self.scope_id != self.tenant_id:
            raise ValueError("tenant scope_id must match tenant_id")
        if self.scope_type is RuntimeDefaultScope.PLATFORM and self.scope_id != self.platform_id:
            raise ValueError("platform scope_id must match platform_id")
        if self.status not in {"ACTIVE", "DISABLED"}:
            raise ValueError("unsupported ModelRoute binding status")
