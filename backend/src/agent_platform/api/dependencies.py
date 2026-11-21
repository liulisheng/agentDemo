from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversation_turns import ConversationTurnService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.model_management import ModelManagementService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.application.resource_center import ResourceCenterService
from agent_platform.application.skill_files import SkillFilePackageService
from agent_platform.application.tool_approval import ToolApprovalService
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


def conversation_service(request: Request) -> ConversationService:
    """读取应用启动时注入的 Conversation 服务。"""

    return request.app.state.conversation_service


def conversation_turn_service(request: Request) -> ConversationTurnService:
    """读取串联员工快照、消息对象和 Run 联合事务的 turn 服务。"""

    return request.app.state.conversation_turn_service


def model_route_service(request: Request) -> ModelRouteService:
    """读取应用启动时注入的模型路由服务。"""

    return request.app.state.model_route_service


def model_management_service(request: Request) -> ModelManagementService:
    """读取租户模型连接、目录、测试任务与可用路由管理服务。"""

    return request.app.state.model_management_service


def resource_center_service(request: Request) -> ResourceCenterService:
    """读取 app factory 显式注入的 Resource Center 服务。"""

    service = request.app.state.resource_center_service
    if service is None:
        from agent_platform.domain.common import DomainError

        raise DomainError("INVALID_STATE", "资源中心需要持久化 Repository 配置")
    return service


def skill_file_package_service(request: Request) -> SkillFilePackageService:
    """读取应用注入的文件包服务；未配置对象仓储时拒绝文件写入。"""

    service = request.app.state.skill_file_package_service
    if service is None:
        from agent_platform.domain.common import DomainError

        raise DomainError("INVALID_STATE", "Skill 文件包需要配置对象存储")
    return service


def tool_approval_service(request: Request) -> ToolApprovalService:
    """读取持久化审批服务；缺失 V006 仓储时 fail closed。"""

    service = request.app.state.tool_approval_service
    if service is None:
        from agent_platform.domain.common import DomainError

        raise DomainError("INVALID_STATE", "Tool Approval 需要 MCP Repository 和对象存储")
    return service
