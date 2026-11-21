"""API 路由按业务功能拆分后的注册与路径契约验收。"""

from agent_platform.api.app import create_app
from agent_platform.api.route_modules import (
    catalog,
    conversations,
    deployments,
    resource_center,
    skill_files,
)
from agent_platform.api.routes import router


def _paths(group: object) -> set[str]:
    return {route.path for route in group.routes}


def test_business_route_groups_own_their_endpoint_families() -> None:
    assert "/digital-employees" in _paths(catalog.router)
    assert "/skills" in _paths(catalog.router)
    assert "/tools" in _paths(catalog.router)
    assert "/deployments" in _paths(deployments.router)
    assert "/sessions/{session_id}/runs" in _paths(deployments.router)
    assert "/runs/{run_id}/cancel" in _paths(deployments.router)
    assert "/hub/resources" in _paths(resource_center.router)
    assert "/hub/imports" in _paths(resource_center.router)
    assert "/skills/{skill_id}/versions/{version}/files/zip/preview" in _paths(skill_files.router)
    assert "/skills/{skill_id}/versions/{version}/files" in _paths(skill_files.router)
    assert "/conversations" in _paths(conversations.router)
    assert "/conversations/{conversation_id}/turns" in _paths(conversations.router)


def test_aggregate_router_preserves_api_prefix_and_registers_each_group_once() -> None:
    assert router.prefix == "/api/v1"
    paths = create_app(mode="fake").openapi()["paths"]
    assert "/api/v1/digital-employees" in paths
    assert "/api/v1/hub/resources" in paths
    assert "/api/v1/skills/{skill_id}/versions/{version}/files" in paths
    assert "get" in paths["/api/v1/conversations"]
