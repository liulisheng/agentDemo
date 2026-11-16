"""数字员工、Skill、Tool 和部署的版本化领域对象。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from .common import DomainError


class LifecycleStatus(StrEnum):
    """控制面资源的最小生命周期。"""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class SkillType(StrEnum):
    """Skill 对 Runtime 暴露的行为类型。"""

    INSTRUCTION = "instruction"
    TOOL_BUNDLE = "tool-bundle"
    WORKFLOW = "workflow"


class SkillContentFormat(StrEnum):
    """Skill 指令以单字段存储，或以文件清单和对象引用存储。"""

    INLINE = "INLINE"
    FILE_PACKAGE = "FILE_PACKAGE"


def normalize_skill_file_path(path: str) -> str:
    """拒绝非规范相对路径，确保对象文件不会逃出 Skill 包目录。"""

    if (
        not isinstance(path, str)
        or not path
        or len(path) > 512
        or path != path.strip()
        or path.startswith("/")
        or path.endswith("/")
        or "\\" in path
        or "\x00" in path
        or re.match(r"^[a-zA-Z]:", path)
    ):
        raise ValueError("Skill file path must be a canonical relative path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Skill file path must not contain traversal or empty segments")
    return unicodedata.normalize("NFC", path)


@dataclass(frozen=True, slots=True)
class SkillFileEntry:
    """Skill 文件清单一行；文件字节保存在租户 MediaAsset 中。"""

    file_path: str
    media_asset_id: str
    mime_type: str
    size_bytes: int
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", normalize_skill_file_path(self.file_path))
        for field_name in ("media_asset_id", "mime_type"):
            _text(getattr(self, field_name), field_name)
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        digest = self.content_sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("content_sha256 must be a 64-character SHA-256 digest")
        object.__setattr__(self, "content_sha256", digest)


def compute_skill_file_manifest_sha256(entries: tuple[SkillFileEntry, ...] | list[SkillFileEntry]) -> str:
    """计算与存储提供商无关的规范 Skill 文件清单摘要。"""

    canonical_entries = [
        {
            "file_path": entry.file_path,
            "mime_type": entry.mime_type,
            "size_bytes": entry.size_bytes,
            "content_sha256": entry.content_sha256,
        }
        for entry in sorted(entries, key=lambda value: value.file_path)
    ]
    serialized = json.dumps(
        {"manifest_schema_version": 1, "files": canonical_entries},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _scope(tenant_id: str) -> str:
    return _text(tenant_id, "tenant_id")


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """可被 Skill 声明并由部署策略授权的工具版本。"""

    tool_id: str
    tenant_id: str
    name: str
    description: str
    input_schema: Mapping[str, object]
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT
    handler_name: str = "echo"
    protocol: str = "builtin"
    requires_recipient_credentials: bool = False
    auth_secret_ref: str | None = None

    def __post_init__(self) -> None:
        _text(self.tool_id, "tool_id")
        _scope(self.tenant_id)
        _text(self.name, "name")
        _text(self.description, "description")
        if self.protocol not in {"builtin", "api", "mcp", "a2a"}:
            raise ValueError("protocol must be builtin, api, mcp or a2a")
        if self.auth_secret_ref is not None:
            _text(self.auth_secret_ref, "auth_secret_ref")
            object.__setattr__(self, "requires_recipient_credentials", True)
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("input_schema must be an object")

    def publish(self) -> ToolVersion:
        self.assert_mutable()
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def deprecate(self) -> ToolVersion:
        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布 Tool 才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或废弃的 Tool 版本不可修改")

    def __repr__(self) -> str:
        """SecretRef 允许持久化但不允许进入普通日志或调试输出。"""

        return (
            f"ToolVersion(tool_id={self.tool_id!r}, tenant_id={self.tenant_id!r}, name={self.name!r}, "
            f"version={self.version!r}, status={self.status!r}, protocol={self.protocol!r}, "
            f"requires_recipient_credentials={self.requires_recipient_credentials!r}, "
            "auth_secret_ref=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class SkillVersion:
    """包含指令、契约和工具声明的可复用 Skill 版本。"""

    skill_id: str
    tenant_id: str
    name: str
    skill_type: SkillType
    instruction: str
    allowed_tool_ids: tuple[str, ...] = ()
    input_schema: Mapping[str, object] = field(default_factory=lambda: {"type": "object"})
    output_schema: Mapping[str, object] = field(default_factory=lambda: {"type": "object"})
    trigger: str | None = None
    workflow_steps: tuple[str, ...] = ()
    memory_policy: str = "none"
    approval_policy: str = "none"
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT
    allowed_tool_versions: tuple[tuple[str, int], ...] = ()
    content_format: SkillContentFormat | str = SkillContentFormat.INLINE
    entry_path: str | None = None
    file_manifest_sha256: str | None = None
    file_entries: tuple[SkillFileEntry, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("skill_id", "name"):
            _text(getattr(self, field_name), field_name)
        _scope(self.tenant_id)
        content_format = SkillContentFormat(self.content_format)
        object.__setattr__(self, "content_format", content_format)
        if content_format is SkillContentFormat.INLINE:
            _text(self.instruction, "instruction")
            if self.entry_path is not None or self.file_manifest_sha256 is not None or self.file_entries:
                raise ValueError("INLINE Skill cannot contain FILE_PACKAGE metadata")
        else:
            is_hub_reference = self.instruction.startswith("hub-reference://")
            if self.instruction and not is_hub_reference:
                raise ValueError("FILE_PACKAGE Skill stores its entry content in the file manifest")
            if self.entry_path != "SKILL.md":
                raise ValueError("FILE_PACKAGE entry_path must be root SKILL.md")
            if not self.file_entries and not is_hub_reference:
                raise ValueError("FILE_PACKAGE Skill requires a file manifest")
            if self.file_manifest_sha256 is None:
                raise ValueError("FILE_PACKAGE requires a file manifest digest")
            if len(self.file_manifest_sha256) != 64 or any(
                char not in "0123456789abcdefABCDEF" for char in self.file_manifest_sha256
            ):
                raise ValueError("file_manifest_sha256 must be a 64-character SHA-256 digest")
            if is_hub_reference and not self.file_entries:
                object.__setattr__(self, "file_manifest_sha256", self.file_manifest_sha256.lower())
            else:
                canonical_paths = [entry.file_path for entry in self.file_entries]
                collision_keys = [unicodedata.normalize("NFKC", path).casefold() for path in canonical_paths]
                if len(collision_keys) != len(set(collision_keys)):
                    raise ValueError("FILE_PACKAGE contains duplicate normalized paths")
                skill_entry_names = [
                    path for path in canonical_paths if path.rsplit("/", 1)[-1].casefold() == "skill.md"
                ]
                if skill_entry_names != ["SKILL.md"]:
                    raise ValueError("FILE_PACKAGE requires root SKILL.md as its only entry file")
                if canonical_paths.count(self.entry_path) != 1:
                    raise ValueError("FILE_PACKAGE requires exactly one root SKILL.md entry")
                expected_digest = compute_skill_file_manifest_sha256(self.file_entries)
                if self.file_manifest_sha256.lower() != expected_digest:
                    raise ValueError("Skill file manifest digest does not match the file entries")
                object.__setattr__(self, "file_manifest_sha256", expected_digest)
                object.__setattr__(self, "file_entries", tuple(sorted(self.file_entries, key=lambda item: item.file_path)))
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.allowed_tool_ids, tuple):
            raise TypeError("allowed_tool_ids must be a tuple")
        if not self.allowed_tool_versions:
            object.__setattr__(
                self,
                "allowed_tool_versions",
                tuple((tool_id, 1) for tool_id in self.allowed_tool_ids),
            )
        if tuple(tool_id for tool_id, _ in self.allowed_tool_versions) != self.allowed_tool_ids:
            raise ValueError("allowed_tool_versions must match allowed_tool_ids order")
        if any(version < 1 for _, version in self.allowed_tool_versions):
            raise ValueError("Tool version references must be positive")

    def publish(self) -> SkillVersion:
        self.assert_mutable()
        if self.skill_type is SkillType.WORKFLOW and not self.workflow_steps:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "workflow Skill 必须包含步骤")
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def deprecate(self) -> SkillVersion:
        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布 Skill 才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或废弃的 Skill 版本不可修改")


@dataclass(frozen=True, slots=True)
class DigitalEmployee:
    """数字员工的稳定身份，版本内容单独保存在 DigitalEmployeeVersion。"""

    employee_id: str
    tenant_id: str
    name: str
    role_name: str
    description: str
    status: LifecycleStatus = LifecycleStatus.DRAFT
    published_to_gallery: bool = False

    def __post_init__(self) -> None:
        for field_name in ("employee_id", "tenant_id", "name", "role_name", "description"):
            _text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class DigitalEmployeePage:
    """租户员工目录的稳定游标分页结果。"""

    items: tuple[DigitalEmployee, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """把引擎和策略组合成可被 Run 固化的配置版本。"""

    profile_id: str
    tenant_id: str
    engine_name: str
    model_alias: str
    tool_policy: tuple[str, ...] = ()
    context_policy: str = "default"
    version: int = 1
    status: LifecycleStatus = LifecycleStatus.DRAFT
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: str = "none"

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "tenant_id", "engine_name", "model_alias"):
            _text(getattr(self, field_name), field_name)
        for field_name in ("model_route_id", "workspace_profile_id", "sandbox_profile_id"):
            value = getattr(self, field_name)
            if value is not None:
                _text(value, field_name)
        for profile_field, version_field in (
            ("workspace_profile_id", "workspace_profile_version"),
            ("sandbox_profile_id", "sandbox_profile_version"),
        ):
            profile_id = getattr(self, profile_field)
            profile_version = getattr(self, version_field)
            if profile_id is None and profile_version is not None:
                raise ValueError(f"{version_field} requires {profile_field}")
            if profile_id is not None:
                if profile_version is None:
                    object.__setattr__(self, version_field, 1)
                elif profile_version < 1:
                    raise ValueError(f"{version_field} must be positive")
        if self.version < 1:
            raise ValueError("version must be positive")

    def publish(self) -> ExecutionProfile:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布 ExecutionProfile 不可修改")
        return replace(self, status=LifecycleStatus.PUBLISHED)


@dataclass(frozen=True, slots=True)
class DigitalEmployeeVersion:
    """固化员工指令、Skill/Tool 绑定和 ExecutionProfile 的不可变快照。"""

    employee_id: str
    tenant_id: str
    version: int
    system_prompt: str
    skill_version_ids: tuple[str, ...]
    tool_version_ids: tuple[str, ...]
    execution_profile_id: str
    status: LifecycleStatus = LifecycleStatus.DRAFT
    model_route_id: str | None = None
    workspace_profile_id: str | None = None
    workspace_profile_version: int | None = None
    sandbox_profile_id: str | None = None
    sandbox_profile_version: int | None = None
    approval_policy: str = "none"
    execution_profile_version: int = 1
    skill_version_refs: tuple[tuple[str, int], ...] = ()
    tool_version_refs: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("employee_id", "tenant_id", "system_prompt", "execution_profile_id"):
            _text(getattr(self, field_name), field_name)
        if self.version < 1:
            raise ValueError("version must be positive")
        if not isinstance(self.skill_version_ids, tuple) or not isinstance(self.tool_version_ids, tuple):
            raise TypeError("version references must be tuples")
        if self.execution_profile_version < 1:
            raise ValueError("execution_profile_version must be positive")
        if not self.skill_version_refs:
            object.__setattr__(
                self,
                "skill_version_refs",
                tuple((skill_id, 1) for skill_id in self.skill_version_ids),
            )
        if not self.tool_version_refs:
            object.__setattr__(
                self,
                "tool_version_refs",
                tuple((tool_id, 1) for tool_id in self.tool_version_ids),
            )
        if tuple(resource_id for resource_id, _ in self.skill_version_refs) != self.skill_version_ids:
            raise ValueError("skill_version_refs must match skill_version_ids order")
        if tuple(resource_id for resource_id, _ in self.tool_version_refs) != self.tool_version_ids:
            raise ValueError("tool_version_refs must match tool_version_ids order")
        if any(version < 1 for _, version in (*self.skill_version_refs, *self.tool_version_refs)):
            raise ValueError("Skill and Tool version references must be positive")
        for field_name in ("model_route_id", "workspace_profile_id", "sandbox_profile_id"):
            value = getattr(self, field_name)
            if value is not None:
                _text(value, field_name)
        for profile_field, version_field in (
            ("workspace_profile_id", "workspace_profile_version"),
            ("sandbox_profile_id", "sandbox_profile_version"),
        ):
            profile_id = getattr(self, profile_field)
            profile_version = getattr(self, version_field)
            if profile_id is None and profile_version is not None:
                raise ValueError(f"{version_field} requires {profile_field}")
            if profile_id is not None:
                if profile_version is None:
                    object.__setattr__(self, version_field, 1)
                elif profile_version < 1:
                    raise ValueError(f"{version_field} must be positive")

    def publish(self) -> DigitalEmployeeVersion:
        self.assert_mutable()
        if not self.skill_version_ids:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "数字员工至少需要一个 Skill")
        return replace(self, status=LifecycleStatus.PUBLISHED)

    def assert_mutable(self) -> None:
        if self.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布员工版本不可修改")

    def deprecate(self) -> DigitalEmployeeVersion:
        """结束已发布版本的新用途，保留固定配置和历史部署。"""

        if self.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "只有已发布员工版本才能废弃")
        return replace(self, status=LifecycleStatus.DEPRECATED)

    def selected_tool_names(self, names_by_id: Mapping[str, str]) -> tuple[str, ...]:
        """按员工版本声明顺序返回工具名，供 Runtime 请求使用。"""

        return tuple(names_by_id[tool_id] for tool_id in self.tool_version_ids if tool_id in names_by_id)


@dataclass(frozen=True, slots=True)
class DigitalEmployeeVersionPage:
    """单个员工固定版本列表的倒序游标分页结果。"""

    items: tuple[DigitalEmployeeVersion, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class Deployment:
    """在租户内固定员工版本；历史部署可继续引用已下线的版本。"""

    deployment_id: str
    tenant_id: str
    employee_id: str
    employee_version: DigitalEmployeeVersion
    status: LifecycleStatus = LifecycleStatus.ACTIVE

    def __post_init__(self) -> None:
        _text(self.deployment_id, "deployment_id")
        _scope(self.tenant_id)
        _text(self.employee_id, "employee_id")
        # 历史部署在版本下线后仍需可读；新建和回滚的目标由应用服务校验。
        if self.employee_version.status not in (LifecycleStatus.PUBLISHED, LifecycleStatus.DEPRECATED):
            raise ValueError("deployment requires a published or deprecated employee version")
        if self.employee_version.tenant_id != self.tenant_id:
            raise ValueError("deployment scope does not match employee version")
        if self.employee_version.employee_id != self.employee_id:
            raise ValueError("deployment employee does not match version")

    def disable(self) -> Deployment:
        """下线运行入口，保留固定的员工版本供历史记录引用。"""

        if self.status is not LifecycleStatus.ACTIVE:
            raise DomainError("INVALID_STATE", "只有上线的部署才能下线")
        return replace(self, status=LifecycleStatus.DISABLED)

    def enable(self) -> Deployment:
        """重新开放已下线的入口，前提是固定版本仍可用于部署。"""

        if self.status is not LifecycleStatus.DISABLED:
            raise DomainError("INVALID_STATE", "只有已下线的部署才能重新上线")
        if self.employee_version.status is not LifecycleStatus.PUBLISHED:
            raise DomainError("INVALID_STATE", "固定的员工版本必须已发布才能重新上线")
        return replace(self, status=LifecycleStatus.ACTIVE)

    def rollback(self, employee_version: DigitalEmployeeVersion) -> Deployment:
        """创建指向旧发布版本的新绑定，已有 Run 的快照不受影响。"""

        return replace(self, employee_version=employee_version)


@dataclass(frozen=True, slots=True)
class DeploymentPage:
    """按租户筛选后的 Deployment 游标分页结果。"""

    items: tuple[Deployment, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    """已有 audit_events 的租户范围只读记录。"""

    event_id: str
    platform_id: str
    tenant_id: str
    actor_type: str
    actor_user_id: str | None
    event_type: str
    resource_type: str | None
    resource_id: str | None
    payload: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    """按创建时间和事件 ID 倒序的审计事件游标页。"""

    items: tuple[AuditEventRecord, ...]
    next_cursor: str | None
