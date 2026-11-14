from dataclasses import dataclass

from agent_platform.domain.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class FakeUser:
    """本地开发使用的最小用户投影，不代表生产用户中心实现。"""

    user_id: str
    platform_id: str
    tenant_id: str
    roles: frozenset[str]
    status: str = "ACTIVE"


class FakeIdentityProvider:
    """为首批控制面切片提供确定性的本地身份解析。"""

    def __init__(self) -> None:
        self._users = {
            user.user_id: user
            for user in (
                FakeUser("admin-local", "plt-local", "tenant-a", frozenset({"admin", "member"})),
                FakeUser("member-local", "plt-local", "tenant-a", frozenset({"member"})),
                FakeUser("admin-other", "plt-local", "tenant-b", frozenset({"admin", "member"})),
                FakeUser("disabled-local", "plt-local", "tenant-a", frozenset({"member"}), "DISABLED"),
            )
        }

    def resolve_identity(self, user_id: str) -> IdentityContext:
        """按本地用户 ID 解析身份，并对未知或禁用用户 fail closed。"""

        try:
            user = self._users[user_id]
        except KeyError as error:
            raise PermissionError("unknown user") from error
        if user.status != "ACTIVE":
            raise PermissionError("user is not active")
        return IdentityContext(
            platform_id=user.platform_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            roles=user.roles,
        )


class FakeAuthorizationProvider:
    """使用租户前缀和角色集合执行第一批本地授权判断。"""

    def can(self, identity: IdentityContext, action: str, resource_id: str) -> bool:
        """只允许访问当前租户资源，管理员拥有完整本地权限。"""

        resource_scope, _, _ = resource_id.partition(":")
        if resource_scope != identity.tenant_id:
            return False
        if "admin" in identity.roles:
            return True
        return action.endswith(":read") and "member" in identity.roles
