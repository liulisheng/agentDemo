from __future__ import annotations

import hashlib
import io
import stat
import zipfile

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.domain.catalog import SkillContentFormat, SkillType
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.ports.storage import MediaAsset, MediaAssetStatus, RetentionPolicy, UploadIntent


class MemoryObjectStore:
    def __init__(self) -> None:
        self.requests = {}
        self.assets = {}
        self.contents = {}
        self.completed = 0
        self.fail_on_complete: int | None = None

    def create_upload_intent(self, request):
        upload_id = generate_database_id()
        object_ref = f"object:{request.platform_id}:{request.tenant_id}:{generate_database_id()}"
        self.requests[upload_id] = (request, object_ref)
        return UploadIntent(upload_id, object_ref, f"upload://{upload_id}", 600, 50 * 1024 * 1024)

    def complete_upload(self, upload_id: str, content: bytes) -> MediaAsset:
        self.completed += 1
        if self.fail_on_complete == self.completed:
            raise RuntimeError("simulated object upload failure")
        request, object_ref = self.requests.pop(upload_id)
        asset = MediaAsset(
            asset_id=generate_database_id(),
            object_ref=object_ref,
            platform_id=request.platform_id,
            tenant_id=request.tenant_id,
            filename=request.filename,
            mime_type=request.mime_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            retention_policy=RetentionPolicy(request.retention_policy),
            status=MediaAssetStatus.UPLOADED,
        )
        self.assets[object_ref] = asset
        self.contents[object_ref] = content
        return asset

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        asset = self.assets.get(object_ref)
        if asset and asset.platform_id == platform_id and asset.tenant_id == tenant_id:
            self.assets.pop(object_ref, None)
            self.contents.pop(object_ref, None)


def _zip(entries: list[tuple[str, bytes]], *, symlink_path: str | None = None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries:
            info = zipfile.ZipInfo(path)
            if path == symlink_path:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "target")
            else:
                archive.writestr(info, content)
    return stream.getvalue()


@pytest.fixture
def file_package_service():
    from agent_platform.application.skill_files import SkillFilePackageService

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    skill = catalog.create_skill(identity, "工单技能", SkillType.INSTRUCTION, "旧 INLINE 指令", ())
    object_store = MemoryObjectStore()
    service = SkillFilePackageService(catalog, object_store)
    return identity, catalog, skill, object_store, service


def test_zip_preview_then_atomic_commit_creates_file_package(file_package_service) -> None:
    identity, catalog, skill, object_store, service = file_package_service
    archive = _zip(
        [
            ("SKILL.md", "新入口指令".encode()),
            ("references/guide.txt", b"supporting text"),
        ]
    )

    preview = service.preview_zip(identity, skill.skill_id, skill.version, archive)

    assert preview.file_count == 2
    assert preview.entry_path == "SKILL.md"
    assert {entry.file_path for entry in preview.files} == {"SKILL.md", "references/guide.txt"}
    assert object_store.completed == 0
    assert catalog.repository.get_skill_version(identity, skill.skill_id, 1).content_format is SkillContentFormat.INLINE

    updated = service.commit_zip(identity, skill.skill_id, skill.version, preview.staging_id, archive)

    assert updated.content_format is SkillContentFormat.FILE_PACKAGE
    assert updated.instruction == ""
    assert updated.file_manifest_sha256 == preview.manifest_sha256
    assert {entry.file_path for entry in updated.file_entries} == {"SKILL.md", "references/guide.txt"}
    assert object_store.completed == 2


@pytest.mark.parametrize(
    "entries,symlink_path,detail",
    [
        ([("../escape.txt", b"bad"), ("SKILL.md", b"entry")], None, "路径"),
        ([('/absolute.txt', b"bad"), ("SKILL.md", b"entry")], None, "路径"),
        ([('docs/SKILL.md', b"nested"), ("SKILL.md", b"entry")], None, "入口文件"),
        ([("guide.md", b"missing")], None, "skill.md"),
        ([("assets/a.txt", b"one"), ("assets//a.txt", b"two"), ("SKILL.md", b"entry")], None, "路径"),
        ([('assets/readme.txt', b"one"), ('assets/README.txt', b"two"), ("SKILL.md", b"entry")], None, "重复"),
        ([('assets/café.txt', b"one"), ('assets/cafe\u0301.txt', b"two"), ("SKILL.md", b"entry")], None, "重复"),
        ([("link", b"ignored"), ("SKILL.md", b"entry")], "link", "符号链接"),
        ([("SKILL.md", b"   ")], None, "不能为空"),
    ],
)
def test_zip_security_validation_rejects_entire_archive_before_object_write(
    file_package_service, entries, symlink_path, detail
) -> None:
    identity, _, skill, object_store, service = file_package_service
    archive = _zip(entries, symlink_path=symlink_path)

    with pytest.raises(DomainError) as error:
        service.preview_zip(identity, skill.skill_id, skill.version, archive)

    assert error.value.code == "INVALID_ARGUMENT"
    assert detail in error.value.message.lower()
    assert object_store.completed == 0


def test_zip_uncompressed_size_and_file_count_limits_reject_before_upload(file_package_service) -> None:
    from agent_platform.application.skill_files import SkillFilePackageService

    identity, catalog, skill, object_store, _ = file_package_service
    limited = SkillFilePackageService(
        catalog,
        object_store,
        max_uncompressed_bytes=32,
        max_file_bytes=16,
        max_files=2,
    )
    too_large = _zip([("SKILL.md", b"x" * 64)])
    too_many = _zip([("SKILL.md", b"ok"), ("a.txt", b"a"), ("b.txt", b"b")])

    for archive in (too_large, too_many):
        with pytest.raises(DomainError):
            limited.preview_zip(identity, skill.skill_id, skill.version, archive)
    assert object_store.completed == 0


def test_zip_archive_and_total_uncompressed_limits_are_enforced(file_package_service) -> None:
    from agent_platform.application.skill_files import SkillFilePackageService

    identity, catalog, skill, object_store, _ = file_package_service
    compressed_limit = SkillFilePackageService(catalog, object_store, max_archive_bytes=16)
    uncompressed_limit = SkillFilePackageService(
        catalog,
        object_store,
        max_file_bytes=32,
        max_uncompressed_bytes=32,
        max_compression_ratio=1000,
    )
    archive_over_limit = _zip([("SKILL.md", b"entry")])
    total_over_limit = _zip([("SKILL.md", bytes(range(20))), ("guide.bin", bytes(range(20)))])

    with pytest.raises(DomainError, match="压缩包"):
        compressed_limit.preview_zip(identity, skill.skill_id, 1, archive_over_limit)
    with pytest.raises(DomainError, match="总大小"):
        uncompressed_limit.preview_zip(identity, skill.skill_id, 1, total_over_limit)
    assert object_store.completed == 0


def test_commit_requires_the_previewed_archive_and_unchanged_draft(file_package_service) -> None:
    identity, catalog, skill, object_store, service = file_package_service
    archive = _zip([("SKILL.md", b"original")])
    preview = service.preview_zip(identity, skill.skill_id, skill.version, archive)
    different_archive = _zip([("SKILL.md", b"changed")])

    with pytest.raises(DomainError) as changed_upload:
        service.commit_zip(identity, skill.skill_id, skill.version, preview.staging_id, different_archive)
    assert changed_upload.value.code == "IDEMPOTENCY_CONFLICT"
    assert object_store.completed == 0

    refreshed = service.preview_zip(identity, skill.skill_id, skill.version, archive)
    catalog.update_skill(identity, skill.skill_id, instruction="另一个管理员改过指令")
    with pytest.raises(DomainError) as stale_preview:
        service.commit_zip(identity, skill.skill_id, skill.version, refreshed.staging_id, archive)
    assert stale_preview.value.code == "IDEMPOTENCY_CONFLICT"
    assert object_store.completed == 0


def test_upload_failure_never_saves_partial_file_manifest(file_package_service) -> None:
    identity, catalog, skill, object_store, service = file_package_service
    object_store.fail_on_complete = 2
    archive = _zip([("SKILL.md", b"entry"), ("other.txt", b"support")])
    preview = service.preview_zip(identity, skill.skill_id, skill.version, archive)

    with pytest.raises(RuntimeError, match="simulated"):
        service.commit_zip(identity, skill.skill_id, skill.version, preview.staging_id, archive)

    current = catalog.repository.get_skill_version(identity, skill.skill_id, 1)
    assert current.content_format is SkillContentFormat.INLINE
    assert current.file_entries == ()
    assert object_store.assets == {}


def test_zip_preview_and_commit_http_api_returns_manifest_without_file_bytes() -> None:
    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    skill = catalog.create_skill(identity, "API 文件技能", SkillType.INSTRUCTION, "old", ())
    store = MemoryObjectStore()
    client = TestClient(
        create_app(mode="fake", catalog_repository=catalog.repository, object_store=store)
    )
    headers = {"X-Fake-User": identity.user_id, "Content-Type": "application/zip"}
    archive = _zip([("SKILL.md", b"api entry"), ("docs/guide.txt", b"guide")])

    preview = client.post(
        f"/api/v1/skills/{skill.skill_id}/versions/1/files/zip/preview?strategy=replace",
        headers=headers,
        content=archive,
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["entry_path"] == "SKILL.md"
    assert "api entry" not in preview.text
    assert preview_body["diff"]["added"] == ["SKILL.md", "docs/guide.txt"]

    committed = client.post(
        f"/api/v1/skills/{skill.skill_id}/versions/1/files/zip/commit/{preview_body['staging_id']}",
        headers=headers,
        content=archive,
    )

    assert committed.status_code == 200
    assert committed.json()["content_format"] == "FILE_PACKAGE"
    assert committed.json()["file_count"] == 2
