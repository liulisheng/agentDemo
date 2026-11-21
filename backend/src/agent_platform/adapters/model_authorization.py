"""Agent Platform 首期租户模型授权策略。"""

from __future__ import annotations

from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, ModelRoute


class PlatformModelAuthorizationProvider:
    """允许当前租户使用其启用连接和模型目录项。员工权限由上层既有策略检查。"""

    authorization_source = "platform"

    def authorize(
        self,
        identity: IdentityContext,
        connection: ModelConnection,
        model: ModelCatalogItem,
    ) -> bool:
        return (
            identity.tenant_id == connection.tenant_id == model.tenant_id
            and model.connection_id == connection.connection_id
            and connection.status.value == "ACTIVE"
            and model.status.value == "ACTIVE"
        )

    def route_is_selectable(self, identity: IdentityContext, route: ModelRoute) -> bool:
        """员工编辑器仅可选当前租户的启用路由。"""

        return identity.tenant_id == route.tenant_id and route.status == "ACTIVE"
