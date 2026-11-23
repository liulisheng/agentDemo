"""暴露当前服务已注册的 EngineProvider 能力，不返回执行命令或凭据。"""

from fastapi import APIRouter

from .common import EngineProviders, Identity

router = APIRouter()


@router.get("/engine-providers")
def list_engine_providers(identity: Identity, registry: EngineProviders) -> dict[str, object]:
    """返回当前身份可查看的引擎选择项；演示模式明确标记 SIMULATION。"""

    del identity  # 该清单由服务部署注册，身份只负责 API 认证，不扩大租户资源范围。
    return {"items": list(registry.available())}
