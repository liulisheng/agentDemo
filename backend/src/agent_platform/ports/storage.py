"""对象存储、媒体附件与保留策略的统一端口。"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value.strip()


class ObjectStoreError(Exception):
    """对象不存在、大小超限或状态非法时抛出的稳定错误。"""


class MediaAssetStatus(StrEnum):
    """媒体对象生命周期，便于扫描、处理和回收任务观测。"""

    INIT = "INIT"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    SCANNING = "SCANNING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    DELETED = "DELETED"


class StorageProvider(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    MINIO = "minio"
    OSS = "oss"
    COS = "cos"


@dataclass(frozen=True, slots=True)
class SecretRef:
    """仅保存凭据引用，不保存 API Key 或 Secret Key。"""

    value: str

    def __post_init__(self) -> None:
        _text(self.value, "secret_ref")


@dataclass(frozen=True, slots=True)
class StorageProfile:
    """显式选择对象存储提供方及其非敏感配置。"""

    profile_id: str
    provider: StorageProvider | str
    bucket: str
    endpoint: str | None = None
    region: str | None = None
    base_prefix: str = ""
    credential_ref: SecretRef | None = None
    local_root: str | None = None
    signed_url_ttl_seconds: int = 600
    encryption_policy: str = "provider-managed"
    retention_policy: str = "conversation"

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        _text(self.bucket, "bucket")
        try:
            provider = StorageProvider(self.provider)
        except ValueError as exc:
            raise ValueError(f"unsupported storage provider: {self.provider}") from exc
        if isinstance(self.credential_ref, str):
            raise ValueError("credential_ref must be SecretRef")  # noqa: TRY004
        if self.signed_url_ttl_seconds <= 0 or self.signed_url_ttl_seconds > 3600:
            raise ValueError("signed URL TTL must be between 1 and 3600 seconds")
        if provider is StorageProvider.LOCAL and not self.local_root:
            raise ValueError("local provider requires local_root")
        if provider is not StorageProvider.LOCAL and not self.endpoint:
            raise ValueError("external provider requires endpoint")


@dataclass(frozen=True, slots=True)
class UploadRequest:
    platform_id: str
    tenant_id: str
    filename: str
    mime_type: str
    size_bytes: int
    retention_policy: str = "conversation"

    def __post_init__(self) -> None:
        for name in ("platform_id", "tenant_id", "filename", "mime_type"):
            value = _text(getattr(self, name), name)
            if name in {"platform_id", "tenant_id"} and any(char in value for char in ("/", "\\", ":")):
                raise ValueError(f"{name} contains a path separator")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        RetentionPolicy(self.retention_policy)


@dataclass(frozen=True, slots=True)
class UploadIntent:
    upload_id: str
    object_ref: str
    upload_url: str
    expires_in_seconds: int
    max_size_bytes: int


@dataclass(frozen=True, slots=True)
class MediaAsset:
    asset_id: str
    object_ref: str
    platform_id: str
    tenant_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    retention_policy: RetentionPolicy | str
    status: MediaAssetStatus | str = MediaAssetStatus.UPLOADED

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.object_ref, "object_ref")
        _text(self.platform_id, "platform_id")
        _text(self.tenant_id, "tenant_id")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        object.__setattr__(self, "retention_policy", RetentionPolicy(self.retention_policy))
        object.__setattr__(self, "status", MediaAssetStatus(self.status))


class RetentionPolicy(StrEnum):
    EPHEMERAL = "ephemeral"
    SESSION = "session"
    CONVERSATION = "conversation"
    KNOWLEDGE_BASE = "knowledge_base"


@dataclass(frozen=True, slots=True)
class MessageAttachment:
    """消息只保存平台 MediaAsset 引用和租户保留策略。"""

    attachment_id: str
    media_asset_ref: str
    tenant_id: str
    retention_policy: RetentionPolicy | str = RetentionPolicy.CONVERSATION

    def __post_init__(self) -> None:
        for name in ("attachment_id", "media_asset_ref", "tenant_id"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "retention_policy", RetentionPolicy(self.retention_policy))


@dataclass(frozen=True, slots=True)
class RuntimeAttachmentRef:
    """Runtime 可用的执行副本引用，不替代平台 MediaAsset。"""

    media_asset_ref: str
    workspace_path: str
    runtime_name: str = "generic"
    tenant_id: str = ""

    @classmethod
    def from_attachment(cls, attachment: MessageAttachment, workspace_path: str, runtime_name: str = "generic") -> RuntimeAttachmentRef:
        _text(workspace_path, "workspace_path")
        if workspace_path.startswith(("/", "\\")) or ".." in workspace_path.replace("\\", "/").split("/"):
            raise ValueError("workspace_path must stay inside the Runtime workspace")
        return cls(attachment.media_asset_ref, workspace_path, runtime_name, attachment.tenant_id)

    def authorize_for(self, tenant_id: str) -> bool:
        return self.tenant_id == tenant_id


@dataclass(frozen=True, slots=True)
class StorageBinding:
    """将 StorageProfile 绑定到 platform、tenant 或 project 作用域。"""

    scope_type: str
    storage_profile_id: str
    platform_id: str | None = None
    tenant_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.scope_type not in {"PLATFORM", "TENANT", "PROJECT"}:
            raise ValueError("scope_type must be PLATFORM, TENANT or PROJECT")
        _text(self.storage_profile_id, "storage_profile_id")


def select_storage_profile(bindings: tuple[StorageBinding, ...], *, platform_id: str, tenant_id: str, project_id: str | None = None) -> str | None:
    """按 project > tenant > platform 优先级解析配置。"""

    for scope, value in (("PROJECT", project_id), ("TENANT", tenant_id), ("PLATFORM", platform_id)):
        if value is None:
            continue
        for binding in bindings:
            if binding.scope_type != scope:
                continue
            if scope == "PROJECT" and binding.project_id == value and binding.tenant_id == tenant_id:
                return binding.storage_profile_id
            if scope == "TENANT" and binding.tenant_id == value and binding.platform_id == platform_id:
                return binding.storage_profile_id
            if scope == "PLATFORM" and binding.platform_id == value:
                return binding.storage_profile_id
    return None


class ObjectStore(Protocol):
    """上传、受控读取、删除和垃圾回收端口。"""

    def create_upload_intent(self, request: UploadRequest) -> UploadIntent:
        ...

    def complete_upload(self, upload_id: str, content: bytes) -> MediaAsset:
        ...

    def read(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        ...

    def get(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        ...

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        ...

    def collect_garbage(self) -> int:
        ...


class LocalObjectStore:
    """开发环境本地存储，使用服务端生成键并严格校验租户边界。"""

    def __init__(self, root: str | Path, *, max_upload_bytes: int = 50 * 1024 * 1024) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_upload_bytes = max_upload_bytes
        self._intents: dict[str, tuple[UploadRequest, str]] = {}
        self._assets: dict[str, MediaAsset] = {}
        self._deleted: set[str] = set()

    def _path(self, object_ref: str) -> Path:
        if not object_ref.startswith("object:"):
            raise ObjectStoreError("invalid object reference")
        parts = object_ref.split(":")
        if len(parts) != 4:
            raise ObjectStoreError("invalid object reference")
        path = (self._root / parts[1] / parts[2] / parts[3]).resolve()
        if self._root not in path.parents:
            raise ObjectStoreError("object path escapes storage root")
        return path

    def create_upload_intent(self, request: UploadRequest) -> UploadIntent:
        if request.size_bytes > self._max_upload_bytes:
            raise ObjectStoreError("upload exceeds configured size limit")
        upload_id = f"upload:{secrets.token_hex(12)}"
        object_ref = f"object:{request.platform_id}:{request.tenant_id}:{secrets.token_hex(16)}"
        self._intents[upload_id] = (request, object_ref)
        return UploadIntent(upload_id, object_ref, f"upload://{upload_id}", 600, self._max_upload_bytes)

    def complete_upload(self, upload_id: str, content: bytes) -> MediaAsset:
        intent = self._intents.pop(upload_id, None)
        if intent is None:
            raise ObjectStoreError("upload intent not found or already completed")
        request, object_ref = intent
        if len(content) > self._max_upload_bytes or len(content) != request.size_bytes:
            raise ObjectStoreError("uploaded content size does not match intent")
        path = self._path(object_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        asset = MediaAsset(f"asset:{secrets.token_hex(12)}", object_ref, request.platform_id, request.tenant_id, request.filename, request.mime_type, len(content), hashlib.sha256(content).hexdigest(), request.retention_policy)
        self._assets[object_ref] = asset
        return asset

    def read(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        asset = self._assets.get(object_ref)
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id or object_ref in self._deleted:
            raise PermissionError("object is outside the authorized scope")
        if asset.size_bytes > max_bytes:
            raise ObjectStoreError("read exceeds configured bound")
        return self._path(object_ref).read_bytes()

    def get(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        """兼容文档中的 get 命名，仍复用受控读取逻辑。"""

        return self.read(object_ref, platform_id, tenant_id, max_bytes=max_bytes)

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        asset = self._assets.get(object_ref)
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id:
            raise PermissionError("object is outside the authorized scope")
        self._deleted.add(object_ref)

    def collect_garbage(self) -> int:
        count = 0
        for object_ref in tuple(self._deleted):
            path = self._path(object_ref)
            path.unlink(missing_ok=True)
            self._assets.pop(object_ref, None)
            self._deleted.remove(object_ref)
            count += 1
        return count


def build_object_store(profile: StorageProfile, *, local_root: str | Path | None = None) -> ObjectStore:
    """按显式 provider 构造适配器；外部后端未接入时明确失败，不静默回退本地。"""

    if StorageProvider(profile.provider) is StorageProvider.LOCAL:
        return LocalObjectStore(local_root or profile.local_root or ".artifacts/object-store")
    raise NotImplementedError(f"storage provider adapter is not installed: {profile.provider}")
