"""对象存储、媒体附件与保留策略的统一端口。"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agent_platform.domain.common import generate_database_id
from agent_platform.ports.workspaces import SecretProvider


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
    expected_sha256: str | None = None
    retention_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("platform_id", "tenant_id", "filename", "mime_type"):
            value = _text(getattr(self, name), name)
            if name in {"platform_id", "tenant_id"} and any(char in value for char in ("/", "\\", ":")):
                raise ValueError(f"{name} contains a path separator")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if self.expected_sha256 is not None:
            digest = self.expected_sha256.lower()
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError("expected_sha256 must be a 64-character SHA-256 digest")
            object.__setattr__(self, "expected_sha256", digest)
        if self.retention_expires_at is not None and self.retention_expires_at.tzinfo is None:
            raise ValueError("retention_expires_at must include a timezone")
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
    encryption_key_version: str | None = None
    retention_expires_at: datetime | None = None

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

    def read_asset(
        self, asset_id: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024
    ) -> bytes:
        """通过当前租户 MediaAsset ID 受控读取对象字节。"""
        ...

    def get(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        ...

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        ...

    def collect_garbage(self) -> int:
        ...


class MediaAssetRepository(Protocol):
    """MediaAsset、上传 intent 和回收队列的租户范围持久化端口。"""

    def create_upload_intent(self, upload_id: str, object_ref: str, request: UploadRequest, *, encryption_key_version: str | None) -> None: ...

    def get_upload_intent(self, upload_id: str) -> tuple[UploadRequest, str, str | None] | None: ...

    def publish_upload(self, upload_id: str, asset: MediaAsset) -> None: ...

    def get_asset(self, object_ref: str, platform_id: str, tenant_id: str) -> MediaAsset | None: ...

    def get_asset_by_id(self, asset_id: str, platform_id: str, tenant_id: str) -> MediaAsset | None: ...

    def mark_deleted_and_enqueue(self, object_ref: str, platform_id: str, tenant_id: str) -> None: ...

    def claim_gc_jobs(self, *, limit: int = 100) -> tuple[tuple[str, str, str], ...]: ...

    def is_object_protected(self, platform_id: str, tenant_id: str, object_ref: str) -> bool: ...

    def finish_gc(self, platform_id: str, tenant_id: str, object_ref: str) -> None: ...

    def retry_gc(self, platform_id: str, tenant_id: str, object_ref: str, error_code: str) -> None: ...


class LocalObjectStore:
    """开发环境本地存储，使用服务端生成键并严格校验租户边界。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_upload_bytes: int = 50 * 1024 * 1024,
        metadata_repository: MediaAssetRepository | None = None,
        require_encryption: bool = False,
        encryption_secret_ref: SecretRef | None = None,
        encryption_key_version: str | None = None,
        key_provider: SecretProvider | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._root.chmod(0o700)
        self._max_upload_bytes = max_upload_bytes
        self._metadata_repository = metadata_repository
        self._require_encryption = require_encryption
        self._encryption_secret_ref = encryption_secret_ref
        self._encryption_key_version = encryption_key_version
        self._key_provider = key_provider
        self._intents: dict[str, tuple[UploadRequest, str, str | None]] = {}
        self._assets: dict[str, MediaAsset] = {}
        self._deleted: set[str] = set()

    @property
    def encryption_enabled(self) -> bool:
        """仅当对象加密、版本和 SecretProvider 都已配置时才视为可加密。"""

        return bool(
            self._require_encryption
            and self._encryption_secret_ref is not None
            and self._encryption_key_version
            and self._key_provider is not None
        )

    def _path(self, object_ref: str) -> Path:
        if not object_ref.startswith("object:"):
            raise ObjectStoreError("invalid object reference")
        parts = object_ref.split(":")
        if (
            len(parts) != 4
            or not parts[1]
            or not parts[2]
            or not parts[3]
            or any(char not in "abcdefghijklmnopqrstuvwxyz234567" for char in parts[3])
        ):
            raise ObjectStoreError("invalid object reference")
        # 平台和租户只用于授权、审计；磁盘路径只使用独立随机 key。
        return self._root / parts[3]

    def create_upload_intent(self, request: UploadRequest) -> UploadIntent:
        if request.size_bytes > self._max_upload_bytes:
            raise ObjectStoreError("upload exceeds configured size limit")
        if self._require_encryption:
            self._resolve_key(self._encryption_key_version)
        upload_id = generate_database_id()
        object_ref = f"object:{request.platform_id}:{request.tenant_id}:{generate_database_id()}"
        key_version = self._encryption_key_version if self._require_encryption else None
        if self._metadata_repository is not None:
            self._metadata_repository.create_upload_intent(
                upload_id, object_ref, request, encryption_key_version=key_version
            )
        self._intents[upload_id] = (request, object_ref, key_version)
        return UploadIntent(upload_id, object_ref, f"upload://{upload_id}", 600, self._max_upload_bytes)

    def complete_upload(self, upload_id: str, content: bytes) -> MediaAsset:
        intent = self._intents.get(upload_id)
        if intent is None and self._metadata_repository is not None:
            persisted = self._metadata_repository.get_upload_intent(upload_id)
            if persisted is not None:
                intent = persisted
        if intent is None:
            raise ObjectStoreError("upload intent not found or already completed")
        request, object_ref, key_version = intent
        if len(content) > self._max_upload_bytes or len(content) != request.size_bytes:
            raise ObjectStoreError("uploaded content size does not match intent")
        content_digest = hashlib.sha256(content).hexdigest()
        if request.expected_sha256 is not None and content_digest != request.expected_sha256:
            raise ObjectStoreError("uploaded content digest does not match intent")
        stored_content = self._encrypt(
            content,
            object_ref=object_ref,
            platform_id=request.platform_id,
            tenant_id=request.tenant_id,
            key_version=key_version,
        )
        path = self._path(object_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(stored_content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        asset = MediaAsset(
            generate_database_id(),
            object_ref,
            request.platform_id,
            request.tenant_id,
            request.filename,
            request.mime_type,
            len(content),
            content_digest,
            request.retention_policy,
            encryption_key_version=key_version,
            retention_expires_at=request.retention_expires_at,
        )
        if self._metadata_repository is not None:
            self._metadata_repository.publish_upload(upload_id, asset)
        else:
            self._assets[object_ref] = asset
        self._intents.pop(upload_id, None)
        return asset

    def read(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        asset = (
            self._metadata_repository.get_asset(object_ref, platform_id, tenant_id)
            if self._metadata_repository is not None
            else self._assets.get(object_ref)
        )
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id or object_ref in self._deleted or asset.status is MediaAssetStatus.DELETED:
            raise PermissionError("object is outside the authorized scope")
        if asset.size_bytes > max_bytes:
            raise ObjectStoreError("read exceeds configured bound")
        path = self._path(object_ref)
        try:
            if path.stat().st_size > max_bytes + 512:
                raise ObjectStoreError("stored object exceeds configured bound")
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise PermissionError("object is outside the authorized scope") from error
        content = self._decrypt(
            content,
            object_ref=object_ref,
            platform_id=platform_id,
            tenant_id=tenant_id,
            key_version=asset.encryption_key_version,
        )
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise ObjectStoreError("stored object digest does not match metadata")
        return content

    def get(self, object_ref: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        """兼容文档中的 get 命名，仍复用受控读取逻辑。"""

        return self.read(object_ref, platform_id, tenant_id, max_bytes=max_bytes)

    def read_asset(
        self,
        asset_id: str,
        platform_id: str,
        tenant_id: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> bytes:
        """按租户授权的 MediaAsset ID 读取文件，调用方无需持有可枚举 object_ref。"""

        asset = (
            self._metadata_repository.get_asset_by_id(asset_id, platform_id, tenant_id)
            if self._metadata_repository is not None
            else next(
                (
                    item
                    for item in self._assets.values()
                    if item.asset_id == asset_id
                    and item.platform_id == platform_id
                    and item.tenant_id == tenant_id
                ),
                None,
            )
        )
        if asset is None:
            raise PermissionError("asset is outside the authorized scope")
        return self.read(asset.object_ref, platform_id, tenant_id, max_bytes=max_bytes)

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        asset = (
            self._metadata_repository.get_asset(object_ref, platform_id, tenant_id)
            if self._metadata_repository is not None
            else self._assets.get(object_ref)
        )
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id:
            raise PermissionError("object is outside the authorized scope")
        if self._metadata_repository is not None:
            self._metadata_repository.mark_deleted_and_enqueue(object_ref, platform_id, tenant_id)
        else:
            self._deleted.add(object_ref)

    def collect_garbage(self) -> int:
        if self._metadata_repository is not None:
            count = 0
            for platform_id, tenant_id, object_ref in self._metadata_repository.claim_gc_jobs():
                try:
                    if self._metadata_repository.is_object_protected(platform_id, tenant_id, object_ref):
                        self._metadata_repository.retry_gc(platform_id, tenant_id, object_ref, "OBJECT_REFERENCED")
                        continue
                    self._path(object_ref).unlink(missing_ok=True)
                    self._metadata_repository.finish_gc(platform_id, tenant_id, object_ref)
                    count += 1
                except OSError:
                    self._metadata_repository.retry_gc(platform_id, tenant_id, object_ref, "LOCAL_UNLINK_FAILED")
            return count
        count = 0
        for object_ref in tuple(self._deleted):
            path = self._path(object_ref)
            path.unlink(missing_ok=True)
            self._assets.pop(object_ref, None)
            self._deleted.remove(object_ref)
            count += 1
        return count

    def _resolve_key(self, key_version: str | None) -> bytes:
        if (
            key_version is None
            or self._encryption_secret_ref is None
            or self._key_provider is None
        ):
            raise ObjectStoreError("required encryption key is unavailable")
        try:
            versioned_ref = SecretRef(f"{self._encryption_secret_ref.value}@{key_version}")
            resolved = self._key_provider.resolve(versioned_ref.value, ttl_seconds=300)
            key = base64.b64decode(resolved["key_base64"], validate=True)
        except Exception as error:
            raise ObjectStoreError("required encryption key is unavailable") from error
        if len(key) not in {16, 24, 32}:
            raise ObjectStoreError("encryption key must be 128, 192 or 256 bits")
        return key

    @staticmethod
    def _associated_data(object_ref: str, platform_id: str, tenant_id: str, key_version: str) -> bytes:
        return f"{platform_id}\0{tenant_id}\0{object_ref}\0{key_version}".encode()

    def _encrypt(
        self,
        content: bytes,
        *,
        object_ref: str,
        platform_id: str,
        tenant_id: str,
        key_version: str | None,
    ) -> bytes:
        if key_version is None:
            return content
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        version = key_version.encode("utf-8")
        if not version or len(version) > 255:
            raise ObjectStoreError("invalid encryption key version")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._resolve_key(key_version)).encrypt(
            nonce,
            content,
            self._associated_data(object_ref, platform_id, tenant_id, key_version),
        )
        return b"AGENTOBJ1" + bytes([len(version)]) + version + nonce + ciphertext

    def _decrypt(
        self,
        stored: bytes,
        *,
        object_ref: str,
        platform_id: str,
        tenant_id: str,
        key_version: str | None,
    ) -> bytes:
        if key_version is None:
            if stored.startswith(b"AGENTOBJ1"):
                raise ObjectStoreError("encrypted object metadata is incomplete")
            return stored
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not stored.startswith(b"AGENTOBJ1") or len(stored) < 9 + 1 + 12 + 16:
            raise ObjectStoreError("encrypted object header is invalid")
        version_length = stored[9]
        version_end = 10 + version_length
        if version_end + 12 + 16 > len(stored):
            raise ObjectStoreError("encrypted object header is invalid")
        stored_version = stored[10:version_end].decode("utf-8")
        if stored_version != key_version:
            raise ObjectStoreError("encryption key version does not match metadata")
        nonce = stored[version_end : version_end + 12]
        ciphertext = stored[version_end + 12 :]
        try:
            return AESGCM(self._resolve_key(key_version)).decrypt(
                nonce,
                ciphertext,
                self._associated_data(object_ref, platform_id, tenant_id, key_version),
            )
        except Exception as error:
            raise ObjectStoreError("encrypted object authentication failed") from error


def build_object_store(profile: StorageProfile, *, local_root: str | Path | None = None) -> ObjectStore:
    """按显式 provider 构造适配器；外部后端未接入时明确失败，不静默回退本地。"""

    if StorageProvider(profile.provider) is StorageProvider.LOCAL:
        return LocalObjectStore(local_root or profile.local_root or ".artifacts/object-store")
    raise NotImplementedError(f"storage provider adapter is not installed: {profile.provider}")
