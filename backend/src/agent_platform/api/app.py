from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.api.routes import router
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.domain.common import DomainError


def create_app() -> FastAPI:
    """创建控制面应用并注入一组隔离的内存服务。"""

    app = FastAPI(title="Agent Platform API", version="0.1.0")
    catalog = CatalogService(InMemoryCatalogRepository())
    app.state.catalog_service = catalog
    app.state.control_plane_service = ControlPlaneService(catalog, FakeRuntimeAdapter())
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
        status_by_code = {
            "RESOURCE_NOT_FOUND": 404,
            "AUTHORIZATION_DENIED": 403,
            "DEPENDENCY_VALIDATION_ERROR": 409,
            "IMMUTABLE_VERSION": 409,
            "IDEMPOTENCY_CONFLICT": 409,
            "INVALID_STATE": 409,
            "INVALID_ARGUMENT": 422,
        }
        request_id = request.headers.get("X-Request-ID", f"req-{uuid4().hex}")
        return JSONResponse(
            status_code=status_by_code.get(error.code, 400),
            content={
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
                "details": error.details,
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
