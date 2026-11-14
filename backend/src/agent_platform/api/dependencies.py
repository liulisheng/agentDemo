from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.identity import IdentityContext

fake_identity_provider = FakeIdentityProvider()


def current_identity(
    fake_user: Annotated[str | None, Header(alias="X-Fake-User")] = None,
) -> IdentityContext:
    """从本地 Fake 用户头解析请求身份，生产环境将替换为 Dadi 适配器。"""

    if not fake_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="identity required")
    try:
        return fake_identity_provider.resolve_identity(fake_user)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


def catalog_service(request: Request) -> CatalogService:
    """读取应用启动时注入的目录服务，避免路由自行创建仓储。"""

    return request.app.state.catalog_service


def control_plane_service(request: Request) -> ControlPlaneService:
    """读取应用启动时注入的 Session/Run 编排服务。"""

    return request.app.state.control_plane_service
