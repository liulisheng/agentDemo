"""Skill FILE_PACKAGE ZIP staging、清单预览和租户对象原子引用提交。"""

from __future__ import annotations

import hashlib
import mimetypes
import stat
import threading
import time
import unicodedata
import zipfile
from dataclasses import dataclass, replace
from io import BytesIO

from agent_platform.application.catalog_service import CatalogService
from agent_platform.domain.catalog import (
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillVersion,
    compute_skill_file_manifest_sha256,
    normalize_skill_file_path,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.resource_center import HubResourceType
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.storage import ObjectStore, ObjectStoreError, UploadRequest


@dataclass(frozen=True, slots=True)
class SkillZipFile:
    """预览文件的脱敏清单；不包含文件正文或对象引用。"""

    file_path: str
    mime_type: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SkillZipPreview:
    """ZIP 导入预览；staging_id 绑定租户、版本、策略和归档摘要。"""

    staging_id: str
    skill_id: str
    version: int
    strategy: str
    archive_sha256: str
    base_manifest_sha256: str | None
    manifest_sha256: str
    entry_path: str
    file_count: int
    files: tuple[SkillZipFile, ...]
    added_paths: tuple[str, ...]
    updated_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class _ParsedZipFile:
    file_path: str
    mime_type: str
    content: bytes
    content_sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class _StagedZip:
    staging_id: str
    platform_id: str
    tenant_id: str
    skill_id: str
    version: int
    strategy: str
    archive_sha256: str
    base_revision: SkillVersion
    manifest_sha256: str
    files: tuple[SkillZipFile, ...]
    expires_at: float


class SkillFilePackageService:
    """先验证 ZIP 和变更，再把文件对象原子关联到一个 Skill DRAFT 版本。"""

    MAX_ARCHIVE_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        catalog: CatalogService,
        object_store: ObjectStore,
        resource_center_repository: ResourceCenterRepository | None = None,
        *,
        max_archive_bytes: int = MAX_ARCHIVE_BYTES,
        max_uncompressed_bytes: int = 50 * 1024 * 1024,
        max_file_bytes: int = 10 * 1024 * 1024,
        max_files: int = 256,
        max_compression_ratio: int = 100,
        staging_ttl_seconds: int = 900,
    ) -> None:
        self.catalog = catalog
        self.object_store = object_store
        self.resource_center_repository = resource_center_repository
        self.max_archive_bytes = max_archive_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.max_compression_ratio = max_compression_ratio
        self.staging_ttl_seconds = staging_ttl_seconds
        self._stages: dict[str, _StagedZip] = {}
        self._stages_lock = threading.RLock()

    @staticmethod
    def _require_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以导入 Skill 文件包")

    @staticmethod
    def _invalid(message: str, **details: object) -> DomainError:
        return DomainError("INVALID_ARGUMENT", message, details or None)

    @staticmethod
    def _preview_entry(file_path: str, mime_type: str, content: bytes, digest: str) -> SkillFileEntry:
        # MediaAsset ID 不参与清单摘要；预览使用固定占位值且不写对象。
        return SkillFileEntry(
            file_path=file_path,
            media_asset_id="preview-asset",
            mime_type=mime_type,
            size_bytes=len(content),
            content_sha256=digest,
        )

    @staticmethod
    def _manifest_digest(entries: tuple[SkillFileEntry, ...]) -> str:
        return compute_skill_file_manifest_sha256(entries)

    @staticmethod
    def _mime_type(file_path: str) -> str:
        if file_path.casefold().endswith(".md"):
            return "text/markdown; charset=utf-8"
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"

    def _parse_zip(self, archive: bytes) -> tuple[_ParsedZipFile, ...]:
        if len(archive) > self.max_archive_bytes:
            raise self._invalid("ZIP 压缩包超过大小限制", max_bytes=self.max_archive_bytes)
        try:
            stream = BytesIO(archive)
            with zipfile.ZipFile(stream) as package:
                infos = package.infolist()
                file_infos = [info for info in infos if not info.is_dir()]
                if len(file_infos) > self.max_files:
                    raise self._invalid("ZIP 文件数量超过限制", max_files=self.max_files)
                total_size = 0
                files: list[_ParsedZipFile] = []
                collision_keys: dict[str, str] = {}
                directory_paths: set[str] = set()
                seen_paths: set[str] = set()
                for info in infos:
                    raw_path = info.filename[:-1] if info.is_dir() and info.filename.endswith("/") else info.filename
                    try:
                        canonical_path = normalize_skill_file_path(raw_path)
                    except ValueError as error:
                        raise self._invalid("ZIP 包含非法文件路径", path=info.filename) from error
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    if mode == stat.S_IFLNK:
                        raise self._invalid("ZIP 不允许包含符号链接", path=canonical_path)
                    if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise self._invalid("ZIP 包含不支持的特殊文件", path=canonical_path)
                    if info.is_dir():
                        directory_paths.add(canonical_path)
                        continue
                    if info.flag_bits & 0x1:
                        raise self._invalid("ZIP 加密文件不受支持", path=canonical_path)
                    if canonical_path in seen_paths:
                        raise self._invalid("ZIP 包含重复文件路径", path=canonical_path)
                    collision_key = unicodedata.normalize("NFKC", canonical_path).casefold()
                    previous_path = collision_keys.get(collision_key)
                    if previous_path is not None:
                        raise self._invalid(
                            "ZIP 包含重复规范化路径",
                            path=canonical_path,
                            previous_path=previous_path,
                        )
                    seen_paths.add(canonical_path)
                    collision_keys[collision_key] = canonical_path
                    if info.file_size > self.max_file_bytes:
                        raise self._invalid(
                            "ZIP 单文件解压大小超过限制",
                            path=canonical_path,
                            max_file_bytes=self.max_file_bytes,
                        )
                    if total_size + info.file_size > self.max_uncompressed_bytes:
                        raise self._invalid(
                            "ZIP 解压后总大小超过限制",
                            max_uncompressed_bytes=self.max_uncompressed_bytes,
                        )
                    if info.file_size and (
                        info.compress_size == 0
                        or info.file_size / info.compress_size > self.max_compression_ratio
                    ):
                        raise self._invalid(
                            "ZIP 文件压缩比超过限制",
                            path=canonical_path,
                            max_compression_ratio=self.max_compression_ratio,
                        )
                    try:
                        chunks: list[bytes] = []
                        actual_size = 0
                        with package.open(info) as member:
                            while True:
                                remaining_file = self.max_file_bytes - actual_size
                                remaining_package = self.max_uncompressed_bytes - total_size - actual_size
                                chunk = member.read(max(1, min(64 * 1024, remaining_file + 1, remaining_package + 1)))
                                if not chunk:
                                    break
                                actual_size += len(chunk)
                                if actual_size > self.max_file_bytes:
                                    raise self._invalid(
                                        "ZIP 单文件解压大小超过限制",
                                        path=canonical_path,
                                        max_file_bytes=self.max_file_bytes,
                                    )
                                if total_size + actual_size > self.max_uncompressed_bytes:
                                    raise self._invalid(
                                        "ZIP 解压后总大小超过限制",
                                        max_uncompressed_bytes=self.max_uncompressed_bytes,
                                    )
                                chunks.append(chunk)
                        content = b"".join(chunks)
                    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                        raise self._invalid("ZIP 文件无法安全解压", path=canonical_path) from error
                    if actual_size != info.file_size:
                        raise self._invalid("ZIP 文件解压大小与目录记录不匹配", path=canonical_path)
                    total_size += actual_size
                    if canonical_path.casefold() == "skill.md":
                        try:
                            entry_text = content.decode("utf-8-sig")
                        except UnicodeDecodeError as error:
                            raise self._invalid("根 SKILL.md 必须是 UTF-8 文本") from error
                        if not entry_text.strip():
                            raise self._invalid("根 SKILL.md 入口指令不能为空")
                    digest = hashlib.sha256(content).hexdigest()
                    files.append(
                        _ParsedZipFile(
                            canonical_path,
                            self._mime_type(canonical_path),
                            content,
                            digest,
                        )
                    )
        except DomainError:
            raise
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise self._invalid("上传内容不是有效 ZIP 文件") from error

        paths = {item.file_path for item in files}
        if "SKILL.md" not in paths:
            raise self._invalid("ZIP 根目录必须包含唯一入口文件 SKILL.md")
        nested_entries = [
            path for path in paths if path.rsplit("/", 1)[-1].casefold() == "skill.md" and path != "SKILL.md"
        ]
        if nested_entries:
            raise self._invalid(
                "ZIP 中只能有根目录 SKILL.md 作为入口文件",
                paths=sorted(nested_entries),
            )
        for path in paths:
            parents = path.split("/")[:-1]
            for index in range(1, len(parents) + 1):
                parent = "/".join(parents[:index])
                if parent in paths:
                    raise self._invalid("ZIP 文件路径与目录层级冲突", path=path, parent=parent)
        if any(directory in paths for directory in directory_paths):
            raise self._invalid("ZIP 文件与显式目录路径冲突")
        if not files:
            raise self._invalid("ZIP 文件包不能为空")
        return tuple(sorted(files, key=lambda item: item.file_path))

    def _prune_stages(self) -> None:
        now = time.monotonic()
        expired = [stage_id for stage_id, stage in self._stages.items() if stage.expires_at <= now]
        for stage_id in expired:
            self._stages.pop(stage_id, None)

    def _require_draft(
        self, identity: IdentityContext, skill_id: str, version: int
    ) -> SkillVersion:
        self._require_admin(identity)
        skill = self.catalog.repository.get_skill_version(identity, skill_id, version)
        if skill.status is not LifecycleStatus.DRAFT:
            raise DomainError("IMMUTABLE_VERSION", "已发布或 Hub REFERENCE Skill 文件只读")
        return skill

    @staticmethod
    def _normalize_path(path: str) -> str:
        try:
            return normalize_skill_file_path(path)
        except ValueError as error:
            raise DomainError("INVALID_ARGUMENT", "Skill 文件路径必须是规范的相对路径") from error

    @staticmethod
    def _path_key(path: str) -> str:
        return unicodedata.normalize("NFKC", path).casefold()

    @staticmethod
    def _expected_sha(value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise DomainError("INVALID_ARGUMENT", "expected_sha256 必须是 64 位 SHA-256")
        return normalized

    def _upload_file(
        self, identity: IdentityContext, file_path: str, mime_type: str, content: bytes
    ) -> tuple[SkillFileEntry, str]:
        if len(content) > self.max_file_bytes:
            raise DomainError(
                "INVALID_ARGUMENT",
                "Skill 文件大小超过限制",
                {"max_file_bytes": self.max_file_bytes},
            )
        if file_path == "SKILL.md":
            try:
                entry_text = content.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise DomainError("INVALID_ARGUMENT", "根 SKILL.md 必须是 UTF-8 文本") from error
            if not entry_text.strip():
                raise DomainError("INVALID_ARGUMENT", "根 SKILL.md 入口指令不能为空")
        digest = hashlib.sha256(content).hexdigest()
        intent = self.object_store.create_upload_intent(
            UploadRequest(
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                filename=file_path.rsplit("/", 1)[-1],
                mime_type=mime_type,
                size_bytes=len(content),
                retention_policy="conversation",
                expected_sha256=digest,
            )
        )
        try:
            asset = self.object_store.complete_upload(intent.upload_id, content)
        except Exception:
            try:
                self.object_store.delete(intent.object_ref, identity.platform_id, identity.tenant_id)
            except (PermissionError, ObjectStoreError):
                pass
            raise
        return (
            SkillFileEntry(file_path, asset.asset_id, mime_type, len(content), digest),
            asset.object_ref,
        )

    def _save_file_package(
        self,
        identity: IdentityContext,
        current: SkillVersion,
        entries: tuple[SkillFileEntry, ...],
    ) -> SkillVersion:
        if not any(entry.file_path == "SKILL.md" for entry in entries):
            raise DomainError("INVALID_ARGUMENT", "FILE_PACKAGE 必须保留根 SKILL.md")
        digest = compute_skill_file_manifest_sha256(entries)
        try:
            updated = replace(
                current,
                instruction="",
                content_format=SkillContentFormat.FILE_PACKAGE,
                entry_path="SKILL.md",
                file_manifest_sha256=digest,
                file_entries=tuple(sorted(entries, key=lambda item: item.file_path)),
            )
        except ValueError as error:
            raise DomainError("INVALID_ARGUMENT", "Skill 文件清单不符合 FILE_PACKAGE 规则") from error
        self.catalog.repository.save_skill_if_revision_matches(
            identity, updated, expected_revision=current
        )
        return updated

    def _reference_content(
        self, identity: IdentityContext, skill: SkillVersion
    ) -> tuple[object, dict[str, object]]:
        if self.resource_center_repository is None:
            raise DomainError("INVALID_STATE", "Hub REFERENCE 文件读取需要配置资源中心")
        from agent_platform.application.resolvers import HubResourceResolver

        _, package = HubResourceResolver(self.resource_center_repository).package_for_local(
            identity, HubResourceType.SKILL, skill.skill_id, skill.version
        )
        if package is None:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 版本不可读取")
        content = package.manifest.data.get("content")
        if not isinstance(content, dict):
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 清单格式非法")
        return package, content

    @staticmethod
    def _hub_file_entries(content: dict[str, object]) -> tuple[SkillFileEntry, ...]:
        raw_files = content.get("files")
        file_digest = content.get("file_manifest_sha256")
        if not isinstance(raw_files, list) or not isinstance(file_digest, str):
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 文件清单缺失")
        try:
            entries = tuple(
                SkillFileEntry(
                    file_path=str(item["file_path"]),
                    media_asset_id=str(item["media_asset_id"]),
                    mime_type=str(item["mime_type"]),
                    size_bytes=int(item["size_bytes"]),
                    content_sha256=str(item["content_sha256"]),
                )
                for item in raw_files
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 文件清单非法") from error
        if compute_skill_file_manifest_sha256(entries) != file_digest:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 清单摘要不匹配")
        return entries

    def list_files(
        self, identity: IdentityContext, skill_id: str, version: int
    ) -> tuple[SkillZipFile, ...]:
        """返回当前租户精确 Skill 版本的文件摘要；INLINE 以虚拟根入口呈现。"""

        skill = self.catalog.repository.get_skill_version(identity, skill_id, version)
        if skill.content_format is SkillContentFormat.INLINE:
            if skill.instruction.startswith("hub-reference://"):
                _, content_data = self._reference_content(identity, skill)
                instruction = content_data.get("instruction")
                if not isinstance(instruction, str):
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 缺少入口指令")
                content = instruction.encode("utf-8")
            else:
                content = skill.instruction.encode("utf-8")
            return (
                SkillZipFile(
                    "SKILL.md",
                    "text/markdown; charset=utf-8",
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                ),
            )
        if not skill.file_entries and skill.instruction.startswith("hub-reference://"):
            _, content_data = self._reference_content(identity, skill)
            entries = self._hub_file_entries(content_data)
            return tuple(
                SkillZipFile(item.file_path, item.mime_type, item.size_bytes, item.content_sha256)
                for item in entries
            )
        return tuple(
            SkillZipFile(
                entry.file_path,
                entry.mime_type,
                entry.size_bytes,
                entry.content_sha256,
            )
            for entry in skill.file_entries
        )

    def get_skill_version(
        self, identity: IdentityContext, skill_id: str, version: int
    ) -> SkillVersion:
        """读取当前租户指定 Skill 版本元数据。"""

        return self.catalog.repository.get_skill_version(identity, skill_id, version)

    def read_file(self, identity: IdentityContext, skill_id: str, version: int, file_path: str) -> bytes:
        """读取一个当前租户 Skill 文件；INLINE 根入口兼容为 UTF-8 正文。"""

        path = self._normalize_path(file_path)
        skill = self.catalog.repository.get_skill_version(identity, skill_id, version)
        if skill.content_format is SkillContentFormat.INLINE:
            if path != "SKILL.md":
                raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
            if skill.instruction.startswith("hub-reference://"):
                _, content_data = self._reference_content(identity, skill)
                instruction = content_data.get("instruction")
                if not isinstance(instruction, str):
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 缺少入口指令")
                return instruction.encode("utf-8")
            return skill.instruction.encode("utf-8")
        if not skill.file_entries and skill.instruction.startswith("hub-reference://"):
            package, content_data = self._reference_content(identity, skill)
            entry = next(
                (item for item in self._hub_file_entries(content_data) if item.file_path == path),
                None,
            )
            if entry is None:
                raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
            if self.object_store is None:
                raise DomainError("INVALID_STATE", "读取 Hub Skill 文件需要配置对象存储")
            try:
                content = self.object_store.read_asset(
                    entry.media_asset_id,
                    package.platform_id,
                    package.publisher_tenant_id,
                    max_bytes=self.max_file_bytes,
                )
            except (PermissionError, ObjectStoreError) as error:
                raise DomainError("RESOURCE_NOT_FOUND", "Hub Skill 文件内容不存在") from error
            if len(content) != entry.size_bytes or hashlib.sha256(content).hexdigest() != entry.content_sha256:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub Skill 文件摘要不匹配")
            return content
        entry = next((item for item in skill.file_entries if item.file_path == path), None)
        if entry is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
        try:
            content = self.object_store.read_asset(
                entry.media_asset_id,
                identity.platform_id,
                identity.tenant_id,
                max_bytes=self.max_file_bytes,
            )
        except (PermissionError, ObjectStoreError) as error:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件内容不存在") from error
        if hashlib.sha256(content).hexdigest() != entry.content_sha256:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Skill 文件内容摘要不匹配")
        return content

    def create_file(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        file_path: str,
        content: bytes,
        *,
        mime_type: str | None = None,
    ) -> SkillVersion:
        """在 Skill DRAFT 新建文件；INLINE 仅允许从根 SKILL.md 转为文件包。"""

        current = self._require_draft(identity, skill_id, version)
        path = self._normalize_path(file_path)
        if current.content_format is SkillContentFormat.INLINE and path != "SKILL.md":
            raise DomainError("INVALID_STATE", "INLINE Skill 必须先创建根 SKILL.md 才能添加辅助文件")
        existing = current.file_entries
        if any(self._path_key(item.file_path) == self._path_key(path) for item in existing):
            raise DomainError("INVALID_ARGUMENT", "Skill 文件路径已存在", {"path": path})
        selected_mime_type = mime_type or self._mime_type(path)
        asset_entry, object_ref = self._upload_file(identity, path, selected_mime_type, content)
        entries = (*existing, asset_entry)
        try:
            return self._save_file_package(identity, current, entries)
        except Exception:
            try:
                self.object_store.delete(object_ref, identity.platform_id, identity.tenant_id)
            except (PermissionError, ObjectStoreError):
                pass
            raise

    def update_file(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        file_path: str,
        content: bytes,
        *,
        expected_sha256: str,
        mime_type: str | None = None,
    ) -> SkillVersion:
        """按文件 SHA-256 乐观并发更新文件或兼容 INLINE 根指令。"""

        current = self._require_draft(identity, skill_id, version)
        path = self._normalize_path(file_path)
        expected = self._expected_sha(expected_sha256)
        if current.content_format is SkillContentFormat.INLINE:
            old_content = current.instruction.encode("utf-8")
            if path != "SKILL.md":
                raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
            if hashlib.sha256(old_content).hexdigest() != expected:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 文件已被修改，请重新载入")
            try:
                instruction = content.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise DomainError("INVALID_ARGUMENT", "INLINE Skill 指令必须是 UTF-8 文本") from error
            if not instruction.strip():
                raise DomainError("INVALID_ARGUMENT", "Skill 指令不能为空")
            updated = replace(current, instruction=instruction)
            self.catalog.repository.save_skill_if_revision_matches(
                identity, updated, expected_revision=current
            )
            return updated

        entry = next((item for item in current.file_entries if item.file_path == path), None)
        if entry is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
        if entry.content_sha256 != expected:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 文件已被修改，请重新载入")
        selected_mime_type = mime_type or entry.mime_type
        if (
            hashlib.sha256(content).hexdigest() == entry.content_sha256
            and len(content) == entry.size_bytes
            and selected_mime_type == entry.mime_type
        ):
            return current
        replacement_entry, object_ref = self._upload_file(
            identity, path, selected_mime_type, content
        )
        entries = tuple(replacement_entry if item.file_path == path else item for item in current.file_entries)
        try:
            return self._save_file_package(identity, current, entries)
        except Exception:
            try:
                self.object_store.delete(object_ref, identity.platform_id, identity.tenant_id)
            except (PermissionError, ObjectStoreError):
                pass
            raise

    def rename_file(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        file_path: str,
        new_file_path: str,
        *,
        expected_sha256: str,
    ) -> SkillVersion:
        """修改文件路径但复用原 MediaAsset；根入口始终固定为 SKILL.md。"""

        current = self._require_draft(identity, skill_id, version)
        old_path = self._normalize_path(file_path)
        new_path = self._normalize_path(new_file_path)
        expected = self._expected_sha(expected_sha256)
        if current.content_format is not SkillContentFormat.FILE_PACKAGE:
            raise DomainError("INVALID_STATE", "INLINE Skill 没有可重命名的文件清单")
        if old_path == "SKILL.md" or new_path == "SKILL.md":
            raise DomainError("INVALID_ARGUMENT", "根 SKILL.md 入口路径不能重命名")
        entry = next((item for item in current.file_entries if item.file_path == old_path), None)
        if entry is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
        if entry.content_sha256 != expected:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 文件已被修改，请重新载入")
        if any(
            item.file_path != old_path and self._path_key(item.file_path) == self._path_key(new_path)
            for item in current.file_entries
        ):
            raise DomainError("INVALID_ARGUMENT", "目标 Skill 文件路径已存在", {"path": new_path})
        renamed = SkillFileEntry(
            new_path,
            entry.media_asset_id,
            entry.mime_type,
            entry.size_bytes,
            entry.content_sha256,
        )
        entries = tuple(renamed if item.file_path == old_path else item for item in current.file_entries)
        return self._save_file_package(identity, current, entries)

    def delete_file(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        file_path: str,
        *,
        expected_sha256: str,
    ) -> SkillVersion:
        """按文件 SHA-256 乐观并发删除辅助文件，根入口不可删除。"""

        current = self._require_draft(identity, skill_id, version)
        path = self._normalize_path(file_path)
        expected = self._expected_sha(expected_sha256)
        if path == "SKILL.md":
            raise DomainError("INVALID_ARGUMENT", "根 SKILL.md 入口不能删除")
        if current.content_format is not SkillContentFormat.FILE_PACKAGE:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
        entry = next((item for item in current.file_entries if item.file_path == path), None)
        if entry is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Skill 文件不存在")
        if entry.content_sha256 != expected:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 文件已被修改，请重新载入")
        entries = tuple(item for item in current.file_entries if item.file_path != path)
        return self._save_file_package(identity, current, entries)

    def preview_zip(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        archive: bytes,
        *,
        strategy: str = "replace",
    ) -> SkillZipPreview:
        """只读解压并展示 replace/merge 计划；不会创建 MediaAsset 或改写目录。"""

        if strategy not in {"replace", "merge"}:
            raise self._invalid("ZIP 写入策略必须是 replace 或 merge")
        current = self._require_draft(identity, skill_id, version)
        files = self._parse_zip(archive)
        before = {item.file_path: item for item in current.file_entries}
        upload = {
            item.file_path: self._preview_entry(
                item.file_path, item.mime_type, item.content, item.content_sha256
            )
            for item in files
        }
        after = dict(before) if strategy == "merge" else {}
        after.update(upload)
        current_by_path = before
        added = tuple(sorted(path for path in upload if path not in current_by_path))
        updated = tuple(
            sorted(
                path
                for path, item in upload.items()
                if path in current_by_path
                and (
                    item.content_sha256 != current_by_path[path].content_sha256
                    or item.size_bytes != current_by_path[path].size_bytes
                    or item.mime_type != current_by_path[path].mime_type
                )
            )
        )
        removed = tuple(sorted(set(before).difference(after)))
        preview_entries = tuple(after[path] for path in sorted(after))
        manifest_sha256 = self._manifest_digest(preview_entries)
        archive_sha256 = hashlib.sha256(archive).hexdigest()
        staging_id = generate_database_id()
        stage = _StagedZip(
            staging_id=staging_id,
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            skill_id=skill_id,
            version=version,
            strategy=strategy,
            archive_sha256=archive_sha256,
            base_revision=current,
            manifest_sha256=manifest_sha256,
            files=tuple(
                SkillZipFile(item.file_path, item.mime_type, item.size_bytes, item.content_sha256)
                for item in files
            ),
            expires_at=time.monotonic() + self.staging_ttl_seconds,
        )
        with self._stages_lock:
            self._prune_stages()
            if len(self._stages) >= 64:
                raise DomainError("INVALID_STATE", "Skill ZIP 预览暂存数已达上限，请稍后重试")
            self._stages[staging_id] = stage
        return SkillZipPreview(
            staging_id=staging_id,
            skill_id=skill_id,
            version=version,
            strategy=strategy,
            archive_sha256=archive_sha256,
            base_manifest_sha256=current.file_manifest_sha256,
            manifest_sha256=manifest_sha256,
            entry_path="SKILL.md",
            file_count=len(files),
            files=stage.files,
            added_paths=added,
            updated_paths=updated,
            removed_paths=removed,
            expires_in_seconds=self.staging_ttl_seconds,
        )

    def commit_zip(
        self,
        identity: IdentityContext,
        skill_id: str,
        version: int,
        staging_id: str,
        archive: bytes,
    ) -> SkillVersion:
        """重新校验同一 ZIP 并以一个 Catalog 事务提交完整清单。"""

        self._require_admin(identity)
        with self._stages_lock:
            self._prune_stages()
            stage = self._stages.get(staging_id)
        if (
            stage is None
            or stage.platform_id != identity.platform_id
            or stage.tenant_id != identity.tenant_id
            or stage.skill_id != skill_id
            or stage.version != version
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "ZIP 预览暂存不存在或已过期")
        archive_sha256 = hashlib.sha256(archive).hexdigest()
        if archive_sha256 != stage.archive_sha256:
            raise DomainError("IDEMPOTENCY_CONFLICT", "提交的 ZIP 与预览文件不一致")
        current = self._require_draft(identity, skill_id, version)
        if current != stage.base_revision:
            raise DomainError("IDEMPOTENCY_CONFLICT", "Skill 在 ZIP 预览后已被修改，请重新预览")
        files = self._parse_zip(archive)
        actual_previews = tuple(
            SkillZipFile(item.file_path, item.mime_type, item.size_bytes, item.content_sha256)
            for item in files
        )
        if actual_previews != stage.files:
            raise DomainError("IDEMPOTENCY_CONFLICT", "ZIP 解压清单与预览结果不一致")

        old_entries = {item.file_path: item for item in current.file_entries}
        resulting_entries = dict(old_entries) if stage.strategy == "merge" else {}
        created_objects: list[str] = []
        try:
            for item in files:
                previous = old_entries.get(item.file_path)
                if (
                    previous is not None
                    and previous.content_sha256 == item.content_sha256
                    and previous.size_bytes == item.size_bytes
                    and previous.mime_type == item.mime_type
                ):
                    resulting_entries[item.file_path] = previous
                    continue
                intent = self.object_store.create_upload_intent(
                    UploadRequest(
                        platform_id=identity.platform_id,
                        tenant_id=identity.tenant_id,
                        filename=item.file_path.rsplit("/", 1)[-1],
                        mime_type=item.mime_type,
                        size_bytes=item.size_bytes,
                        retention_policy="conversation",
                        expected_sha256=item.content_sha256,
                    )
                )
                created_objects.append(intent.object_ref)
                asset = self.object_store.complete_upload(intent.upload_id, item.content)
                resulting_entries[item.file_path] = SkillFileEntry(
                    item.file_path,
                    asset.asset_id,
                    item.mime_type,
                    item.size_bytes,
                    item.content_sha256,
                )
            final_entries = tuple(resulting_entries[path] for path in sorted(resulting_entries))
            final_digest = compute_skill_file_manifest_sha256(final_entries)
            if final_digest != stage.manifest_sha256:
                raise DomainError("IDEMPOTENCY_CONFLICT", "提交清单摘要与预览摘要不一致")
            updated = replace(
                current,
                instruction="",
                content_format=SkillContentFormat.FILE_PACKAGE,
                entry_path="SKILL.md",
                file_manifest_sha256=final_digest,
                file_entries=final_entries,
            )
            self.catalog.repository.save_skill_if_revision_matches(
                identity, updated, expected_revision=stage.base_revision
            )
        except Exception:
            for object_ref in created_objects:
                try:
                    self.object_store.delete(object_ref, identity.platform_id, identity.tenant_id)
                except (PermissionError, ObjectStoreError):
                    pass
            raise
        with self._stages_lock:
            self._stages.pop(staging_id, None)
        return updated
