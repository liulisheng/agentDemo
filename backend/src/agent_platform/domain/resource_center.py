"""资源中心资源包的规范化清单和摘要计算。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Any

_RESOURCE_TYPES = frozenset({"DIGITAL_EMPLOYEE", "SKILL", "TOOL"})
_SECRET_KEYS = frozenset(
    {
        "auth_secret_ref",
        "secret_ref",
        "secret_id",
        "secret",
        "secret_value",
        "api_key",
        "api_secret",
        "password",
        "credential",
        "credentials",
        "access_token",
        "refresh_token",
    }
)
_KEY_SEPARATOR = re.compile(r"[-\s]+")


class HubResourceType(StrEnum):
    """首期可在平台资源中心共享的资源类型。"""

    DIGITAL_EMPLOYEE = "DIGITAL_EMPLOYEE"
    SKILL = "SKILL"
    TOOL = "TOOL"


class HubVisibility(StrEnum):
    """目录元数据的发现范围，与资源包导入授权分开控制。"""

    PLATFORM = "PLATFORM"
    RESTRICTED = "RESTRICTED"


class HubResourceStatus(StrEnum):
    """资源中心主记录状态。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    REVOKED = "REVOKED"


class HubVersionStatus(StrEnum):
    """不可变 Hub 资源版本状态。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


class HubImportMode(StrEnum):
    """租户导入 Hub 资源的复制/只读引用语义。"""

    COPY = "COPY"
    REFERENCE = "REFERENCE"


class HubGrantStatus(StrEnum):
    """指定资源版本与接收租户之间的授权状态。"""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class TenantImportStatus(StrEnum):
    """租户 COPY/REFERENCE 物化和授权状态。"""

    IMPORTING = "IMPORTING"
    ACTIVE = "ACTIVE"
    OUTDATED = "OUTDATED"
    REVOKED = "REVOKED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class HubResource:
    """平台资源中心条目；平台范围 ID 不替代发布租户的资源 ID。"""

    platform_id: str
    hub_resource_id: str
    resource_type: HubResourceType | str
    publisher_tenant_id: str
    publisher_user_id: str
    name: str
    description: str
    visibility: HubVisibility | str = HubVisibility.PLATFORM
    status: HubResourceStatus | str = HubResourceStatus.DRAFT

    def __post_init__(self) -> None:
        for field_name in (
            "platform_id",
            "hub_resource_id",
            "publisher_tenant_id",
            "publisher_user_id",
            "name",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        object.__setattr__(self, "resource_type", HubResourceType(self.resource_type))
        object.__setattr__(self, "visibility", HubVisibility(self.visibility))
        object.__setattr__(self, "status", HubResourceStatus(self.status))


@dataclass(frozen=True, slots=True)
class HubResourceVersion:
    """指向发布方精确资源版本的不可变、脱敏 Package manifest。"""

    platform_id: str
    hub_resource_id: str
    version: int
    publisher_tenant_id: str
    source_resource_type: HubResourceType | str
    source_resource_id: str
    source_resource_version: int
    manifest: HubPackageManifest
    artifact_asset_id: str | None
    created_by: str
    status: HubVersionStatus | str = HubVersionStatus.DRAFT
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "platform_id",
            "hub_resource_id",
            "publisher_tenant_id",
            "source_resource_id",
            "created_by",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.version < 1 or self.source_resource_version < 1:
            raise ValueError("resource versions must be positive")
        object.__setattr__(self, "source_resource_type", HubResourceType(self.source_resource_type))
        object.__setattr__(self, "status", HubVersionStatus(self.status))


@dataclass(frozen=True, slots=True)
class HubGrant:
    """由发布租户授予接收租户的 Hub 精确版本导入权限。"""

    grant_id: str
    platform_id: str
    hub_resource_id: str
    hub_version: int
    publisher_tenant_id: str
    recipient_tenant_id: str
    copy_allowed: bool
    reference_allowed: bool
    created_by: str
    expires_at: datetime | None = None
    status: HubGrantStatus | str = HubGrantStatus.ACTIVE
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "grant_id",
            "platform_id",
            "hub_resource_id",
            "publisher_tenant_id",
            "recipient_tenant_id",
            "created_by",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.hub_version < 1:
            raise ValueError("hub_version must be positive")
        if not self.copy_allowed and not self.reference_allowed:
            raise ValueError("grant must allow COPY, REFERENCE or both")
        object.__setattr__(self, "status", HubGrantStatus(self.status))


@dataclass(frozen=True, slots=True)
class TenantResourceImport:
    """接收租户本地资源代理到固定 Hub 来源版本的映射。"""

    platform_id: str
    tenant_id: str
    import_id: str
    grant_id: str
    hub_resource_id: str
    hub_version: int
    mode: HubImportMode | str
    local_resource_type: HubResourceType | str
    local_resource_id: str
    local_resource_version: int
    manifest_sha256: str
    imported_by: str
    status: TenantImportStatus | str = TenantImportStatus.IMPORTING

    def __post_init__(self) -> None:
        for field_name in (
            "platform_id",
            "tenant_id",
            "import_id",
            "grant_id",
            "hub_resource_id",
            "local_resource_id",
            "manifest_sha256",
            "imported_by",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.hub_version < 1 or self.local_resource_version < 1:
            raise ValueError("resource versions must be positive")
        object.__setattr__(self, "mode", HubImportMode(self.mode))
        object.__setattr__(self, "local_resource_type", HubResourceType(self.local_resource_type))
        object.__setattr__(self, "status", TenantImportStatus(self.status))


@dataclass(frozen=True, slots=True)
class HubResourceSummary:
    """资源中心可发现的脱敏目录卡片；不包含发布租户内部 ID 或 Package 内容。"""

    hub_resource_id: str
    resource_type: HubResourceType
    name: str
    description: str
    publisher_name: str
    visibility: HubVisibility
    latest_version: int
    available_modes: tuple[HubImportMode, ...]
    is_publisher: bool = False


@dataclass(frozen=True, slots=True)
class HubResourceDetail:
    """资源中心版本详情只返回脱敏摘要，不返回发布方租户 ID 或完整 Manifest。"""

    hub_resource_id: str
    resource_type: HubResourceType
    name: str
    description: str
    publisher_name: str
    visibility: HubVisibility
    version: int
    manifest_sha256: str
    dependency_count: int
    available_modes: tuple[HubImportMode, ...]


@dataclass(frozen=True, slots=True)
class HubResourceDependency:
    """资源包对另一个 Hub 不可变资源版本的精确依赖。"""

    dependency_id: str
    dependency_type: HubResourceType | str
    dependency_hub_resource_id: str
    dependency_hub_version: int
    sequence_no: int
    required: bool = True

    def __post_init__(self) -> None:
        for field_name in ("dependency_id", "dependency_hub_resource_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.dependency_hub_version < 1 or self.sequence_no < 1:
            raise ValueError("dependency version and sequence must be positive")
        object.__setattr__(self, "dependency_type", HubResourceType(self.dependency_type))


@dataclass(frozen=True, slots=True)
class HubPackageManifest:
    """用于发布、COPY/REFERENCE 和 RunSnapshot 校验的不可变资源包。"""

    data: dict[str, Any]
    manifest_json: str
    sha256: str


def _normalized_key(value: str) -> str:
    return _KEY_SEPARATOR.sub("_", value.strip().lower())


def _sanitize_content(value: Any, *, schema_node: bool = False) -> tuple[Any, bool]:
    """过滤凭据字段；JSON Schema 中的 password 属性只是输入定义，必须保留。"""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        removed_secret = False
        for raw_key in sorted(value):
            key = str(raw_key)
            normalized = _normalized_key(key)
            child_is_schema = normalized in {"input_schema", "output_schema", "json_schema"}
            if not schema_node and normalized in _SECRET_KEYS:
                secret_value = value[raw_key]
                if secret_value not in (None, "", False):
                    removed_secret = True
                continue
            child, child_removed = _sanitize_content(
                value[raw_key], schema_node=schema_node or child_is_schema
            )
            result[key] = child
            removed_secret = removed_secret or child_removed
        return result, removed_secret
    if isinstance(value, (list, tuple)):
        result_list: list[Any] = []
        removed_secret = False
        for item in value:
            sanitized, child_removed = _sanitize_content(item, schema_node=schema_node)
            result_list.append(sanitized)
            removed_secret = removed_secret or child_removed
        return result_list, removed_secret
    return value, False


def build_hub_manifest(
    *,
    resource_type: str,
    name: str,
    version: int,
    content: dict[str, Any],
    dependencies: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> HubPackageManifest:
    """构造键顺序稳定的 JSON manifest，并排除凭据及 SecretRef。"""

    normalized_type = resource_type.strip().upper()
    if normalized_type not in _RESOURCE_TYPES:
        raise ValueError("resource_type must be DIGITAL_EMPLOYEE, SKILL or TOOL")
    if not name.strip():
        raise ValueError("name must not be blank")
    if version < 1:
        raise ValueError("version must be positive")

    sanitized_content, removed_secret = _sanitize_content(content)
    normalized_dependencies: list[dict[str, Any]] = []
    for dependency in dependencies:
        dependency_type = str(dependency["type"]).strip().upper()
        dependency_id = str(dependency["resource_id"]).strip()
        dependency_version = int(dependency["version"])
        sequence = int(dependency.get("sequence_no", 0))
        if dependency_type not in _RESOURCE_TYPES or not dependency_id or dependency_version < 1:
            raise ValueError("dependency must contain a supported type, resource_id and positive version")
        normalized_dependencies.append(
            {
                "sequence_no": sequence,
                "type": dependency_type,
                "resource_id": dependency_id,
                "version": dependency_version,
                "required": bool(dependency.get("required", True)),
            }
        )
    normalized_dependencies.sort(
        key=lambda item: (
            item["sequence_no"],
            item["type"],
            item["resource_id"],
            item["version"],
        )
    )
    for previous, current in pairwise(normalized_dependencies):
        if previous["sequence_no"] == current["sequence_no"]:
            raise ValueError("dependency sequence_no values must be unique")

    data: dict[str, Any] = {
        "manifest_schema_version": 1,
        "resource": {
            "type": normalized_type,
            "name": name.strip(),
            "version": version,
        },
        "content": sanitized_content,
        "dependencies": normalized_dependencies,
        "requires_recipient_credentials": removed_secret,
    }
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return HubPackageManifest(data, canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest())
