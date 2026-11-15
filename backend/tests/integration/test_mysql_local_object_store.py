"""LocalObjectStore 的 MySQL 元数据、跨实例读取和租户授权集成测试。"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_storage_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_storage_test_{secrets.token_hex(5)}"
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
            cursor.execute("INSERT INTO platforms (platform_id,code,name,status) VALUES ('plt-local','local','本地','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-a','tenant-a','A','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-b','tenant-b','B','ACTIVE')")
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


def test_uploaded_object_and_authorization_survive_store_restart(tmp_path: Path, mysql_storage_database) -> None:
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore, UploadRequest

    database, config = mysql_storage_database
    repository_factory = lambda: MySqlMediaAssetRepository(
        lambda: mysql.connect(**config, database=database)
    )
    content = b"persistent local object"
    request = UploadRequest(
        "plt-local",
        "tenant-a",
        "../../display-only.txt",
        "text/plain",
        len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    first = LocalObjectStore(tmp_path / "objects", metadata_repository=repository_factory())
    intent = first.create_upload_intent(request)
    restarted_writer = LocalObjectStore(tmp_path / "objects", metadata_repository=repository_factory())
    asset = restarted_writer.complete_upload(intent.upload_id, content)
    assert list((tmp_path / "objects").iterdir()) == [tmp_path / "objects" / intent.object_ref.split(":")[-1]]

    restarted = LocalObjectStore(tmp_path / "objects", metadata_repository=repository_factory())
    assert restarted.read(asset.object_ref, "plt-local", "tenant-a") == content
    with pytest.raises(PermissionError):
        restarted.read(asset.object_ref, "plt-local", "tenant-b")

    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT tenant_id, size_bytes, content_sha256, retention_policy, status "
            "FROM media_assets WHERE platform_id=%s AND asset_id=%s",
            ("plt-local", asset.asset_id),
        )
        row = cursor.fetchone()
        assert row == ("tenant-a", len(content), hashlib.sha256(content).hexdigest(), "conversation", "UPLOADED")
    finally:
        cursor.close()
        connection.close()


def test_encrypted_local_object_uses_versioned_secret_and_rejects_missing_key(
    tmp_path: Path, mysql_storage_database
) -> None:
    import base64

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.application.raw_payload import LocalRawPayloadStore, RawPayloadPolicy
    from agent_platform.domain.common import DomainError
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import (
        LocalObjectStore,
        ObjectStoreError,
        SecretRef,
        UploadRequest,
    )
    from agent_platform.ports.workspaces import FakeSecretProvider

    database, config = mysql_storage_database
    repository_factory = lambda: MySqlMediaAssetRepository(
        lambda: mysql.connect(**config, database=database)
    )
    key = bytes(range(32))
    provider = FakeSecretProvider(
        {"secret/local-objects@v1": {"key_base64": base64.b64encode(key).decode("ascii")}}
    )
    root = tmp_path / "encrypted-objects"
    store = LocalObjectStore(
        root,
        metadata_repository=repository_factory(),
        require_encryption=True,
        encryption_secret_ref=SecretRef("secret/local-objects"),
        encryption_key_version="v1",
        key_provider=provider,
    )
    content = b"must be encrypted at rest"
    intent = store.create_upload_intent(
        UploadRequest("plt-local", "tenant-a", "secret.txt", "text/plain", len(content))
    )
    asset = store.complete_upload(intent.upload_id, content)
    encrypted_bytes = next(root.iterdir()).read_bytes()
    assert content not in encrypted_bytes
    assert asset.encryption_key_version == "v1"

    restarted = LocalObjectStore(
        root,
        metadata_repository=repository_factory(),
        require_encryption=True,
        encryption_secret_ref=SecretRef("secret/local-objects"),
        encryption_key_version="v1",
        key_provider=provider,
    )
    assert restarted.read(asset.object_ref, "plt-local", "tenant-a") == content
    assert base64.b64encode(key).decode("ascii") not in repr(asset)
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT encryption_key_version FROM media_assets WHERE platform_id=%s AND tenant_id=%s AND asset_id=%s",
            ("plt-local", "tenant-a", asset.asset_id),
        )
        assert cursor.fetchone()[0] == "v1"
    finally:
        cursor.close()
        connection.close()
    with pytest.raises(ObjectStoreError, match="encryption key"):
        LocalObjectStore(root, require_encryption=True).create_upload_intent(
            UploadRequest("plt-local", "tenant-a", "missing.txt", "text/plain", 1)
        )

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    payload_store = LocalRawPayloadStore(restarted)
    payload_ref = payload_store.save(identity, content, RawPayloadPolicy(enabled=True))
    assert payload_ref is not None and payload_ref.encrypted
    after_restart = LocalRawPayloadStore(
        LocalObjectStore(
            root,
            metadata_repository=repository_factory(),
            require_encryption=True,
            encryption_secret_ref=SecretRef("secret/local-objects"),
            encryption_key_version="v1",
            key_provider=provider,
        )
    )
    assert after_restart.read(identity, payload_ref.object_ref) == content
    with pytest.raises(DomainError) as cross_tenant:
        after_restart.read(other, payload_ref.object_ref)
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(DomainError):
        after_restart.delete(other, payload_ref.object_ref)
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT object_ref, filename, content_sha256, size_bytes, retention_policy, retention_expires_at "
            "FROM media_assets "
            "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
            (identity.platform_id, identity.tenant_id, payload_ref.object_ref),
        )
        metadata = cursor.fetchone()
        assert metadata is not None
        assert content not in repr(metadata).encode("utf-8")
        assert metadata[4] == "ephemeral" and metadata[5] is not None
        cursor.execute(
            "UPDATE media_assets SET retention_expires_at=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 1 SECOND) "
            "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
            (identity.platform_id, identity.tenant_id, payload_ref.object_ref),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert after_restart.object_store.collect_garbage() == 1
    with pytest.raises(DomainError):
        after_restart.read(identity, payload_ref.object_ref)


def test_upload_metadata_commit_failure_is_atomic_and_intent_can_retry(
    tmp_path: Path, mysql_storage_database
) -> None:
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore, UploadRequest

    database, config = mysql_storage_database
    connection_factory = lambda: mysql.connect(**config, database=database)

    def fail(stage: str) -> None:
        if stage == "after_asset_insert":
            raise RuntimeError("injected database commit failure")

    root = tmp_path / "atomic"
    failing_repository = MySqlMediaAssetRepository(connection_factory, failure_injector=fail)
    store = LocalObjectStore(root, metadata_repository=failing_repository)
    content = b"retry this object"
    intent = store.create_upload_intent(
        UploadRequest("plt-local", "tenant-a", "retry.txt", "text/plain", len(content))
    )
    with pytest.raises(RuntimeError, match="commit failure"):
        store.complete_upload(intent.upload_id, content)

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM media_assets WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
            ("plt-local", "tenant-a", intent.object_ref),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT status, CHAR_LENGTH(upload_id) FROM content_upload_intents "
            "WHERE platform_id=%s AND tenant_id=%s AND upload_id=%s",
            ("plt-local", "tenant-a", intent.upload_id),
        )
        assert cursor.fetchone() == ("UPLOADING", 26)
    finally:
        cursor.close()
        connection.close()

    retry = LocalObjectStore(root, metadata_repository=MySqlMediaAssetRepository(connection_factory))
    asset = retry.complete_upload(intent.upload_id, content)
    assert retry.read(asset.object_ref, "plt-local", "tenant-a") == content


def test_upload_checks_digest_size_and_expired_lease_before_publishing(
    tmp_path: Path, mysql_storage_database, monkeypatch
) -> None:
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore, ObjectStoreError, UploadRequest

    database, config = mysql_storage_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    repository = MySqlMediaAssetRepository(connection_factory)
    store = LocalObjectStore(tmp_path / "checks", metadata_repository=repository)
    content = b"valid"
    intent = store.create_upload_intent(
        UploadRequest(
            "plt-local",
            "tenant-a",
            "check.txt",
            "text/plain",
            len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
        )
    )
    original_path = store._path
    monkeypatch.setattr(store, "_path", lambda _: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.complete_upload(intent.upload_id, content)
    monkeypatch.setattr(store, "_path", original_path)
    with pytest.raises(ObjectStoreError, match="digest"):
        store.complete_upload(intent.upload_id, b"other")

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE content_upload_intents SET lease_expires_at=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 1 SECOND) "
            "WHERE platform_id=%s AND tenant_id=%s AND upload_id=%s",
            ("plt-local", "tenant-a", intent.upload_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    with pytest.raises(ObjectStoreError, match="not found"):
        LocalObjectStore(tmp_path / "checks", metadata_repository=MySqlMediaAssetRepository(connection_factory)).complete_upload(
            intent.upload_id, content
        )


def test_delete_revokes_read_retries_gc_and_requeues_reappeared_objects(
    tmp_path: Path, mysql_storage_database
) -> None:
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore, ObjectStoreError, UploadRequest

    database, config = mysql_storage_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    repository = MySqlMediaAssetRepository(connection_factory)
    root = tmp_path / "gc"
    store = LocalObjectStore(root, metadata_repository=repository)
    content = b"delete and retry"
    intent = store.create_upload_intent(
        UploadRequest("plt-local", "tenant-a", "gc.txt", "text/plain", len(content))
    )
    asset = store.complete_upload(intent.upload_id, content)
    path = store._path(asset.object_ref)
    backup = path.with_name("verified-content")
    path.rename(backup)
    path.mkdir()

    store.delete(asset.object_ref, "plt-local", "tenant-a")
    with pytest.raises(PermissionError):
        store.read(asset.object_ref, "plt-local", "tenant-a")
    assert store.collect_garbage() == 0

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT status, attempt_count FROM content_gc_queue "
            "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
            ("plt-local", "tenant-a", asset.object_ref),
        )
        assert cursor.fetchone() == ("RETRY", 1)
        cursor.execute(
            "UPDATE content_gc_queue SET next_attempt_at=CURRENT_TIMESTAMP(6) "
            "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
            ("plt-local", "tenant-a", asset.object_ref),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    path.rmdir()
    backup.rename(path)
    assert store.collect_garbage() == 1
    assert not path.exists()

    path.write_bytes(content)
    store.delete(asset.object_ref, "plt-local", "tenant-a")
    assert store.collect_garbage() == 1
    assert not path.exists()

    late = store.create_upload_intent(UploadRequest("plt-local", "tenant-a", "late.txt", "text/plain", len(content)))
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE content_upload_intents SET lease_expires_at=DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL 1 SECOND) "
            "WHERE platform_id=%s AND tenant_id=%s AND upload_id=%s",
            ("plt-local", "tenant-a", late.upload_id),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert store.collect_garbage() == 1
    late_path = store._path(late.object_ref)
    assert not late_path.exists()
    with pytest.raises(ObjectStoreError, match="lease expired"):
        store.complete_upload(late.upload_id, content)
    assert late_path.exists()
    assert store.collect_garbage() == 1
    assert not late_path.exists()


def test_gc_protects_message_chunk_run_and_runtime_references(tmp_path: Path, mysql_storage_database) -> None:
    import hashlib

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore, UploadRequest

    database, config = mysql_storage_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
            "VALUES ('plt-local','tenant-a','admin-local','subject-local','admin-local','管理员','ACTIVE')"
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "技能", SkillType.INSTRUCTION, "回答", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "助手", "支持", "GC 引用保护")
    version = catalog.create_employee_version(identity, employee.employee_id, "系统指令", [skill.skill_id], [])
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    execution = MySqlExecutionRepository(connection_factory)
    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=execution)
    session = control_plane.create_session(identity, deployment.deployment_id)
    run = control_plane.create_run(identity, session.session_id, "创建结果引用", "protect-result")

    store = LocalObjectStore(tmp_path / "protected", metadata_repository=MySqlMediaAssetRepository(connection_factory))
    content = b"run result reference"
    intent = store.create_upload_intent(
        UploadRequest("plt-local", "tenant-a", "result.bin", "application/octet-stream", len(content))
    )
    asset = store.complete_upload(intent.upload_id, content)
    extra_assets = []
    for name in ("message", "chunk", "runtime-thread", "runtime-workspace"):
        body = f"protected {name}".encode()
        upload = store.create_upload_intent(
            UploadRequest("plt-local", "tenant-a", f"{name}.bin", "application/octet-stream", len(body))
        )
        extra_assets.append((store.complete_upload(upload.upload_id, body), body))
    message_asset, message_body = extra_assets[0]
    chunk_asset, chunk_body = extra_assets[1]
    runtime_thread_asset, _ = extra_assets[2]
    runtime_workspace_asset, _ = extra_assets[3]
    from agent_platform.domain.common import generate_database_id

    message_id = generate_database_id()
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE runs SET result_ref=%s, runtime_result_ref=%s "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (asset.object_ref, runtime_thread_asset.object_ref, identity.platform_id, identity.tenant_id, run.run_id),
        )
        cursor.execute(
            "UPDATE sessions SET runtime_thread_ref=%s, runtime_workspace_ref=%s "
            "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
            (
                runtime_thread_asset.object_ref,
                runtime_workspace_asset.object_ref,
                identity.platform_id,
                identity.tenant_id,
                session.session_id,
            ),
        )
        cursor.execute(
            "INSERT INTO messages "
            "(platform_id, tenant_id, message_id, session_id, run_id, sequence_no, role, status, content_ref, "
            "content_sha256, byte_length, content_type, sender_id, source, routing_depth) "
            "VALUES (%s,%s,%s,%s,%s,1,'ASSISTANT','COMPLETE',%s,%s,%s,'application/octet-stream',%s,'agent',0)",
            (
                identity.platform_id,
                identity.tenant_id,
                message_id,
                session.session_id,
                run.run_id,
                message_asset.object_ref,
                hashlib.sha256(message_body).hexdigest(),
                len(message_body),
                identity.user_id,
            ),
        )
        cursor.execute(
            "INSERT INTO message_chunks "
            "(platform_id, tenant_id, message_id, chunk_sequence, content_ref, content_sha256, byte_length) "
            "VALUES (%s,%s,%s,1,%s,%s,%s)",
            (
                identity.platform_id,
                identity.tenant_id,
                message_id,
                chunk_asset.object_ref,
                hashlib.sha256(chunk_body).hexdigest(),
                len(chunk_body),
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    protected_assets = [asset, message_asset, chunk_asset, runtime_thread_asset, runtime_workspace_asset]
    for protected_asset in protected_assets:
        store.delete(protected_asset.object_ref, "plt-local", "tenant-a")
    assert store.collect_garbage() == 0
    assert all(store._path(protected_asset.object_ref).exists() for protected_asset in protected_assets)
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "DELETE FROM message_chunks WHERE platform_id=%s AND tenant_id=%s AND message_id=%s",
            (identity.platform_id, identity.tenant_id, message_id),
        )
        cursor.execute(
            "DELETE FROM messages WHERE platform_id=%s AND tenant_id=%s AND message_id=%s",
            (identity.platform_id, identity.tenant_id, message_id),
        )
        cursor.execute(
            "UPDATE runs SET result_ref=NULL, runtime_result_ref=NULL "
            "WHERE platform_id=%s AND tenant_id=%s AND run_id=%s",
            (identity.platform_id, identity.tenant_id, run.run_id),
        )
        cursor.execute(
            "UPDATE sessions SET runtime_thread_ref=NULL, runtime_workspace_ref=NULL "
            "WHERE platform_id=%s AND tenant_id=%s AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        for protected_asset in protected_assets:
            cursor.execute(
                "UPDATE content_gc_queue SET next_attempt_at=CURRENT_TIMESTAMP(6) "
                "WHERE platform_id=%s AND tenant_id=%s AND object_ref=%s",
                (identity.platform_id, identity.tenant_id, protected_asset.object_ref),
            )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert store.collect_garbage() == len(protected_assets)
    assert all(not store._path(protected_asset.object_ref).exists() for protected_asset in protected_assets)


def test_gc_worker_cli_uses_the_configured_mysql_pool(tmp_path: Path, mysql_storage_database, monkeypatch) -> None:
    from agent_platform.infrastructure.object_gc import main as run_gc_worker

    database, config = mysql_storage_database
    monkeypatch.setenv("MYSQL_HOST", str(config["host"]))
    monkeypatch.setenv("MYSQL_PORT", str(config["port"]))
    monkeypatch.setenv("MYSQL_DATABASE", database)
    monkeypatch.setenv("MYSQL_USER", str(config["user"]))
    monkeypatch.setenv("MYSQL_PASSWORD", str(config["password"]))
    monkeypatch.setenv("AGENT_PLATFORM_DATA_ROOT", str(tmp_path / "gc-worker-data"))
    assert run_gc_worker() == 0
