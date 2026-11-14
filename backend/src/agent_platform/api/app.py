from fastapi import FastAPI

from agent_platform.api.routes import router


def create_app() -> FastAPI:
    """创建平台控制面应用并注册第一批 API 路由。"""

    app = FastAPI(title="Agent Platform API", version="0.1.0")
    app.include_router(router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
