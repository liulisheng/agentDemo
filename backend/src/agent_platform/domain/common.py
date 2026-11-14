"""控制面共享的标识、请求元数据和领域错误。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .identity import IdentityContext


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """带完整租户边界的资源引用，避免服务层只传裸资源 ID。"""

    resource_type: str
    resource_id: str
    platform_id: str
    tenant_id: str

    def __post_init__(self) -> None:
        for name in ("resource_type", "resource_id", "platform_id", "tenant_id"):
            _require_text(getattr(self, name), name)

    def same_scope(self, identity: IdentityContext) -> bool:
        """只有平台和租户同时匹配才允许继续解析资源。"""

        return (
            self.platform_id == identity.platform_id
            and self.tenant_id == identity.tenant_id
        )


@dataclass(frozen=True, slots=True)
class RequestMetadata:
    """把请求关联 ID 与幂等键放在业务服务可复用的结构中。"""

    request_id: str
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        if self.idempotency_key is not None:
            _require_text(self.idempotency_key, "idempotency_key")


class DomainError(Exception):
    """可安全映射到 API 的稳定领域错误，不携带敏感原始 payload。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = _require_text(code, "code")
        self.message = _require_text(message, "message")
        self.details = dict(details or {})
        super().__init__(self.message)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }
