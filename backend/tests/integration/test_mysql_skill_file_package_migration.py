"""V005 Skill 文件包 schema/COMMENT/租户复合外键 disposable MySQL 验收。"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


def _has_chinese(value: str | None) -> bool:
    return bool(value and re.search(r"[\u3400-\u9fff]", value))


@pytest.fixture
def migrated_v005_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_skill_files_{secrets.token_hex(5)}"
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

        result = MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        yield database, config, result
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def test_v005_file_package_schema_has_chinese_comments_and_scoped_keys(migrated_v005_database) -> None:
    database, config, result = migrated_v005_database
    assert result.applied_migrations == ("V001", "V002", "V003", "V004", "V005")
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT table_comment FROM information_schema.tables "
            "WHERE table_schema=DATABASE() AND table_name='skill_file_entries'"
        )
        table_row = cursor.fetchone()
        assert table_row is not None and _has_chinese(table_row[0])

        cursor.execute(
            "SELECT column_name, column_comment FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='skill_file_entries'"
        )
        file_columns = dict(cursor.fetchall())
        assert {
            "platform_id", "tenant_id", "skill_id", "version", "file_path", "media_asset_id",
            "mime_type", "size_bytes", "content_sha256", "created_at",
        } <= set(file_columns)
        assert all(_has_chinese(comment) for comment in file_columns.values())

        cursor.execute(
            "SELECT column_name, column_comment, column_default FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name='skill_versions' "
            "AND column_name IN ('content_format','entry_path','file_manifest_sha256')"
        )
        skill_columns = {name: (comment, default) for name, comment, default in cursor.fetchall()}
        assert set(skill_columns) == {"content_format", "entry_path", "file_manifest_sha256"}
        assert all(_has_chinese(comment) for comment, _ in skill_columns.values())
        assert skill_columns["content_format"][1] == "INLINE"

        cursor.execute(
            "SELECT constraint_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage WHERE table_schema=DATABASE() "
            "AND table_name='skill_file_entries' AND referenced_table_name IS NOT NULL "
            "ORDER BY constraint_name, ordinal_position"
        )
        references: dict[str, list[tuple[str, str, str]]] = {}
        for constraint, column, target_table, target_column in cursor.fetchall():
            references.setdefault(str(constraint), []).append(
                (str(column), str(target_table), str(target_column))
            )
        assert references["fk_skill_file_version"] == [
            ("platform_id", "skill_versions", "platform_id"),
            ("tenant_id", "skill_versions", "tenant_id"),
            ("skill_id", "skill_versions", "skill_id"),
            ("version", "skill_versions", "version"),
        ]
        assert references["fk_skill_file_asset"] == [
            ("platform_id", "media_assets", "platform_id"),
            ("tenant_id", "media_assets", "tenant_id"),
            ("media_asset_id", "media_assets", "asset_id"),
        ]

        cursor.execute(
            "SELECT constraint_name FROM information_schema.check_constraints "
            "WHERE constraint_schema=DATABASE() AND constraint_name IN "
            "('ck_skill_content_format','ck_skill_file_package_metadata')"
        )
        assert {row[0] for row in cursor.fetchall()} == {
            "ck_skill_content_format", "ck_skill_file_package_metadata"
        }
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 5
    finally:
        cursor.close()
        connection.close()


def test_v005_down_removes_file_entries_and_returns_to_schema_four(migrated_v005_database) -> None:
    database, config, _ = migrated_v005_database
    script = (
        Path(__file__).parents[3] / "docs" / "migrations" / "V005__skill_file_packages.down.sql"
    ).read_text(encoding="utf-8")
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        for statement in script.split(";"):
            if statement.strip():
                cursor.execute(statement)
        connection.commit()
        cursor.execute("SELECT MAX(version) FROM schema_version")
        assert cursor.fetchone()[0] == 4
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name='skill_file_entries'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name='skill_versions' AND column_name IN "
            "('content_format','entry_path','file_manifest_sha256')"
        )
        assert cursor.fetchone()[0] == 0
    finally:
        cursor.close()
        connection.close()


def test_mysql_catalog_round_trips_file_package_metadata_and_entries(migrated_v005_database) -> None:
    import hashlib
    from dataclasses import replace

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.catalog import (
        SkillContentFormat,
        SkillFileEntry,
        SkillType,
        SkillVersion,
        compute_skill_file_manifest_sha256,
    )
    from agent_platform.domain.common import DomainError, generate_database_id
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository

    database, config, _ = migrated_v005_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = connection_factory()
    cursor = connection.cursor()
    file_content = "# 文件入口\n处理输入。".encode()
    file_digest = hashlib.sha256(file_content).hexdigest()
    asset_id = "asset-file-package-entry"
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s)",
            (identity.platform_id, "local", "本地平台", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
            (identity.platform_id, identity.tenant_id, identity.tenant_id, "文件包测试租户", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO media_assets "
            "(platform_id, tenant_id, asset_id, object_ref, filename, mime_type, size_bytes, "
            "content_sha256, retention_policy, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identity.platform_id,
                identity.tenant_id,
                asset_id,
                "local/test/file-package/SKILL.md",
                "SKILL.md",
                "text/markdown; charset=utf-8",
                len(file_content),
                file_digest,
                "conversation",
                "UPLOADED",
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    entry = SkillFileEntry(
        "SKILL.md",
        asset_id,
        "text/markdown; charset=utf-8",
        len(file_content),
        file_digest,
    )
    entries = (entry,)
    skill = SkillVersion(
        skill_id=generate_database_id(),
        tenant_id=identity.tenant_id,
        name="MySQL 文件 Skill",
        skill_type=SkillType.INSTRUCTION,
        instruction="",
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256=compute_skill_file_manifest_sha256(entries),
        file_entries=entries,
    )
    repository = MySqlCatalogRepository(connection_factory)
    repository.save_skill(identity, skill)
    loaded = repository.get_skill_version(identity, skill.skill_id, 1)

    assert loaded.content_format is SkillContentFormat.FILE_PACKAGE
    assert loaded.entry_path == "SKILL.md"
    assert loaded.file_manifest_sha256 == skill.file_manifest_sha256
    assert loaded.file_entries == entries

    changed = replace(loaded, name="并发修改后的文件 Skill")
    repository.save_skill(identity, changed)
    with pytest.raises(DomainError) as stale_write:
        repository.save_skill_if_revision_matches(
            identity,
            replace(loaded, name="预览后覆盖旧版本"),
            expected_revision=loaded,
        )
    assert stale_write.value.code == "IDEMPOTENCY_CONFLICT"
    assert repository.get_skill_version(identity, skill.skill_id, 1).name == changed.name


def test_mysql_zip_commit_uses_local_object_store_and_gc_protects_referenced_files(
    migrated_v005_database, tmp_path: Path
) -> None:
    import hashlib
    import io
    import zipfile

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.skill_files import SkillFilePackageService
    from agent_platform.domain.catalog import SkillContentFormat, SkillType
    from agent_platform.domain.common import DomainError
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config, _ = migrated_v005_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s)",
            (identity.platform_id, "local", "本地平台", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
            (identity.platform_id, identity.tenant_id, identity.tenant_id, "ZIP 测试租户", "ACTIVE"),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "本地 ZIP Skill", SkillType.INSTRUCTION, "旧正文", ())
    metadata = MySqlMediaAssetRepository(connection_factory)
    store = LocalObjectStore(tmp_path / "objects", metadata_repository=metadata)
    service = SkillFilePackageService(catalog, store)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "# 本地入口\n处理请求。")
        archive.writestr("references/guide.txt", "辅助说明。")
    package = stream.getvalue()
    preview = service.preview_zip(identity, skill.skill_id, 1, package)
    assert preview.file_count == 2
    updated = service.commit_zip(identity, skill.skill_id, 1, preview.staging_id, package)
    assert updated.content_format is SkillContentFormat.FILE_PACKAGE
    loaded = catalog.repository.get_skill_version(identity, skill.skill_id, 1)
    assert loaded.file_manifest_sha256 == preview.manifest_sha256

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        for entry in loaded.file_entries:
            cursor.execute(
                "SELECT object_ref FROM media_assets WHERE platform_id=%s AND tenant_id=%s AND asset_id=%s",
                (identity.platform_id, identity.tenant_id, entry.media_asset_id),
            )
            object_ref = str(cursor.fetchone()[0])
            assert metadata.is_object_protected(identity.platform_id, identity.tenant_id, object_ref)
            assert hashlib.sha256(
                store.read(object_ref, identity.platform_id, identity.tenant_id, max_bytes=1024)
            ).hexdigest() == entry.content_sha256
    finally:
        cursor.close()
        connection.close()

    guide = service.create_file(identity, skill.skill_id, 1, "guide.txt", b"guide v1")
    guide_entry = next(item for item in guide.file_entries if item.file_path == "guide.txt")
    with pytest.raises(DomainError) as stale_edit:
        service.update_file(
            identity,
            skill.skill_id,
            1,
            "guide.txt",
            b"stale guide",
            expected_sha256="0" * 64,
        )
    assert stale_edit.value.code == "IDEMPOTENCY_CONFLICT"
    guide = service.update_file(
        identity,
        skill.skill_id,
        1,
        "guide.txt",
        b"guide v2",
        expected_sha256=guide_entry.content_sha256,
    )
    guide_entry = next(item for item in guide.file_entries if item.file_path == "guide.txt")
    guide = service.rename_file(
        identity,
        skill.skill_id,
        1,
        "guide.txt",
        "references/another.txt",
        expected_sha256=guide_entry.content_sha256,
    )
    guide_entry = next(item for item in guide.file_entries if item.file_path == "references/another.txt")
    guide = service.delete_file(
        identity,
        skill.skill_id,
        1,
        "references/another.txt",
        expected_sha256=guide_entry.content_sha256,
    )
    assert [entry.file_path for entry in guide.file_entries] == ["SKILL.md", "references/guide.txt"]

    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape.txt", "bad")
        archive.writestr("SKILL.md", "entry")
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM media_assets WHERE platform_id=%s AND tenant_id=%s",
            (identity.platform_id, identity.tenant_id),
        )
        before = int(cursor.fetchone()[0])
    finally:
        cursor.close()
        connection.close()
    with pytest.raises(DomainError):
        service.preview_zip(identity, skill.skill_id, 1, malicious.getvalue())
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM media_assets WHERE platform_id=%s AND tenant_id=%s",
            (identity.platform_id, identity.tenant_id),
        )
        assert int(cursor.fetchone()[0]) == before
    finally:
        cursor.close()
        connection.close()


def test_mysql_zip_commit_rolls_back_manifest_when_file_asset_fk_fails(
    migrated_v005_database, tmp_path: Path
) -> None:
    import io
    import zipfile
    from dataclasses import replace

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.skill_files import SkillFilePackageService
    from agent_platform.domain.catalog import SkillContentFormat, SkillType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config, _ = migrated_v005_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s)",
            (identity.platform_id, "local", "本地平台", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
            (identity.platform_id, identity.tenant_id, identity.tenant_id, "ZIP 失败测试租户", "ACTIVE"),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "事务 ZIP Skill", SkillType.INSTRUCTION, "旧正文", ())
    store = LocalObjectStore(
        tmp_path / "objects", metadata_repository=MySqlMediaAssetRepository(connection_factory)
    )

    class InvalidSecondAssetStore:
        def __init__(self) -> None:
            self.completed = 0

        def create_upload_intent(self, request):
            return store.create_upload_intent(request)

        def complete_upload(self, upload_id: str, content: bytes):
            self.completed += 1
            asset = store.complete_upload(upload_id, content)
            if self.completed == 2:
                return replace(asset, asset_id="missing-media-asset")
            return asset

        def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
            store.delete(object_ref, platform_id, tenant_id)

    service = SkillFilePackageService(catalog, InvalidSecondAssetStore())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "入口")
        archive.writestr("guide.txt", "辅助文件")
    package = stream.getvalue()
    preview = service.preview_zip(identity, skill.skill_id, 1, package)

    with pytest.raises(mysql.IntegrityError):
        service.commit_zip(identity, skill.skill_id, 1, preview.staging_id, package)

    current = catalog.repository.get_skill_version(identity, skill.skill_id, 1)
    assert current.content_format is SkillContentFormat.INLINE
    assert current.file_entries == ()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM skill_file_entries WHERE platform_id=%s AND tenant_id=%s AND skill_id=%s",
            (identity.platform_id, identity.tenant_id, skill.skill_id),
        )
        assert cursor.fetchone()[0] == 0
    finally:
        cursor.close()
        connection.close()


def test_mysql_run_snapshot_and_trace_pin_file_package_refs(migrated_v005_database, tmp_path: Path) -> None:
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.api.app import create_app
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.skill_files import SkillFilePackageService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config, _ = migrated_v005_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s,%s,%s,%s)",
            (identity.platform_id, "local", "本地平台", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s,%s,%s,%s,%s)",
            (identity.platform_id, identity.tenant_id, identity.tenant_id, "Run 文件包租户", "ACTIVE"),
        )
        cursor.execute(
            "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (identity.platform_id, identity.tenant_id, identity.user_id, "subject-local", identity.user_id, "本地管理员", "ACTIVE"),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    catalog_repository = MySqlCatalogRepository(connection_factory)
    catalog = CatalogService(catalog_repository)
    object_store = LocalObjectStore(
        tmp_path / "objects", metadata_repository=MySqlMediaAssetRepository(connection_factory)
    )
    skill = catalog.create_skill(identity, "Run 文件入口 Skill", SkillType.INSTRUCTION, "旧指令", ())
    package_service = SkillFilePackageService(catalog, object_store)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "固定入口：只读取这个说明。")
        archive.writestr("tools/sample.py", 'print("不得执行此代码")')
    archive = stream.getvalue()
    preview = package_service.preview_zip(identity, skill.skill_id, 1, archive)
    package_service.commit_zip(identity, skill.skill_id, 1, preview.staging_id, archive)
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "文件引用员工", "执行助手", "运行文件 Skill")
    employee_version = catalog.create_employee_version(
        identity, employee.employee_id, "员工系统提示", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    deployment = catalog.deploy(identity, employee.employee_id, employee_version.version)
    execution = MySqlExecutionRepository(connection_factory)
    client = TestClient(
        create_app(
            mode="fake",
            catalog_repository=catalog_repository,
            execution_repository=execution,
            object_store=object_store,
        )
    )
    headers = {"X-Fake-User": identity.user_id}
    session_response = client.post(
        "/api/v1/sessions", headers=headers, json={"deployment_id": deployment.deployment_id}
    )
    assert session_response.status_code == 201
    run_response = client.post(
        f"/api/v1/sessions/{session_response.json()['id']}/runs",
        headers=headers,
        json={"prompt": "请按入口工作"},
    )
    assert run_response.status_code == 201
    snapshot = run_response.json()["snapshot"]
    assert snapshot["skill_file_packages"][0]["manifest_sha256"] == preview.manifest_sha256
    assert snapshot["skill_file_packages"][0]["entry_path"] == "SKILL.md"
    assert [item["path"] for item in snapshot["skill_file_packages"][0]["files"]] == [
        "SKILL.md",
        "tools/sample.py",
    ]
    assert "不得执行此代码" not in run_response.json()["result"]
    assert "media_asset_id" not in run_response.text

    restarted_app = create_app(
        mode="fake",
        catalog_repository=catalog_repository,
        execution_repository=MySqlExecutionRepository(connection_factory),
        object_store=object_store,
    )
    trace_response = TestClient(restarted_app).get(
        f"/api/v1/runs/{run_response.json()['id']}/trace", headers=headers
    )
    assert trace_response.status_code == 200
    persisted_snapshot = trace_response.json()["run"]["snapshot"]["skill_file_packages"][0]
    assert persisted_snapshot["manifest_sha256"] == preview.manifest_sha256
    skill_span = next(
        span
        for span in trace_response.json()["spans"]
        if span["span_type"] == "skill"
    )
    assert skill_span["attributes"]["file_manifest_sha256"] == preview.manifest_sha256
