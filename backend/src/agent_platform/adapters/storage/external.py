"""外部对象存储适配器契约；真正 SDK 接入由部署实现。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_platform.ports.storage import StorageProfile, StorageProvider


@dataclass(frozen=True, slots=True)
class ExternalObjectStoreContract:
    """S3、MinIO、OSS、COS 共用的配置契约，不接触明文凭据。"""

    profile: StorageProfile

    def __post_init__(self) -> None:
        if StorageProvider(self.profile.provider) is StorageProvider.LOCAL:
            raise ValueError("external contract requires a non-local provider")

    def signed_url_ttl(self, requested_seconds: int) -> int:
        if requested_seconds <= 0 or requested_seconds > self.profile.signed_url_ttl_seconds:
            raise ValueError("requested signed URL TTL exceeds profile policy")
        return requested_seconds


class S3ObjectStore(ExternalObjectStoreContract):
    """通用 S3 兼容服务的端口契约。"""


class MinioObjectStore(ExternalObjectStoreContract):
    """MinIO 服务的端口契约。"""


class OssObjectStore(ExternalObjectStoreContract):
    """阿里云 OSS 服务的端口契约。"""


class CosObjectStore(ExternalObjectStoreContract):
    """腾讯云 COS 服务的端口契约。"""


__all__ = ["CosObjectStore", "ExternalObjectStoreContract", "MinioObjectStore", "OssObjectStore", "S3ObjectStore"]
