from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """请求期间使用的已验证身份和租户上下文。"""

    platform_id: str
    tenant_id: str
    user_id: str
    roles: frozenset[str]
    scopes: frozenset[str] = frozenset()
    auth_source: str = "fake"
