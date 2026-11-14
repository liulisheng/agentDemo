from collections.abc import Protocol

from agent_platform.domain.identity import IdentityContext


class IdentityProvider(Protocol):
    """身份解析端口，后续可替换为 Dadi User Center。"""

    def resolve_identity(self, user_id: str) -> IdentityContext:
        ...


class TenantDirectory(Protocol):
    """租户目录端口，用于验证平台和租户归属关系。"""

    def exists(self, platform_id: str, tenant_id: str) -> bool:
        ...


class AuthorizationProvider(Protocol):
    """资源授权端口，所有实现都必须使用已验证身份上下文。"""

    def can(self, identity: IdentityContext, action: str, resource_id: str) -> bool:
        ...
