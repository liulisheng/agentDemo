from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import (
    LifecycleStatus,
    SkillContentFormat,
    SkillType,
    SkillVersion,
    ToolVersion,
)
from agent_platform.domain.common import DomainError
from agent_platform.domain.resource_center import (
    HubImportMode,
    HubResourceType,
    HubResourceVersion,
    HubVersionStatus,
    TenantImportStatus,
    TenantResourceImport,
    build_hub_manifest,
)


class StubResourceCenterRepository:
    def __init__(self, imported: TenantResourceImport, package: HubResourceVersion | None) -> None:
        self.imported = imported
        self.package = package

    def get_import_for_local_resource(self, identity, resource_type, local_resource_id, local_resource_version):
        if (
            resource_type is self.imported.local_resource_type
            and local_resource_id == self.imported.local_resource_id
            and local_resource_version == self.imported.local_resource_version
        ):
            return self.imported
        return None

    def get_authorized_version(self, identity, hub_resource_id, version, mode):
        if self.package is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Grant 已撤销")
        return self.package


def _reference_fixture(*, manifest_digest: str | None = None, content_format: str = "INLINE"):
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    skill_id = "local-reference-skill"
    hub_id = "hub-reference-skill"
    content = {
        "content_format": content_format,
        "instruction": "入口指令" if content_format == "INLINE" else None,
        "file_manifest_sha256": "f" * 64,
    }
    manifest = build_hub_manifest(
        resource_type="SKILL",
        name="引用技能",
        version=1,
        content=content,
    )
    package = HubResourceVersion(
        platform_id=identity.platform_id,
        hub_resource_id=hub_id,
        version=1,
        publisher_tenant_id="tenant-source",
        source_resource_type=HubResourceType.SKILL,
        source_resource_id="source-skill",
        source_resource_version=1,
        manifest=manifest,
        artifact_asset_id=None,
        created_by="source-admin",
        status=HubVersionStatus.PUBLISHED,
    )
    imported = TenantResourceImport(
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        import_id="import-1",
        grant_id="grant-1",
        hub_resource_id=hub_id,
        hub_version=1,
        mode=HubImportMode.REFERENCE,
        local_resource_type=HubResourceType.SKILL,
        local_resource_id=skill_id,
        local_resource_version=1,
        manifest_sha256=manifest_digest or manifest.sha256,
        imported_by=identity.user_id,
        status=TenantImportStatus.ACTIVE,
    )
    skill = SkillVersion(
        skill_id=skill_id,
        tenant_id=identity.tenant_id,
        name="引用技能代理",
        skill_type=SkillType.INSTRUCTION,
        instruction=(
            "hub-reference://hub-reference-skill/SKILL.md"
            if content_format == "INLINE"
            else "hub-reference://hub-reference-skill"
        ),
        status=LifecycleStatus.PUBLISHED,
        content_format=(SkillContentFormat.FILE_PACKAGE if content_format == "FILE_PACKAGE" else SkillContentFormat.INLINE),
        entry_path="SKILL.md" if content_format == "FILE_PACKAGE" else None,
        file_manifest_sha256=("e" * 64 if content_format == "FILE_PACKAGE" else None),
        file_entries=(),
    )
    return identity, package, imported, skill


def _draft_employee(identity, repository, skill):
    repository.save_skill(identity, skill)
    service = CatalogService(repository)
    employee = service.create_employee(identity, "待发布助手", "客服", "预检员工")
    version = service.create_employee_version(identity, employee.employee_id, "处理请求", [skill.skill_id], [])
    return service, employee, version


def test_publish_preflight_reports_revoked_hub_grant() -> None:
    identity, _, imported, skill = _reference_fixture()
    repository = InMemoryCatalogRepository()
    service, employee, version = _draft_employee(identity, repository, skill)
    service.resource_center_repository = StubResourceCenterRepository(imported, package=None)

    with pytest.raises(DomainError) as error:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    assert error.value.code == "DEPENDENCY_VALIDATION_ERROR"
    assert {
        "kind": "hub_grant",
        "id": skill.skill_id,
        "hub_resource_id": imported.hub_resource_id,
        "reason": "GRANT_REVOKED_OR_EXPIRED",
    } in error.value.details["missing"]


def test_publish_preflight_reports_import_manifest_digest_mismatch() -> None:
    identity, package, imported, skill = _reference_fixture(content_format="FILE_PACKAGE")
    mismatched_import = replace(imported, manifest_sha256="0" * 64)
    repository = InMemoryCatalogRepository()
    service, employee, version = _draft_employee(identity, repository, skill)
    service.resource_center_repository = StubResourceCenterRepository(mismatched_import, package)

    with pytest.raises(DomainError) as error:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    missing = error.value.details["missing"]
    assert any(item["kind"] == "hub_manifest" and item["reason"] == "MANIFEST_DIGEST_MISMATCH" for item in missing)


def test_publish_preflight_reports_skill_file_manifest_mismatch() -> None:
    identity, package, imported, skill = _reference_fixture(content_format="FILE_PACKAGE")
    repository = InMemoryCatalogRepository()
    service, employee, version = _draft_employee(identity, repository, skill)
    service.resource_center_repository = StubResourceCenterRepository(imported, package)

    with pytest.raises(DomainError) as error:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    missing = error.value.details["missing"]
    assert any(
        item["kind"] == "hub_manifest" and item["reason"] == "SKILL_FILE_MANIFEST_MISMATCH"
        for item in missing
    )


def test_publish_preflight_reports_missing_recipient_secret_reference() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    service = CatalogService(repository)
    skill = SkillVersion(
        "published-skill",
        identity.tenant_id,
        "已发布技能",
        SkillType.INSTRUCTION,
        "回答客户问题",
        status=LifecycleStatus.PUBLISHED,
    )
    repository.save_skill(identity, skill)
    tool = ToolVersion(
        "credential-tool",
        identity.tenant_id,
        "需要凭据的工具",
        "接收租户需绑定 SecretRef",
        {"type": "object"},
        status=LifecycleStatus.PUBLISHED,
        requires_recipient_credentials=True,
    )
    repository.save_tool(identity, tool)
    employee = service.create_employee(identity, "凭据助手", "客服", "依赖预检")
    version = service.create_employee_version(
        identity, employee.employee_id, "安全处理", [skill.skill_id], [tool.tool_id]
    )

    with pytest.raises(DomainError) as error:
        service.publish_employee_version(identity, employee.employee_id, version.version)

    assert {
        "kind": "recipient_credentials",
        "id": tool.tool_id,
        "reason": "SECRET_REF_MISSING",
    } in error.value.details["missing"]
