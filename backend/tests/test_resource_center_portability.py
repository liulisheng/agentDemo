from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.route_modules.resource_center import _hub_import_response
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.resource_center import ResourceCenterService
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.resource_center import (
    HubGrant,
    HubImportMode,
    HubResourceDependency,
    HubResourceType,
    HubResourceVersion,
    HubVersionStatus,
    build_hub_manifest,
)


def test_new_employee_package_contains_only_employee_content_and_capability_refs() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    skill = catalog.create_skill(identity, "共享技能", SkillType.INSTRUCTION, "只读查询", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "可移植员工", "分析员", "跨租户复用")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "按本租户运行默认处理。",
        [skill.skill_id],
        [],
    )
    # 旧版本可能残留发布方运行引用；发布和打包都不得要求或传播这些资源。
    legacy_version = replace(
        version,
        execution_profile_id="publisher-engine-profile",
        execution_profile_version=4,
        model_route_id="publisher-model-route",
        workspace_profile_id="publisher-workspace",
        workspace_profile_version=3,
        sandbox_profile_id="publisher-sandbox",
        sandbox_profile_version=2,
        approval_policy="publisher-approval-policy",
    )
    repository.save_employee_version(identity, legacy_version)
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    resource_center = ResourceCenterService(catalog, repository=None)

    def resolve_local_skill(_identity, _resource_type, source_id, source_version, sequence_no):
        dependency = HubResourceDependency(
            dependency_id="dep-skill-1",
            dependency_type=HubResourceType.SKILL,
            dependency_hub_resource_id="hub-skill-1",
            dependency_hub_version=1,
            sequence_no=sequence_no,
        )
        manifest_item = {
            "type": "SKILL",
            "resource_id": "hub-skill-1",
            "version": 1,
            "sequence_no": sequence_no,
            "source_resource_id": source_id,
            "source_resource_version": source_version,
        }
        return dependency, manifest_item

    resource_center._resolve_dependency = resolve_local_skill
    _name, _description, content, _dependencies, _manifest_dependencies = (
        resource_center._package_source(
            identity, HubResourceType.DIGITAL_EMPLOYEE, employee.employee_id, version.version
        )
    )

    assert set(content) == {"role_name", "description", "system_prompt"}
    serialized = json.dumps(content, ensure_ascii=False)
    assert "publisher-engine" not in serialized
    assert "publisher-only-model" not in serialized
    assert "publisher-engine-profile" not in serialized
    assert "publisher-model-route" not in serialized
    assert "publisher-workspace" not in serialized
    assert "publisher-sandbox" not in serialized
    assert "publisher-approval-policy" not in serialized
    assert repository.list_profiles(identity) == ()


class _LegacyEmployeePackageRepository:
    def __init__(self, package: HubResourceVersion, grant: HubGrant) -> None:
        self.package = package
        self.grant = grant
        self.import_record = None

    def get_authorized_version(self, _identity, _hub_resource_id, _version, _mode):
        return self.package

    def get_authorized_grant(self, _identity, _hub_resource_id, _version, _mode):
        return self.grant

    def get_import(self, _identity, _hub_resource_id, _version, _mode):
        return self.import_record

    def begin_import(self, _identity, value):
        self.import_record = value
        return value

    def complete_import(self, _identity, _import_id):
        self.import_record = replace(self.import_record, status="ACTIVE")
        return self.import_record

    def fail_import(self, _identity, _import_id):
        self.import_record = replace(self.import_record, status="FAILED")


@pytest.mark.parametrize("mode", [HubImportMode.COPY, HubImportMode.REFERENCE])
def test_legacy_employee_package_runtime_fields_are_ignored(mode: HubImportMode) -> None:
    recipient = FakeIdentityProvider().resolve_identity("admin-local")
    legacy_content = {
        "role_name": "旧岗位",
        "description": "旧说明",
        "system_prompt": "只保留员工指令",
        "execution_profile": {
            "engine_name": "publisher-private-engine",
            "model_alias": "publisher-private-model",
            "model_route_id": "publisher-private-route",
            "workspace_profile_id": "publisher-private-workspace",
            "sandbox_profile_id": "publisher-private-sandbox",
            "approval_policy": "publisher-private-policy",
            "secret_ref": "publisher-private-secret",
        },
    }
    manifest = build_hub_manifest(
        resource_type="DIGITAL_EMPLOYEE",
        name="旧版员工包",
        version=1,
        content=legacy_content,
    )
    package = HubResourceVersion(
        platform_id=recipient.platform_id,
        hub_resource_id="hub-old-employee",
        version=1,
        publisher_tenant_id="publisher-tenant",
        source_resource_type=HubResourceType.DIGITAL_EMPLOYEE,
        source_resource_id="publisher-employee",
        source_resource_version=4,
        manifest=manifest,
        artifact_asset_id=None,
        created_by="publisher-admin",
        status=HubVersionStatus.PUBLISHED,
    )
    grant = HubGrant(
        grant_id="grant-old-employee",
        platform_id=recipient.platform_id,
        hub_resource_id=package.hub_resource_id,
        hub_version=1,
        publisher_tenant_id=package.publisher_tenant_id,
        recipient_tenant_id=recipient.tenant_id,
        copy_allowed=True,
        reference_allowed=True,
        created_by="publisher-admin",
    )
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    hub = _LegacyEmployeePackageRepository(package, grant)
    service = ResourceCenterService(catalog, hub)

    imported = service.import_resource(recipient, package.hub_resource_id, 1, mode)
    import_response = _hub_import_response(imported)
    local_version = repository.get_employee_version(
        recipient, imported.local_resource_id, imported.local_resource_version
    )

    assert import_response["runtime_configuration_required"] is True
    assert import_response["next_action"] == "CONFIGURE_DEPLOYMENT_RUNTIME"
    assert local_version.system_prompt != ""
    assert local_version.execution_profile_id is None
    assert local_version.execution_profile_version is None
    assert local_version.model_route_id is None
    assert local_version.workspace_profile_id is None
    assert local_version.sandbox_profile_id is None
    assert repository.list_profiles(recipient) == ()
