"""租户范围模型连接、模型目录、同步任务和可用路由 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from agent_platform.api.schemas import (
    ModelCatalogItemCreateRequest,
    ModelConnectionCreateRequest,
    ModelConnectionUpdateRequest,
    ModelTestCreateRequest,
)

from .common import Identity, ModelManagement, _check_tenant

router = APIRouter()


@router.get("/model-connections")
def list_model_connections(identity: Identity, service: ModelManagement) -> dict[str, object]:
    """返回当前租户可读的模型连接和凭据引用元数据。"""

    return {"items": [item.to_public_dict() for item in service.list_connections(identity)]}


@router.post("/model-connections", status_code=status.HTTP_201_CREATED)
def create_model_connection(
    request: ModelConnectionCreateRequest,
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    """创建租户内共享给多个模型目录项的连接。"""

    _check_tenant(identity, request)
    return service.create_connection(
        identity,
        name=request.name,
        provider_kind=request.provider_kind,
        protocol=request.protocol,
        endpoint=request.endpoint,
        secret_id=request.secret_id,
        catalog_mode=request.catalog_mode,
    ).to_public_dict()


@router.get("/model-connections/{connection_id}")
def get_model_connection(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    return service.get_connection(identity, connection_id).to_public_dict()


@router.put("/model-connections/{connection_id}")
def update_model_connection(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    request: ModelConnectionUpdateRequest,
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    _check_tenant(identity, request)
    updates = request.model_dump(exclude_unset=True)
    updates.pop("tenant_id", None)
    return service.update_connection(identity, connection_id, updates).to_public_dict()


@router.get("/model-connections/{connection_id}/models")
def list_connection_models(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    return {
        "items": [
            item.to_public_dict()
            for item in service.list_models(identity, connection_id)
        ]
    }


@router.post("/model-connections/{connection_id}/models", status_code=status.HTTP_201_CREATED)
def add_manual_model(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    request: ModelCatalogItemCreateRequest,
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    _check_tenant(identity, request)
    return service.add_manual_model(
        identity,
        connection_id,
        model_id=request.model_id,
        display_name=request.display_name,
        capabilities=request.capabilities,
        context_window=request.context_window,
    ).to_public_dict()


@router.post("/model-connections/{connection_id}/sync-models", status_code=status.HTTP_202_ACCEPTED)
def sync_connection_models(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    """将目录发现交给 Worker，API 进程不解析 SecretRef 或访问模型端点。"""

    record = service.queue_catalog_sync(identity, connection_id)
    return {"test_id": record.test_id, "state": record.state.value}


@router.post("/model-connections/{connection_id}/tests", status_code=status.HTTP_202_ACCEPTED)
def test_model_connection(
    connection_id: Annotated[str, Path(min_length=1, max_length=64)],
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    """排入不产生模型输出的连接认证与目录可达性测试。"""

    record = service.queue_connection_test(identity, connection_id)
    return {"test_id": record.test_id, "state": record.state.value}


@router.post("/model-routes/{route_id}/tests", status_code=status.HTTP_202_ACCEPTED)
def test_model_route(
    route_id: Annotated[str, Path(min_length=1, max_length=64)],
    request: ModelTestCreateRequest,
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    """只有显式确认可能产生费用后才排入具体模型推理测试。"""

    _check_tenant(identity, request)
    record = service.queue_model_test(
        identity, route_id, confirmed_cost=request.confirmed_cost
    )
    return {"test_id": record.test_id, "state": record.state.value}


@router.get("/model-tests/{test_id}")
def get_model_test(
    test_id: Annotated[str, Path(min_length=1, max_length=26)],
    identity: Identity,
    service: ModelManagement,
) -> dict[str, object]:
    return service.get_test_record(identity, test_id).to_public_dict()


@router.get("/model-routes/selectable")
def list_selectable_model_routes(
    identity: Identity,
    service: ModelManagement,
    provider: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, object]:
    items = service.selectable_routes(identity)
    if provider is not None:
        items = tuple(route for route in items if route.provider == provider)
    source = service.model_authorization.authorization_source
    return {
        "items": [
            {**route.to_public_dict(), "authorization_source": source}
            for route in items
        ]
    }
