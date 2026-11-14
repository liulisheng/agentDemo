from typing import Annotated

from fastapi import Header, HTTPException, status

from agent_platform.adapters.identity.fake import FakeIdentityProvider
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
