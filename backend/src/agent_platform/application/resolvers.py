"""将本地或 Hub 资源解析成一次 Run 使用的不可变执行快照。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from agent_platform.application.catalog_service import CatalogService
from agent_platform.domain.catalog import (
    Deployment,
    DigitalEmployeeVersion,
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillVersion,
    compute_skill_file_manifest_sha256,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.observability import (
    HubResourceSnapshot,
    RunSnapshot,
    SkillFilePackageSnapshot,
    SkillFileSnapshotEntry,
)
from agent_platform.domain.resource_center import (
    HubImportMode,
    HubResourceType,
    HubResourceVersion,
    TenantImportStatus,
    TenantResourceImport,
)
from agent_platform.ports.resource_center import ResourceCenterRepository
from agent_platform.ports.storage import ObjectStore, ObjectStoreError


@dataclass(frozen=True, slots=True)
class ResolvedAgent:
    """一次 Run 使用的员工指令、技能指令、工具名和审计快照。"""

    snapshot: RunSnapshot
    system_prompt: str
    skill_instructions: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    skill_file_packages: tuple[SkillFilePackageSnapshot, ...] = ()


class HubResourceResolver:
    """按租户本地导入映射解析 Hub 固定版本，并在每次新 Run 校验 Grant 与摘要。"""

    def __init__(self, repository: ResourceCenterRepository) -> None:
        self.repository = repository

    @staticmethod
    def _dependency_error(message: str, **details: object) -> DomainError:
        return DomainError("DEPENDENCY_VALIDATION_ERROR", message, details or None)

    @staticmethod
    def _canonical_digest(package: HubResourceVersion) -> str:
        canonical = json.dumps(
            package.manifest.data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def import_for_local(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        resource_id: str,
        version: int,
    ) -> TenantResourceImport | None:
        return self.repository.get_import_for_local_resource(
            identity, resource_type, resource_id, version
        )

    def resolve_import(
        self,
        identity: IdentityContext,
        imported: TenantResourceImport,
    ) -> tuple[HubResourceSnapshot, HubResourceVersion | None]:
        """COPY 只记录来源；REFERENCE 必须重新授权读取并校验不可变 Package。"""

        if imported.status is not TenantImportStatus.ACTIVE:
            raise self._dependency_error("Hub 导入映射已失效", import_id=imported.import_id)
        reference = HubResourceSnapshot(
            local_resource_type=imported.local_resource_type.value,
            local_resource_id=imported.local_resource_id,
            local_resource_version=imported.local_resource_version,
            hub_resource_id=imported.hub_resource_id,
            hub_version=imported.hub_version,
            manifest_sha256=imported.manifest_sha256,
            mode=imported.mode.value,
        )
        if imported.mode is HubImportMode.COPY:
            return reference, None
        try:
            package = self.repository.get_authorized_version(
                identity,
                imported.hub_resource_id,
                imported.hub_version,
                HubImportMode.REFERENCE,
            )
        except DomainError as error:
            if error.code == "RESOURCE_NOT_FOUND":
                raise self._dependency_error(
                    "Hub REFERENCE Grant 已撤销、过期或资源已不可用",
                    hub_resource_id=imported.hub_resource_id,
                    hub_version=imported.hub_version,
                ) from error
            raise
        if (
            package.manifest.sha256 != imported.manifest_sha256
            or self._canonical_digest(package) != imported.manifest_sha256
        ):
            raise self._dependency_error(
                "Hub REFERENCE Manifest 摘要与导入记录不一致",
                hub_resource_id=imported.hub_resource_id,
                hub_version=imported.hub_version,
            )
        return reference, package

    def package_for_local(
        self,
        identity: IdentityContext,
        resource_type: HubResourceType,
        resource_id: str,
        version: int,
    ) -> tuple[HubResourceSnapshot | None, HubResourceVersion | None]:
        imported = self.import_for_local(identity, resource_type, resource_id, version)
        if imported is None:
            return None, None
        return self.resolve_import(identity, imported)

    def dependency_imports(
        self,
        identity: IdentityContext,
        package: HubResourceVersion,
        expected_type: HubResourceType,
    ) -> tuple[TenantResourceImport, ...]:
        """确保父包声明的精确依赖仍有同模式的租户本地导入映射。"""

        dependencies = package.manifest.data.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise self._dependency_error("Hub Manifest 依赖列表格式非法")
        result: list[TenantResourceImport] = []
        for dependency in dependencies:
            if not isinstance(dependency, dict) or dependency.get("type") != expected_type.value:
                continue
            try:
                hub_id = str(dependency["resource_id"])
                hub_version = int(dependency["version"])
            except (KeyError, TypeError, ValueError) as error:
                raise self._dependency_error("Hub Manifest 依赖缺少固定版本") from error
            imported = self.repository.get_import(
                identity, hub_id, hub_version, HubImportMode.REFERENCE
            )
            if (
                imported is None
                or imported.status is not TenantImportStatus.ACTIVE
                or imported.local_resource_type is not expected_type
            ):
                raise self._dependency_error(
                    "Hub REFERENCE 依赖缺少有效的租户本地映射",
                    hub_resource_id=hub_id,
                    hub_version=hub_version,
                )
            result.append(imported)
        return tuple(result)


class SkillResolver:
    """兼容独立 Skill 查询调用方，并按员工版本固定的 ID/版本解析。"""

    def __init__(self, catalog: CatalogService) -> None:
        self.catalog = catalog

    def resolve(
        self, identity: IdentityContext, employee_version: DigitalEmployeeVersion
    ) -> tuple[SkillVersion, ...]:
        refs = employee_version.skill_version_refs or tuple(
            (skill_id, 1) for skill_id in employee_version.skill_version_ids
        )
        resolved: list[SkillVersion] = []
        for skill_id, version in refs:
            skill = self.catalog.repository.get_skill_version(identity, skill_id, version)
            if skill.status is not LifecycleStatus.PUBLISHED:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "运行只能使用已发布 Skill")
            resolved.append(skill)
        return tuple(resolved)


class AgentResolver:
    """将 Deployment 和本地/Hub 资源固定为可执行 RunSnapshot。"""

    def __init__(
        self,
        catalog: CatalogService,
        resource_center_repository: ResourceCenterRepository | None = None,
        object_store: ObjectStore | None = None,
    ) -> None:
        self.catalog = catalog
        self.object_store = object_store
        self.hub = (
            HubResourceResolver(resource_center_repository)
            if resource_center_repository is not None
            else None
        )

    @staticmethod
    def _reference_package(
        resource_type: HubResourceType,
        package: HubResourceVersion | None,
    ) -> dict[str, object] | None:
        if package is None:
            return None
        if package.source_resource_type is not resource_type:
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub Package 类型与租户代理不匹配")
        content = package.manifest.data.get("content")
        if not isinstance(content, dict):
            raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub Package 内容格式非法")
        return content

    def resolve(self, identity: IdentityContext, deployment: Deployment) -> ResolvedAgent:
        version = deployment.employee_version
        profile = self.catalog.repository.get_profile_version(
            identity, version.execution_profile_id, version.execution_profile_version
        )
        source_refs: list[HubResourceSnapshot] = []
        imported_content: dict[tuple[HubResourceType, str, int], dict[str, object]] = {}

        if self.hub is not None:
            root_ref, root_package = self.hub.package_for_local(
                identity,
                HubResourceType.DIGITAL_EMPLOYEE,
                version.employee_id,
                version.version,
            )
            if root_ref is not None:
                source_refs.append(root_ref)
            root_content = self._reference_package(
                HubResourceType.DIGITAL_EMPLOYEE, root_package
            )
            if root_content is not None:
                imported_content[(HubResourceType.DIGITAL_EMPLOYEE, version.employee_id, version.version)] = root_content
                if self.hub is None:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub Resolver 未配置")
                source_refs.extend(
                    self._validate_parent_bindings(
                        identity,
                        root_package,
                        HubResourceType.DIGITAL_EMPLOYEE,
                        version.skill_version_refs,
                        version.tool_version_refs,
                    )
                )

        system_prompt = version.system_prompt
        root_content = imported_content.get(
            (HubResourceType.DIGITAL_EMPLOYEE, version.employee_id, version.version)
        )
        if root_content is not None:
            system_prompt_value = root_content.get("system_prompt")
            if not isinstance(system_prompt_value, str) or not system_prompt_value.strip():
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE 员工缺少系统指令")
            system_prompt = system_prompt_value
        elif system_prompt.startswith("hub-reference://"):
            raise DomainError(
                "DEPENDENCY_VALIDATION_ERROR",
                "Hub REFERENCE 数字员工需要配置 HubResolver 才能运行",
                {"employee_id": version.employee_id},
            )

        resolved_skills: list[SkillVersion] = []
        skill_import_refs: dict[tuple[str, int], HubResourceSnapshot] = {}
        reference_skill_packages: dict[tuple[str, int], HubResourceVersion] = {}
        for skill_id, skill_version in version.skill_version_refs:
            skill = self.catalog.repository.get_skill_version(identity, skill_id, skill_version)
            skill_ref, skill_package = (
                self.hub.package_for_local(identity, HubResourceType.SKILL, skill_id, skill_version)
                if self.hub is not None
                else (None, None)
            )
            if skill_ref is not None:
                source_refs.append(skill_ref)
                skill_import_refs[(skill_id, skill_version)] = skill_ref
            if skill_package is not None:
                source_refs.extend(
                    self._validate_parent_bindings(
                        identity,
                        skill_package,
                        HubResourceType.SKILL,
                        (),
                        skill.allowed_tool_versions,
                    )
                )
                content = self._reference_package(HubResourceType.SKILL, skill_package)
                assert content is not None
                imported_content[(HubResourceType.SKILL, skill_id, skill_version)] = content
                content_format = SkillContentFormat(str(content.get("content_format", "INLINE")))
                if content_format is SkillContentFormat.INLINE:
                    instruction = content.get("instruction")
                    if not isinstance(instruction, str) or not instruction.strip():
                        raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 缺少入口指令")
                    skill = replace(skill, instruction=instruction)
                else:
                    entry_path = content.get("entry_path")
                    file_digest = content.get("file_manifest_sha256")
                    if entry_path != "SKILL.md" or not isinstance(file_digest, str):
                        raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 文件清单格式非法")
                    reference_skill_packages[(skill_id, skill_version)] = skill_package
                    skill = replace(
                        skill,
                        instruction=f"hub-reference://{skill_package.hub_resource_id}/versions/{skill_package.version}",
                        content_format=SkillContentFormat.FILE_PACKAGE,
                        entry_path="SKILL.md",
                        file_manifest_sha256=file_digest,
                        file_entries=(),
                    )
            elif skill.instruction.startswith("hub-reference://"):
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 来源映射缺失")
            if skill.status is not LifecycleStatus.PUBLISHED:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "运行只能使用已发布 Skill")
            resolved_skills.append(skill)

        skill_instructions: list[str] = []
        skill_file_packages: list[SkillFilePackageSnapshot] = []
        for skill in resolved_skills:
            if skill.content_format is not SkillContentFormat.FILE_PACKAGE:
                skill_instructions.append(skill.instruction)
                continue
            if self.object_store is None:
                raise DomainError("INVALID_STATE", "FILE_PACKAGE Skill 运行需要配置对象存储")
            if skill.entry_path != "SKILL.md" or not skill.file_manifest_sha256:
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "FILE_PACKAGE Skill 入口或摘要缺失")
            package_key = (skill.skill_id, skill.version)
            hub_package = reference_skill_packages.get(package_key)
            if hub_package is None:
                local_entries = skill.file_entries
                if compute_skill_file_manifest_sha256(local_entries) != skill.file_manifest_sha256:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "FILE_PACKAGE Skill 清单摘要不匹配")
                file_descriptors = tuple(
                    (
                        entry.file_path,
                        entry.media_asset_id,
                        entry.mime_type,
                        entry.size_bytes,
                        entry.content_sha256,
                        identity.platform_id,
                        identity.tenant_id,
                        skill_import_refs.get(package_key).mode
                        if skill_import_refs.get(package_key) is not None
                        else "LOCAL",
                    )
                    for entry in local_entries
                )
                entry_path = skill.entry_path
                manifest_sha256 = skill.file_manifest_sha256
            else:
                content = imported_content[(HubResourceType.SKILL, skill.skill_id, skill.version)]
                raw_files = content.get("files")
                if not isinstance(raw_files, list):
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 缺少文件清单")
                try:
                    parsed_entries = tuple(
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
                manifest_sha256 = str(content.get("file_manifest_sha256", ""))
                if compute_skill_file_manifest_sha256(parsed_entries) != manifest_sha256:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Skill 清单摘要不匹配")
                file_descriptors = tuple(
                    (
                        entry.file_path,
                        entry.media_asset_id,
                        entry.mime_type,
                        entry.size_bytes,
                        entry.content_sha256,
                        hub_package.platform_id,
                        hub_package.publisher_tenant_id,
                        HubImportMode.REFERENCE.value,
                    )
                    for entry in parsed_entries
                )
                entry_path = str(content.get("entry_path"))
            entry_instruction: str | None = None
            file_refs: list[SkillFileSnapshotEntry] = []
            for (
                file_path,
                media_asset_id,
                mime_type,
                size_bytes,
                content_sha256,
                asset_platform_id,
                asset_tenant_id,
                source_mode,
            ) in file_descriptors:
                try:
                    content = self.object_store.read_asset(
                        media_asset_id,
                        asset_platform_id,
                        asset_tenant_id,
                        max_bytes=10 * 1024 * 1024,
                    )
                except (PermissionError, ObjectStoreError) as error:
                    raise DomainError(
                        "DEPENDENCY_VALIDATION_ERROR",
                        "FILE_PACKAGE Skill 对象缺失或越租户",
                        {"skill_id": skill.skill_id, "file_path": file_path},
                    ) from error
                if (
                    len(content) != size_bytes
                    or hashlib.sha256(content).hexdigest() != content_sha256
                ):
                    raise DomainError(
                        "DEPENDENCY_VALIDATION_ERROR",
                        "FILE_PACKAGE Skill 文件摘要不匹配",
                        {"skill_id": skill.skill_id, "file_path": file_path},
                    )
                file_refs.append(
                    SkillFileSnapshotEntry(
                        file_path,
                        media_asset_id,
                        mime_type,
                        size_bytes,
                        content_sha256,
                        asset_platform_id,
                        asset_tenant_id,
                        source_mode,
                    )
                )
                if file_path == entry_path:
                    try:
                        entry_instruction = content.decode("utf-8-sig")
                    except UnicodeDecodeError as error:
                        raise DomainError(
                            "DEPENDENCY_VALIDATION_ERROR",
                            "FILE_PACKAGE 根 SKILL.md 不是 UTF-8 文本",
                            {"skill_id": skill.skill_id},
                        ) from error
            if not entry_instruction or not entry_instruction.strip():
                raise DomainError("DEPENDENCY_VALIDATION_ERROR", "FILE_PACKAGE 根 SKILL.md 入口内容为空")
            skill_instructions.append(entry_instruction)
            skill_file_packages.append(
                SkillFilePackageSnapshot(
                    skill_id=skill.skill_id,
                    skill_version=skill.version,
                    entry_path=entry_path,
                    manifest_sha256=manifest_sha256,
                    files=tuple(file_refs),
                )
            )

        tool_names: dict[str, str] = {}
        resolved_tool_refs: list[tuple[str, int]] = []
        employee_tool_refs = version.tool_version_refs or tuple(
            (tool_id, 1) for tool_id in version.tool_version_ids
        )
        for tool_id, tool_version in employee_tool_refs:
            tool = self.catalog.repository.get_tool_version(identity, tool_id, tool_version)
            tool_ref, tool_package = (
                self.hub.package_for_local(identity, HubResourceType.TOOL, tool_id, tool_version)
                if self.hub is not None
                else (None, None)
            )
            if tool_ref is not None:
                source_refs.append(tool_ref)
            if tool_package is not None:
                content = self._reference_package(HubResourceType.TOOL, tool_package)
                assert content is not None
                imported_content[(HubResourceType.TOOL, tool_id, tool_version)] = content
                resource = tool_package.manifest.data.get("resource")
                name = resource.get("name") if isinstance(resource, dict) else None
                if not isinstance(name, str) or not name.strip():
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Tool 缺少名称")
                if tool.handler_name == "hub-reference":
                    if tool.requires_recipient_credentials and tool.auth_secret_ref is None:
                        raise DomainError(
                            "DEPENDENCY_VALIDATION_ERROR",
                            "REFERENCE Tool 尚未绑定接收租户的凭据 SecretRef",
                            {"tool_id": tool_id},
                        )
                    tool_names[tool_id] = name
                else:
                    tool_names[tool_id] = name
            else:
                if tool.handler_name == "hub-reference":
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "Hub REFERENCE Tool 来源映射缺失")
                if tool.status is not LifecycleStatus.PUBLISHED:
                    raise DomainError("DEPENDENCY_VALIDATION_ERROR", "运行只能使用已发布 Tool")
                if tool.requires_recipient_credentials and tool.auth_secret_ref is None:
                    raise DomainError(
                        "DEPENDENCY_VALIDATION_ERROR",
                        "Tool 尚未绑定接收租户的凭据 SecretRef",
                        {"tool_id": tool_id},
                    )
                tool_names[tool_id] = tool.name
            resolved_tool_refs.append((tool_id, tool_version))

        declared_tool_ids = {
            tool_id for skill in resolved_skills for tool_id in skill.allowed_tool_ids
        }
        employee_tool_ids = {tool_id for tool_id, _ in employee_tool_refs}
        selected_ids = declared_tool_ids.intersection(employee_tool_ids)
        selected_names = {
            tool_id: tool_names[tool_id] for tool_id in selected_ids if tool_id in tool_names
        }
        if profile.tool_policy:
            selected_names = {
                tool_id: name
                for tool_id, name in selected_names.items()
                if name in profile.tool_policy
            }

        # 同一资源可能同时由 Employee 和 Skill 声明；RunSnapshot 每个 Hub 版本只记录一次。
        unique_refs: dict[tuple[str, int, str], HubResourceSnapshot] = {}
        for reference in source_refs:
            unique_refs[(reference.hub_resource_id, reference.hub_version, reference.mode)] = reference
        snapshot = RunSnapshot(
            employee_id=version.employee_id,
            employee_version=version.version,
            skill_version_ids=tuple(skill.skill_id for skill in resolved_skills),
            tool_version_ids=tuple(tool_id for tool_id, _ in resolved_tool_refs),
            execution_profile_id=version.execution_profile_id,
            skill_version_refs=tuple(version.skill_version_refs),
            tool_version_refs=tuple(resolved_tool_refs),
            execution_profile_version=version.execution_profile_version,
            model_route_id=version.model_route_id or profile.model_route_id,
            hub_resource_refs=tuple(unique_refs.values()),
            skill_file_packages=tuple(skill_file_packages),
        )
        return ResolvedAgent(
            snapshot=snapshot,
            system_prompt=system_prompt,
            skill_instructions=tuple(skill_instructions),
            allowed_tools=tuple(selected_names.values()),
            skill_file_packages=tuple(skill_file_packages),
        )

    def _validate_parent_bindings(
        self,
        identity: IdentityContext,
        package: HubResourceVersion,
        parent_type: HubResourceType,
        skill_refs: tuple[tuple[str, int], ...],
        tool_refs: tuple[tuple[str, int], ...],
    ) -> tuple[HubResourceSnapshot, ...]:
        """防止租户代理的本地绑定被改写后借用另一个 Hub 子资源。"""

        if self.hub is None:
            return ()
        resolved_refs: list[HubResourceSnapshot] = []
        expected_by_type = {
            HubResourceType.SKILL: skill_refs,
            HubResourceType.TOOL: tool_refs,
        }
        for dependency_type, expected_refs in expected_by_type.items():
            imports = self.hub.dependency_imports(identity, package, dependency_type)
            actual_refs = tuple(
                (item.local_resource_id, item.local_resource_version) for item in imports
            )
            if actual_refs != tuple(expected_refs):
                raise DomainError(
                    "DEPENDENCY_VALIDATION_ERROR",
                    "Hub REFERENCE 依赖与员工/Skill 本地绑定不一致",
                    {
                        "hub_resource_id": package.hub_resource_id,
                        "parent_type": parent_type.value,
                        "dependency_type": dependency_type.value,
                    },
                )
            for imported in imports:
                reference, _ = self.hub.resolve_import(identity, imported)
                resolved_refs.append(reference)
        return tuple(resolved_refs)

    def snapshot(self, identity: IdentityContext, deployment: Deployment) -> RunSnapshot:
        """兼容只需要快照的调用方；执行入口应使用 resolve 取得 Runtime 内容。"""

        return self.resolve(identity, deployment).snapshot
