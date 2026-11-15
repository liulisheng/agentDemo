"""Resource Center Repository 的目录发现、版本授权和撤销集成契约。"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_resource_center_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_hub_repo_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
    connection = mysql.connect(**config)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner

        MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute("INSERT INTO platforms (platform_id,code,name,status) VALUES ('plt-local','local','本地平台','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-a','tenant-a','发布方租户','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-b','tenant-b','接收方租户','ACTIVE')")
            cursor.execute("INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) VALUES ('plt-local','tenant-a','admin-local','subject-a','admin-local','发布管理员','ACTIVE')")
            cursor.execute("INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) VALUES ('plt-local','tenant-b','admin-other','subject-b','admin-other','接收管理员','ACTIVE')")
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        yield database, config
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def test_platform_catalog_discovery_does_not_grant_package_read(mysql_resource_center_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.resource_center import (
        HubImportMode,
        HubResource,
        HubResourceType,
        HubVisibility,
    )
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_database
    repository = MySqlResourceCenterRepository(lambda: mysql.connect(**config, database=database))
    owner = FakeIdentityProvider().resolve_identity("admin-local")
    recipient = FakeIdentityProvider().resolve_identity("admin-other")
    resource = HubResource(
        platform_id=owner.platform_id,
        hub_resource_id=generate_database_id(),
        resource_type=HubResourceType.SKILL,
        publisher_tenant_id=owner.tenant_id,
        publisher_user_id=owner.user_id,
        name="客服问答 Skill",
        description="共享的客服回复规则",
        visibility=HubVisibility.PLATFORM,
    )
    repository.save_resource(owner, resource)
    from agent_platform.domain.resource_center import build_hub_manifest

    manifest = build_hub_manifest(
        resource_type="SKILL",
        name=resource.name,
        version=1,
        content={"instruction": "回答客服问题"},
    )
    from agent_platform.domain.resource_center import HubResourceVersion

    version = HubResourceVersion(
        platform_id=owner.platform_id,
        hub_resource_id=resource.hub_resource_id,
        version=1,
        publisher_tenant_id=owner.tenant_id,
        source_resource_type=HubResourceType.SKILL,
        source_resource_id=generate_database_id(),
        source_resource_version=2,
        manifest=manifest,
        artifact_asset_id=None,
        created_by=owner.user_id,
    )
    repository.save_version(owner, version)
    repository.publish_version(owner, resource.hub_resource_id, 1)

    discovered = repository.list_discoverable(recipient)
    assert len(discovered) == 1
    assert discovered[0].name == resource.name
    assert discovered[0].publisher_name == "发布方租户"
    assert discovered[0].available_modes == ()
    assert not hasattr(discovered[0], "publisher_tenant_id")
    detail = repository.get_discoverable_version(recipient, resource.hub_resource_id, 1)
    assert detail.name == resource.name
    assert detail.manifest_sha256 == manifest.sha256
    assert detail.dependency_count == 0
    assert not hasattr(detail, "publisher_tenant_id")
    assert not hasattr(detail, "manifest_json")
    with pytest.raises(DomainError) as no_grant:
        repository.get_authorized_version(recipient, resource.hub_resource_id, 1, HubImportMode.COPY)
    assert no_grant.value.code == "RESOURCE_NOT_FOUND"


def test_copy_and_reference_grants_are_versioned_and_revocable(mysql_resource_center_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.domain.resource_center import (
        HubGrant,
        HubImportMode,
        HubResource,
        HubResourceType,
        HubVisibility,
    )
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_database
    repository = MySqlResourceCenterRepository(lambda: mysql.connect(**config, database=database))
    owner = FakeIdentityProvider().resolve_identity("admin-local")
    recipient = FakeIdentityProvider().resolve_identity("admin-other")
    resource = HubResource(
        owner.platform_id,
        generate_database_id(),
        HubResourceType.TOOL,
        owner.tenant_id,
        owner.user_id,
        "工单查询 Tool",
        "不含发布租户密钥",
        HubVisibility.PLATFORM,
    )
    repository.save_resource(owner, resource)
    from agent_platform.domain.resource_center import build_hub_manifest

    manifest = build_hub_manifest(
        resource_type="TOOL",
        name=resource.name,
        version=1,
        content={"protocol": "api", "input_schema": {"type": "object"}, "api_key": "secret"},
    )
    from agent_platform.domain.resource_center import HubResourceVersion

    repository.save_version(
        owner,
        HubResourceVersion(
            owner.platform_id,
            resource.hub_resource_id,
            1,
            owner.tenant_id,
            HubResourceType.TOOL,
            generate_database_id(),
            4,
            manifest,
            None,
            owner.user_id,
        ),
    )
    repository.publish_version(owner, resource.hub_resource_id, 1)
    copy_grant = HubGrant(
        generate_database_id(),
        owner.platform_id,
        resource.hub_resource_id,
        1,
        owner.tenant_id,
        recipient.tenant_id,
        copy_allowed=True,
        reference_allowed=False,
        created_by=owner.user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    repository.save_grant(owner, copy_grant)

    assert repository.get_authorized_version(
        recipient, resource.hub_resource_id, 1, HubImportMode.COPY
    ).manifest.sha256 == manifest.sha256
    with pytest.raises(DomainError):
        repository.get_authorized_version(recipient, resource.hub_resource_id, 1, HubImportMode.REFERENCE)

    repository.revoke_grant(owner, copy_grant.grant_id)
    with pytest.raises(DomainError) as revoked:
        repository.get_authorized_version(recipient, resource.hub_resource_id, 1, HubImportMode.COPY)
    assert revoked.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(DomainError):
        repository.save_grant(recipient, copy_grant)


def test_restricted_metadata_requires_grant_and_expired_grant_is_hidden(
    mysql_resource_center_database,
) -> None:
    from datetime import UTC, datetime, timedelta

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import generate_database_id
    from agent_platform.domain.resource_center import (
        HubGrant,
        HubImportMode,
        HubResource,
        HubResourceType,
        HubResourceVersion,
        HubVisibility,
        build_hub_manifest,
    )
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository

    database, config = mysql_resource_center_database
    repository = MySqlResourceCenterRepository(lambda: mysql.connect(**config, database=database))
    owner = FakeIdentityProvider().resolve_identity("admin-local")
    recipient = FakeIdentityProvider().resolve_identity("admin-other")
    resource = HubResource(
        owner.platform_id,
        generate_database_id(),
        HubResourceType.DIGITAL_EMPLOYEE,
        owner.tenant_id,
        owner.user_id,
        "定向员工包",
        "仅授权租户可发现",
        HubVisibility.RESTRICTED,
    )
    repository.save_resource(owner, resource)
    manifest = build_hub_manifest(
        resource_type="DIGITAL_EMPLOYEE",
        name=resource.name,
        version=1,
        content={"system_prompt": "请处理客户请求"},
    )
    repository.save_version(
        owner,
        HubResourceVersion(
            owner.platform_id,
            resource.hub_resource_id,
            1,
            owner.tenant_id,
            HubResourceType.DIGITAL_EMPLOYEE,
            generate_database_id(),
            1,
            manifest,
            None,
            owner.user_id,
        ),
    )
    repository.publish_version(owner, resource.hub_resource_id, 1)
    assert all(item.hub_resource_id != resource.hub_resource_id for item in repository.list_discoverable(recipient))

    repository.save_grant(
        owner,
        HubGrant(
            generate_database_id(),
            owner.platform_id,
            resource.hub_resource_id,
            1,
            owner.tenant_id,
            recipient.tenant_id,
            copy_allowed=False,
            reference_allowed=True,
            created_by=owner.user_id,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        ),
    )
    # 已过期授权既不使受限目录可发现，也不能放行资源包内容。
    assert all(item.hub_resource_id != resource.hub_resource_id for item in repository.list_discoverable(recipient))
    from agent_platform.domain.common import DomainError

    with pytest.raises(DomainError) as expired:
        repository.get_authorized_version(
            recipient, resource.hub_resource_id, 1, HubImportMode.REFERENCE
        )
    assert expired.value.code == "RESOURCE_NOT_FOUND"
