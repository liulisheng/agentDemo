"""路由共用的身份依赖和租户一致性校验。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from agent_platform.api.dependencies import (
    artifact_repository,
    artifact_service,
    catalog_service,
    control_plane_service,
    conversation_service,
    conversation_turn_service,
    current_identity,
    deployment_runtime_config_service,
    engine_provider_registry,
    model_management_service,
    model_route_service,
    orchestration_service,
    resource_center_service,
    runtime_defaults_service,
    skill_file_package_service,
    team_conversation_service,
    team_service,
    team_task_execution_service,
    team_turn_service,
    tool_approval_service,
    worker_pool_registry,
)
from agent_platform.api.schemas import TenantAwareRequest
from agent_platform.application.artifacts import ArtifactService
from agent_platform.application.catalog_service import CatalogService
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.conversation_turns import ConversationTurnService
from agent_platform.application.conversations import ConversationService
from agent_platform.application.deployment_runtime import DeploymentRuntimeConfigService
from agent_platform.application.engine_providers import EngineProviderRegistry
from agent_platform.application.model_management import ModelManagementService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.application.orchestration import OrchestrationService
from agent_platform.application.resource_center import ResourceCenterService
from agent_platform.application.runtime_defaults import RuntimeDefaultsService
from agent_platform.application.skill_files import SkillFilePackageService
from agent_platform.application.team_conversations import TeamConversationService
from agent_platform.application.team_task_execution import TeamTaskExecutionService
from agent_platform.application.team_turns import TeamTurnService
from agent_platform.application.teams import TeamService
from agent_platform.application.tool_approval import ToolApprovalService
from agent_platform.application.worker_pools import WorkerPoolRegistry
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.artifact_repository import ArtifactRepository

Identity = Annotated[IdentityContext, Depends(current_identity)]
Catalog = Annotated[CatalogService, Depends(catalog_service)]
ControlPlane = Annotated[ControlPlaneService, Depends(control_plane_service)]
DeploymentRuntimeConfigManagement = Annotated[
    DeploymentRuntimeConfigService, Depends(deployment_runtime_config_service)
]
EngineProviders = Annotated[EngineProviderRegistry, Depends(engine_provider_registry)]
Conversations = Annotated[ConversationService, Depends(conversation_service)]
ConversationTurns = Annotated[
    ConversationTurnService, Depends(conversation_turn_service)
]
Teams = Annotated[TeamService, Depends(team_service)]
TeamConversations = Annotated[TeamConversationService, Depends(team_conversation_service)]
TeamTaskExecutors = Annotated[TeamTaskExecutionService, Depends(team_task_execution_service)]
TeamTurns = Annotated[TeamTurnService, Depends(team_turn_service)]
Orchestrations = Annotated[OrchestrationService, Depends(orchestration_service)]
Artifacts = Annotated[ArtifactRepository, Depends(artifact_repository)]
ArtifactServices = Annotated[ArtifactService, Depends(artifact_service)]
WorkerPools = Annotated[WorkerPoolRegistry, Depends(worker_pool_registry)]
ModelRoutes = Annotated[ModelRouteService, Depends(model_route_service)]
ModelManagement = Annotated[ModelManagementService, Depends(model_management_service)]
RuntimeDefaults = Annotated[RuntimeDefaultsService, Depends(runtime_defaults_service)]
ResourceCenter = Annotated[ResourceCenterService, Depends(resource_center_service)]
SkillFiles = Annotated[SkillFilePackageService, Depends(skill_file_package_service)]
ToolApprovals = Annotated[ToolApprovalService, Depends(tool_approval_service)]


def _check_tenant(identity: IdentityContext, request: TenantAwareRequest) -> None:
    """请求体的租户声明只能确认身份上下文，不能扩大服务端授权范围。"""

    if request.tenant_id is not None and request.tenant_id != identity.tenant_id:
        raise DomainError(
            "AUTHORIZATION_DENIED",
            "请求租户与认证上下文不一致",
            {"tenant_id": identity.tenant_id},
        )
