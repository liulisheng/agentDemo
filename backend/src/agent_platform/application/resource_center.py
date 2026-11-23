"""将租户已发布 Catalog 版本打包并发布到平台资源中心。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from agent_platform.application.catalog_service import CatalogService
from agent_platform.domain.catalog import (
    DigitalEmployee,
    DigitalEmployeeVersion,
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillType,
    SkillVersion,
    ToolVersion,
    compute_skill_file_manifest_sha256,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.resource_center import (
    HubGrant,
    HubImportMode,
    HubResource,
    HubResourceDependency,
    HubResourceDetail,
    HubResourceStatus,
    HubResourceSummary,
    HubResourceType,
    HubResourceVersion,
    HubVersionStatus,
    HubVisibility,
    TenantImportStatus,
    TenantResourceImport,
    build_hub_manifest,
)
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.storage import ObjectStore, ObjectStoreError, UploadRequest


class ResourceCenterService:
    """发布当前租户的精确版本；Hub Package 不复制平台外租户密钥引用。"""

    def __init__(
        self,
        catalog: CatalogService,
        repository: ResourceCenterRepository,
        object_store: ObjectStore | None = None,
    ) -> None:
        self.catalog = catalog
        self.repository = repository
        self.object_store = object_store

    @staticmethod
    def _require_admin(identity: IdentityContext) -> None:
        if "admin" not in identity.roles:
            raise DomainError("AUTHORIZATION_DENIED", "只有租户管理员可以发布资源中心资源")

    @staticmethod
    def _require_published(source: object, resource_type: HubResourceType) -> None:
        status = getattr(source, "status", None)
        if status is None or status.value != "PUBLISHED":
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "只有租户已发布版本可以发布到资源中心",
                {"resource_type": resource_type.value},
            )

    def _resolve_dependency(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        source_id: str,
        source_version: int,
        sequence_no: int,
    ) -> tuple[HubResourceDependency, dict[str, object]]:
        target = self.repository.find_published_source_version(
            identity, resource_type, source_id, source_version
        )
        dependency_id = generate_database_id()
        item = {
            "sequence_no": sequence_no,
            "type": resource_type.value,
            "resource_id": target.hub_resource_id,
            "version": target.version,
            "required": True,
        }
        return (
            HubResourceDependency(
                dependency_id=dependency_id,
                dependency_type=resource_type,
                dependency_hub_resource_id=target.hub_resource_id,
                dependency_hub_version=target.version,
                sequence_no=sequence_no,
            ),
            item,
        )

    def _package_source(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        source_id: str,
        source_version: int,
    ) -> tuple[str, str, dict[str, object], tuple[HubResourceDependency, ...], list[dict[str, object]]]:
        if resource_type is HubResourceType.TOOL:
            source = self.catalog.repository.get_tool_version(identity, source_id, source_version)
            self._require_published(source, resource_type)
            content: dict[str, object] = {
                "protocol": source.protocol,
                "description": source.description,
                "input_schema": dict(source.input_schema),
                "handler_name": source.handler_name,
                # 只导出“需要接收方重绑凭据”的布尔标记，发布方 SecretRef/明文不离开本租户。
                "auth_secret_ref": source.requires_recipient_credentials or source.auth_secret_ref is not None,
            }
            return source.name, source.description, content, (), []

        if resource_type is HubResourceType.SKILL:
            source = self.catalog.repository.get_skill_version(identity, source_id, source_version)
            self._require_published(source, resource_type)
            dependencies: list[HubResourceDependency] = []
            manifest_dependencies: list[dict[str, object]] = []
            for sequence_no, (tool_id, tool_version) in enumerate(source.allowed_tool_versions, start=1):
                dependency, manifest_item = self._resolve_dependency(
                    identity,
                    HubResourceType.TOOL,
                    tool_id,
                    tool_version,
                    sequence_no,
                )
                dependencies.append(dependency)
                manifest_dependencies.append(manifest_item)
            if source.content_format is SkillContentFormat.FILE_PACKAGE:
                if self.object_store is None:
                    raise DomainError("INVALID_STATE", "发布 FILE_PACKAGE Skill 需要配置对象存储")
                source_files: list[dict[str, object]] = []
                entry_content: str | None = None
                for entry in source.file_entries:
                    try:
                        file_bytes = self.object_store.read_asset(
                            entry.media_asset_id,
                            identity.platform_id,
                            identity.tenant_id,
                            max_bytes=10 * 1024 * 1024,
                        )
                    except (PermissionError, ObjectStoreError) as error:
                        raise DomainError(
                            "DEPENDENCY_VALIDATION_ERROR",
                            "发布 Skill 文件不存在或不属于当前租户",
                            {"skill_id": source.skill_id, "file_path": entry.file_path},
                        ) from error
                    if (
                        len(file_bytes) != entry.size_bytes
                        or hashlib.sha256(file_bytes).hexdigest() != entry.content_sha256
                    ):
                        raise DomainError(
                            "DEPENDENCY_VALIDATION_ERROR",
                            "发布 Skill 文件摘要不匹配",
                            {"skill_id": source.skill_id, "file_path": entry.file_path},
                        )
                    if entry.file_path == source.entry_path:
                        try:
                            entry_content = file_bytes.decode("utf-8-sig")
                        except UnicodeDecodeError as error:
                            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "根 SKILL.md 必须是 UTF-8 文本") from error
                    source_files.append(
                        {
                            "file_path": entry.file_path,
                            "media_asset_id": entry.media_asset_id,
                            "mime_type": entry.mime_type,
                            "size_bytes": entry.size_bytes,
                            "content_sha256": entry.content_sha256,
                        }
                    )
                if not entry_content or not entry_content.strip():
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "FILE_PACKAGE 根 SKILL.md 入口内容为空")
                if compute_skill_file_manifest_sha256(source.file_entries) != source.file_manifest_sha256:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "FILE_PACKAGE Skill 清单摘要不匹配")
                skill_description = entry_content[:1024]
                content = {
                    "content_format": SkillContentFormat.FILE_PACKAGE.value,
                    "entry_path": source.entry_path,
                    "file_manifest_sha256": source.file_manifest_sha256,
                    "files": source_files,
                    "skill_type": source.skill_type.value,
                    "input_schema": dict(source.input_schema),
                    "output_schema": dict(source.output_schema),
                    "trigger_rule": source.trigger,
                    "workflow_steps": list(source.workflow_steps),
                    "memory_policy": source.memory_policy,
                    "approval_policy": source.approval_policy,
                }
            else:
                skill_description = source.instruction[:1024]
                content = {
                    "content_format": SkillContentFormat.INLINE.value,
                    "instruction": source.instruction,
                    "skill_type": source.skill_type.value,
                    "input_schema": dict(source.input_schema),
                    "output_schema": dict(source.output_schema),
                    "trigger_rule": source.trigger,
                    "workflow_steps": list(source.workflow_steps),
                    "memory_policy": source.memory_policy,
                    "approval_policy": source.approval_policy,
                }
            return (
                source.name,
                skill_description,
                content,
                tuple(dependencies),
                manifest_dependencies,
            )

        source = self.catalog.repository.get_employee_version(identity, source_id, source_version)
        self._require_published(source, resource_type)
        employee = self.catalog.repository.get_employee(identity, source_id)
        dependencies: list[HubResourceDependency] = []
        manifest_dependencies: list[dict[str, object]] = []
        seen: set[tuple[str, int]] = set()
        for skill_id, skill_version in source.skill_version_refs:
            dependency, manifest_item = self._resolve_dependency(
                identity,
                HubResourceType.SKILL,
                skill_id,
                skill_version,
                len(manifest_dependencies) + 1,
            )
            key = (dependency.dependency_hub_resource_id, dependency.dependency_hub_version)
            if key not in seen:
                seen.add(key)
                dependencies.append(dependency)
                manifest_dependencies.append(manifest_item)
        for tool_id, tool_version in source.tool_version_refs:
            dependency, manifest_item = self._resolve_dependency(
                identity,
                HubResourceType.TOOL,
                tool_id,
                tool_version,
                len(manifest_dependencies) + 1,
            )
            key = (dependency.dependency_hub_resource_id, dependency.dependency_hub_version)
            if key not in seen:
                seen.add(key)
                dependencies.append(dependency)
                manifest_dependencies.append(manifest_item)
        content = {
            "role_name": employee.role_name,
            "description": employee.description,
            "system_prompt": source.system_prompt,
        }
        return employee.name, employee.description, content, tuple(dependencies), manifest_dependencies

    def create_package(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType | str,
        source_resource_id: str,
        source_resource_version: int,
        *,
        visibility: HubVisibility | str = HubVisibility.PLATFORM,
        hub_resource_id: str | None = None,
    ) -> tuple[HubResource, HubResourceVersion]:
        """为租户已发布的精确资源版本创建 Hub 条目或追加后续 Hub 版本。"""

        self._require_admin(identity)
        selected_type = HubResourceType(resource_type)
        name, description, content, dependencies, manifest_dependencies = self._package_source(
            identity, selected_type, source_resource_id, source_resource_version
        )
        existing = (
            self.repository.get_owned_resource(identity, hub_resource_id)
            if hub_resource_id is not None
            else None
        )
        if existing is not None and existing.resource_type is not selected_type:
            raise DomainError("INVALID_ARGUMENT", "Hub 资源类型不能跨版本改变")
        version_number = (
            self.repository.next_version(identity, hub_resource_id)
            if hub_resource_id is not None
            else 1
        )
        manifest = build_hub_manifest(
            resource_type=selected_type.value,
            name=name,
            version=version_number,
            content=content,
            dependencies=manifest_dependencies,
        )
        resource = existing or HubResource(
            platform_id=identity.platform_id,
            hub_resource_id=generate_database_id(),
            resource_type=selected_type,
            publisher_tenant_id=identity.tenant_id,
            publisher_user_id=identity.user_id,
            name=name,
            description=description,
            visibility=HubVisibility(visibility),
            status=HubResourceStatus.DRAFT,
        )
        version = HubResourceVersion(
            platform_id=identity.platform_id,
            hub_resource_id=resource.hub_resource_id,
            version=version_number,
            publisher_tenant_id=identity.tenant_id,
            source_resource_type=selected_type,
            source_resource_id=source_resource_id,
            source_resource_version=source_resource_version,
            manifest=manifest,
            artifact_asset_id=None,
            created_by=identity.user_id,
            status=HubVersionStatus.DRAFT,
        )
        if existing is None:
            self.repository.create_package(identity, resource, version, dependencies)
        else:
            self.repository.create_version(identity, version, dependencies)
        return resource, version

    def publish_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceVersion:
        """发布指定 Hub DRAFT 版本；发布后资源清单不可原地修改。"""

        self._require_admin(identity)
        return self.repository.publish_version(identity, hub_resource_id, version)

    def import_resource(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode | str = HubImportMode.COPY,
    ) -> TenantResourceImport:
        """递归导入已授权 Hub Package；COPY 可编辑，REFERENCE 物化为租户本地只读代理。"""

        self._require_admin(identity)
        selected_mode = HubImportMode(mode)
        return self._import_recursive(identity, hub_resource_id, version, (), selected_mode)

    def list_imports(
        self,
        identity: IdentityContext,
        *,
        status: TenantImportStatus | str | None = None,
        resource_type: HubResourceType | str | None = None,
        limit: int = 100,
    ) -> tuple[TenantResourceImport, ...]:
        """返回当前租户的导入清单；平台和租户范围由仓储查询强制限定。"""

        return self.repository.list_imports(
            identity,
            status=TenantImportStatus(status) if status is not None else None,
            resource_type=HubResourceType(resource_type) if resource_type is not None else None,
            limit=limit,
        )

    def upgrade_import(
        self, identity: IdentityContext, import_id: str, target_hub_version: int
    ) -> TenantResourceImport:
        """显式导入同一 Hub 条目的新版本，旧代理和员工绑定保持不变。"""

        self._require_admin(identity)
        current = self.repository.get_import_by_id(identity, import_id)
        if current.status is not TenantImportStatus.ACTIVE:
            raise DomainError("INVALID_STATE", "只有 ACTIVE 导入可以升级")
        if target_hub_version <= current.hub_version:
            raise DomainError("INVALID_ARGUMENT", "升级目标必须是更高的 Hub 版本")
        return self.import_resource(
            identity,
            current.hub_resource_id,
            target_hub_version,
            current.mode,
        )

    def revoke_import(self, identity: IdentityContext, import_id: str) -> TenantResourceImport:
        """撤销租户本地 REFERENCE 映射，COPY 副本仍归接收租户独立管理。"""

        self._require_admin(identity)
        current = self.repository.get_import_by_id(identity, import_id)
        if current.mode is not HubImportMode.REFERENCE:
            raise DomainError("INVALID_STATE", "COPY 副本独立于 Hub 授权，不能撤销其本地来源映射")
        self.repository.revoke_import(identity, import_id)
        return self.repository.get_import_by_id(identity, import_id)

    def revoke_grant(self, identity: IdentityContext, grant_id: str) -> None:
        """由发布租户管理员撤销精确版本 Grant，阻止后续 REFERENCE Run。"""

        self._require_admin(identity)
        self.repository.revoke_grant(identity, grant_id)

    def _import_recursive(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        ancestors: tuple[tuple[str, int], ...],
        mode: HubImportMode,
    ) -> TenantResourceImport:
        key = (hub_resource_id, version)
        if key in ancestors:
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "Hub Package 依赖中存在循环",
                {"hub_resource_id": hub_resource_id, "version": version},
            )
        if len(ancestors) >= 32:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub Package 依赖层级超出限制")
        package = self.repository.get_authorized_version(
            identity, hub_resource_id, version, mode
        )
        if package.publisher_tenant_id == identity.tenant_id:
            raise DomainError("INVALID_ARGUMENT", "发布租户应直接使用本地资源，无需导入自己的 Hub 包")
        existing = self.repository.get_import(identity, hub_resource_id, version, mode)
        if existing is not None and existing.status is TenantImportStatus.ACTIVE:
            return existing
        grant = self.repository.get_authorized_grant(
            identity, hub_resource_id, version, mode
        )
        if mode is HubImportMode.COPY and package.artifact_asset_id is not None:
            raise DomainError(
                "INVALID_STATE",
                "含 Hub 包附件的 COPY 需要配置对象复制 Worker",
                {"hub_resource_id": hub_resource_id, "version": version},
            )
        manifest_resource = package.manifest.data.get("resource")
        content = package.manifest.data.get("content")
        manifest_dependencies = package.manifest.data.get("dependencies", [])
        if not isinstance(manifest_resource, dict) or not isinstance(content, dict):
            raise DomainError("INVALID_STATE", "Hub Manifest 缺少资源内容")
        if not isinstance(manifest_dependencies, list):
            raise DomainError("INVALID_STATE", "Hub Manifest 依赖清单格式非法")

        imported_dependencies: list[tuple[dict[str, Any], TenantResourceImport]] = []
        for dependency in manifest_dependencies:
            if not isinstance(dependency, dict):
                raise DomainError("INVALID_STATE", "Hub Manifest 依赖项格式非法")
            try:
                child_id = str(dependency["resource_id"])
                child_version = int(dependency["version"])
            except (KeyError, TypeError, ValueError) as error:
                raise DomainError("INVALID_STATE", "Hub Manifest 依赖项缺少版本引用") from error
            child_import = self._import_recursive(
                identity, child_id, child_version, (*ancestors, key), mode
            )
            expected_type = HubResourceType(str(dependency["type"]))
            if child_import.local_resource_type is not expected_type:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub 依赖资源类型与导入结果不匹配")
            imported_dependencies.append((dependency, child_import))

        if existing is None:
            import_record = TenantResourceImport(
                platform_id=identity.platform_id,
                tenant_id=identity.tenant_id,
                import_id=generate_database_id(),
                grant_id=grant.grant_id,
                hub_resource_id=hub_resource_id,
                hub_version=version,
                mode=mode,
                local_resource_type=package.source_resource_type,
                local_resource_id=generate_database_id(),
                local_resource_version=1,
                manifest_sha256=package.manifest.sha256,
                imported_by=identity.user_id,
                status=TenantImportStatus.IMPORTING,
            )
        else:
            import_record = TenantResourceImport(
                platform_id=existing.platform_id,
                tenant_id=existing.tenant_id,
                import_id=existing.import_id,
                grant_id=grant.grant_id,
                hub_resource_id=existing.hub_resource_id,
                hub_version=existing.hub_version,
                mode=existing.mode,
                local_resource_type=existing.local_resource_type,
                local_resource_id=existing.local_resource_id,
                local_resource_version=existing.local_resource_version,
                manifest_sha256=existing.manifest_sha256,
                imported_by=identity.user_id,
                status=TenantImportStatus.IMPORTING,
            )
        import_record = self.repository.begin_import(identity, import_record)
        if import_record.status is TenantImportStatus.ACTIVE:
            return import_record

        try:
            self._materialize_import(
                identity, import_record, package, manifest_resource, content, imported_dependencies
            )
            return self.repository.complete_import(identity, import_record.import_id)
        except Exception:
            self.repository.fail_import(identity, import_record.import_id)
            raise

    def _materialize_import(
        self,
        identity: IdentityContext,
        import_record: TenantResourceImport,
        package: HubResourceVersion,
        manifest_resource: dict[str, Any],
        content: dict[str, Any],
        imported_dependencies: list[tuple[dict[str, Any], TenantResourceImport]],
    ) -> None:
        """以租户自己的 ID 创建副本或只读代理；上游 Hub 内容不会被原地修改。"""

        copied = import_record.mode is HubImportMode.COPY
        local_status = LifecycleStatus.DRAFT if copied else LifecycleStatus.PUBLISHED
        reference_marker = f"hub-reference://{package.hub_resource_id}/versions/{package.version}"
        name = str(manifest_resource.get("name", "")).strip()
        if not name:
            raise DomainError("INVALID_STATE", "Hub Manifest 资源名称为空")
        if import_record.local_resource_type is HubResourceType.TOOL:
            tool = ToolVersion(
                tool_id=import_record.local_resource_id,
                tenant_id=identity.tenant_id,
                name=name,
                description=str(content.get("description", "")),
                input_schema=content.get("input_schema", {"type": "object"}),
                version=import_record.local_resource_version,
                status=local_status,
                handler_name=(
                    str(content.get("handler_name", "echo"))
                    if copied
                    else "hub-reference"
                ),
                protocol=str(content.get("protocol", "builtin")),
                requires_recipient_credentials=bool(
                    package.manifest.data.get("requires_recipient_credentials", False)
                ),
            )
            self.catalog.repository.save_tool(identity, tool)
            return

        if import_record.local_resource_type is HubResourceType.SKILL:
            if str(content.get("content_format", "INLINE")) != "INLINE":
                selected_format = SkillContentFormat(str(content.get("content_format")))
            else:
                selected_format = SkillContentFormat.INLINE
            tool_bindings = [
                imported
                for dependency, imported in imported_dependencies
                if dependency["type"] == HubResourceType.TOOL.value
            ]
            if selected_format is SkillContentFormat.FILE_PACKAGE:
                entry_path = content.get("entry_path")
                file_digest = content.get("file_manifest_sha256")
                files_data = content.get("files")
                if entry_path != "SKILL.md" or not isinstance(file_digest, str) or not isinstance(files_data, list):
                    raise DomainError("INVALID_STATE", "Hub FILE_PACKAGE Skill 清单格式非法")
                try:
                    source_entries = tuple(
                        SkillFileEntry(
                            file_path=str(item["file_path"]),
                            media_asset_id=str(item["media_asset_id"]),
                            mime_type=str(item["mime_type"]),
                            size_bytes=int(item["size_bytes"]),
                            content_sha256=str(item["content_sha256"]),
                        )
                        for item in files_data
                        if isinstance(item, dict)
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise DomainError("INVALID_STATE", "Hub FILE_PACKAGE Skill 文件行非法") from error
                if compute_skill_file_manifest_sha256(source_entries) != file_digest:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub FILE_PACKAGE Skill 摘要不匹配")
                if copied:
                    if self.object_store is None:
                        raise DomainError("INVALID_STATE", "COPY FILE_PACKAGE Skill 需要配置对象存储")
                    local_entries: list[SkillFileEntry] = []
                    copied_objects: list[str] = []
                    try:
                        for source_entry in source_entries:
                            try:
                                file_content = self.object_store.read_asset(
                                    source_entry.media_asset_id,
                                    package.platform_id,
                                    package.publisher_tenant_id,
                                    max_bytes=10 * 1024 * 1024,
                                )
                            except (PermissionError, ObjectStoreError) as error:
                                raise DomainError(
                                    "DEPENDENCY_VALIDATION_ERROR",
                                    "Hub Skill 文件对象不可读取",
                                    {"file_path": source_entry.file_path},
                                ) from error
                            if (
                                len(file_content) != source_entry.size_bytes
                                or hashlib.sha256(file_content).hexdigest() != source_entry.content_sha256
                            ):
                                raise DomainError(
                                    "DEPENDENCY_VALIDATION_ERROR",
                                    "Hub Skill 文件内容摘要不匹配",
                                    {"file_path": source_entry.file_path},
                                )
                            intent = self.object_store.create_upload_intent(
                                UploadRequest(
                                    platform_id=identity.platform_id,
                                    tenant_id=identity.tenant_id,
                                    filename=source_entry.file_path.rsplit("/", 1)[-1],
                                    mime_type=source_entry.mime_type,
                                    size_bytes=len(file_content),
                                    retention_policy="conversation",
                                    expected_sha256=source_entry.content_sha256,
                                )
                            )
                            copied_objects.append(intent.object_ref)
                            asset = self.object_store.complete_upload(intent.upload_id, file_content)
                            local_entries.append(
                                SkillFileEntry(
                                    source_entry.file_path,
                                    asset.asset_id,
                                    source_entry.mime_type,
                                    source_entry.size_bytes,
                                    source_entry.content_sha256,
                                )
                            )
                        skill = SkillVersion(
                            skill_id=import_record.local_resource_id,
                            tenant_id=identity.tenant_id,
                            name=name,
                            skill_type=SkillType(str(content["skill_type"])),
                            instruction="",
                            allowed_tool_ids=tuple(item.local_resource_id for item in tool_bindings),
                            input_schema=content.get("input_schema", {"type": "object"}),
                            output_schema=content.get("output_schema", {"type": "object"}),
                            trigger=content.get("trigger_rule"),
                            workflow_steps=tuple(str(step) for step in content.get("workflow_steps", [])),
                            memory_policy=str(content.get("memory_policy", "none")),
                            approval_policy=str(content.get("approval_policy", "none")),
                            version=import_record.local_resource_version,
                            status=LifecycleStatus.DRAFT,
                            allowed_tool_versions=tuple(
                                (item.local_resource_id, item.local_resource_version)
                                for item in tool_bindings
                            ),
                            content_format=SkillContentFormat.FILE_PACKAGE,
                            entry_path="SKILL.md",
                            file_manifest_sha256=file_digest,
                            file_entries=tuple(local_entries),
                        )
                        self.catalog.repository.save_skill(identity, skill)
                    except Exception:
                        for object_ref in copied_objects:
                            try:
                                self.object_store.delete(
                                    object_ref, identity.platform_id, identity.tenant_id
                                )
                            except (PermissionError, ObjectStoreError):
                                pass
                        raise
                    return
                skill = SkillVersion(
                    skill_id=import_record.local_resource_id,
                    tenant_id=identity.tenant_id,
                    name=name,
                    skill_type=SkillType(str(content["skill_type"])),
                    instruction=reference_marker,
                    allowed_tool_ids=tuple(item.local_resource_id for item in tool_bindings),
                    version=import_record.local_resource_version,
                    status=LifecycleStatus.PUBLISHED,
                    allowed_tool_versions=tuple(
                        (item.local_resource_id, item.local_resource_version)
                        for item in tool_bindings
                    ),
                    content_format=SkillContentFormat.FILE_PACKAGE,
                    entry_path="SKILL.md",
                    file_manifest_sha256=file_digest,
                    file_entries=(),
                )
                self.catalog.repository.save_skill(identity, skill)
                return
            skill = SkillVersion(
                skill_id=import_record.local_resource_id,
                tenant_id=identity.tenant_id,
                name=name,
                skill_type=SkillType(str(content["skill_type"])),
                instruction=(
                    str(content["instruction"])
                    if copied
                    else reference_marker + "/SKILL.md"
                ),
                allowed_tool_ids=tuple(item.local_resource_id for item in tool_bindings),
                input_schema=(content.get("input_schema", {"type": "object"}) if copied else {"type": "object"}),
                output_schema=(content.get("output_schema", {"type": "object"}) if copied else {"type": "object"}),
                trigger=(
                    str(content["trigger_rule"])
                    if copied and content.get("trigger_rule") is not None
                    else None
                ),
                workflow_steps=(
                    tuple(str(step) for step in content.get("workflow_steps", [])) if copied else ()
                ),
                memory_policy=str(content.get("memory_policy", "none")) if copied else "none",
                approval_policy=str(content.get("approval_policy", "none")) if copied else "none",
                version=import_record.local_resource_version,
                status=local_status,
                allowed_tool_versions=tuple(
                    (item.local_resource_id, item.local_resource_version) for item in tool_bindings
                ),
            )
            self.catalog.repository.save_skill(identity, skill)
            return

        if import_record.local_resource_type is HubResourceType.DIGITAL_EMPLOYEE:
            skill_bindings = [
                imported
                for dependency, imported in imported_dependencies
                if dependency["type"] == HubResourceType.SKILL.value
            ]
            tool_bindings = [
                imported
                for dependency, imported in imported_dependencies
                if dependency["type"] == HubResourceType.TOOL.value
            ]
            self.catalog.repository.save_employee(
                identity,
                DigitalEmployee(
                    employee_id=import_record.local_resource_id,
                    tenant_id=identity.tenant_id,
                    name=name,
                    role_name=str(content.get("role_name", "数字员工")) if copied else "共享数字员工",
                    description=str(content.get("description", "")) if copied else "只读资源中心引用",
                    status=LifecycleStatus.DRAFT if copied else LifecycleStatus.ACTIVE,
                ),
            )
            skill_refs = tuple((item.local_resource_id, item.local_resource_version) for item in skill_bindings)
            tool_refs = tuple((item.local_resource_id, item.local_resource_version) for item in tool_bindings)
            self.catalog.repository.save_employee_version(
                identity,
                DigitalEmployeeVersion(
                    employee_id=import_record.local_resource_id,
                    tenant_id=identity.tenant_id,
                    version=import_record.local_resource_version,
                    system_prompt=(
                        str(content.get("system_prompt", ""))
                        if copied
                        else reference_marker + "/employee"
                    ),
                    skill_version_ids=tuple(resource_id for resource_id, _ in skill_refs),
                    tool_version_ids=tuple(resource_id for resource_id, _ in tool_refs),
                    status=local_status,
                    skill_version_refs=skill_refs,
                    tool_version_refs=tool_refs,
                ),
            )
            return
        raise DomainError("INVALID_ARGUMENT", "不支持 COPY 此 Hub 资源类型")

    def list_discoverable(
        self,
        identity: IdentityContext,
        *,
        resource_type: str | None = None,
        keyword: str | None = None,
        mode: HubImportMode | str | None = None,
        owned_only: bool = False,
        limit: int = 100,
    ) -> tuple[HubResourceSummary, ...]:
        summaries = self.repository.list_discoverable(
            identity,
            resource_type=resource_type,
            keyword=keyword,
            limit=limit,
        )
        if owned_only:
            summaries = tuple(item for item in summaries if item.is_publisher)
        if mode is None:
            return summaries
        selected_mode = HubImportMode(mode)
        return tuple(item for item in summaries if selected_mode in item.available_modes)

    def get_discoverable_version(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> HubResourceDetail:
        return self.repository.get_discoverable_version(identity, hub_resource_id, version)

    def create_grant(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        recipient_tenant_id: str,
        *,
        copy_allowed: bool,
        reference_allowed: bool,
        expires_at: datetime | None = None,
    ) -> HubGrant:
        """发布租户管理员授予同平台接收租户指定模式的精确版本导入权。"""

        self._require_admin(identity)
        self.repository.get_owned_resource(identity, hub_resource_id)
        if not copy_allowed and not reference_allowed:
            raise DomainError("INVALID_ARGUMENT", "Grant 必须允许 COPY 或 REFERENCE")
        if expires_at is not None and (expires_at.tzinfo is None or expires_at <= datetime.now(UTC)):
            raise DomainError("INVALID_ARGUMENT", "Grant 失效时间必须带时区并且晚于当前时间")
        grant = HubGrant(
            grant_id=generate_database_id(),
            platform_id=identity.platform_id,
            hub_resource_id=hub_resource_id,
            hub_version=version,
            publisher_tenant_id=identity.tenant_id,
            recipient_tenant_id=recipient_tenant_id,
            copy_allowed=copy_allowed,
            reference_allowed=reference_allowed,
            created_by=identity.user_id,
            expires_at=expires_at,
        )
        self.repository.save_grant(identity, grant)
        return grant

    def list_grants(
        self, identity: IdentityContext, hub_resource_id: str, version: int
    ) -> tuple[HubGrant, ...]:
        self._require_admin(identity)
        self.repository.get_owned_resource(identity, hub_resource_id)
        return self.repository.list_grants(identity, hub_resource_id, version)

    def get_authorized_manifest(
        self,
        identity: IdentityContext,
        hub_resource_id: str,
        version: int,
        mode: HubImportMode | str,
    ) -> HubResourceVersion:
        """Grant 检查后返回固定版本 Manifest，供资源详情页展示依赖和文件清单。"""

        return self.repository.get_authorized_version(
            identity, hub_resource_id, version, HubImportMode(mode)
        )
