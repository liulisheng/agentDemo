"""按业务路由组装 `/api/v1` FastAPI 路由，不承载业务 handler。"""

from fastapi import APIRouter

from .route_modules import (
    catalog,
    conversations,
    deployments,
    model_management,
    resource_center,
    skill_files,
    tool_approvals,
)

router = APIRouter(prefix="/api/v1")
for route_group in (
    model_management.router,
    catalog.router,
    deployments.router,
    resource_center.router,
    skill_files.router,
    conversations.router,
    tool_approvals.router,
):
    router.include_router(route_group)
