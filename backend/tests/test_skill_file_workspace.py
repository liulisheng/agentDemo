from __future__ import annotations

import hashlib

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.skill_files import SkillFilePackageService
from agent_platform.domain.catalog import SkillContentFormat, SkillType
from agent_platform.domain.common import DomainError
from agent_platform.ports.storage import MediaAsset, MediaAssetStatus, RetentionPolicy, UploadIntent


class MemoryObjectStore:
    def __init__(self) -> None:
        self.intents = {}
        self.assets: dict[str, MediaAsset] = {}
        self.contents: dict[str, bytes] = {}
        self.completed = 0

    def create_upload_intent(self, request):
        from agent_platform.domain.common import generate_database_id

        upload_id = generate_database_id()
        object_ref = f"object:{request.platform_id}:{request.tenant_id}:{generate_database_id()}"
        self.intents[upload_id] = (request, object_ref)
        return UploadIntent(upload_id, object_ref, f"upload://{upload_id}", 600, 10 * 1024 * 1024)

    def complete_upload(self, upload_id: str, content: bytes) -> MediaAsset:
        from agent_platform.domain.common import generate_database_id

        self.completed += 1
        request, object_ref = self.intents.pop(upload_id)
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
        self.assets[asset.asset_id] = asset
        self.contents[object_ref] = content
        return asset

    def read_asset(self, asset_id: str, platform_id: str, tenant_id: str, *, max_bytes: int = 10 * 1024 * 1024):
        asset = self.assets.get(asset_id)
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id:
            raise PermissionError("asset outside tenant scope")
        if asset.size_bytes > max_bytes:
            raise ValueError("asset too large")
        return self.contents[asset.object_ref]

    def delete(self, object_ref: str, platform_id: str, tenant_id: str) -> None:
        asset = next((item for item in self.assets.values() if item.object_ref == object_ref), None)
        if asset is None or asset.platform_id != platform_id or asset.tenant_id != tenant_id:
            raise PermissionError("asset outside tenant scope")
        self.assets.pop(asset.asset_id, None)
        self.contents.pop(object_ref, None)


@pytest.fixture
def workspace():
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    skill = catalog.create_skill(identity, "文件工作区 Skill", SkillType.INSTRUCTION, "INLINE 初始指令", ())
    storage = MemoryObjectStore()
    service = SkillFilePackageService(catalog, storage)
    return identity, catalog, skill, storage, service


def test_file_workspace_creates_root_and_auxiliary_file_then_reads_tree(workspace) -> None:
    identity, catalog, skill, storage, service = workspace

    package = service.create_file(identity, skill.skill_id, 1, "SKILL.md", "根入口指令".encode())
    package = service.create_file(identity, skill.skill_id, 1, "references/guide.txt", b"guide")
    files = service.list_files(identity, skill.skill_id, 1)

    assert package.content_format is SkillContentFormat.FILE_PACKAGE
    assert [item.file_path for item in files] == ["SKILL.md", "references/guide.txt"]
    assert service.read_file(identity, skill.skill_id, 1, "SKILL.md") == "根入口指令".encode()
    assert service.read_file(identity, skill.skill_id, 1, "references/guide.txt") == b"guide"
    assert catalog.repository.get_skill_version(identity, skill.skill_id, 1).file_manifest_sha256
    assert len(storage.assets) == 2


def test_file_update_rename_and_delete_use_expected_sha256(workspace) -> None:
    identity, _, skill, _, service = workspace
    current = service.create_file(identity, skill.skill_id, 1, "SKILL.md", b"entry v1")
    current = service.create_file(identity, skill.skill_id, 1, "guide.txt", b"guide v1")
    guide = next(item for item in current.file_entries if item.file_path == "guide.txt")

    with pytest.raises(DomainError) as stale_update:
        service.update_file(
            identity,
            skill.skill_id,
            1,
            "guide.txt",
            b"lost update",
            expected_sha256="0" * 64,
        )
    assert stale_update.value.code == "IDEMPOTENCY_CONFLICT"

    current = service.update_file(
        identity,
        skill.skill_id,
        1,
        "guide.txt",
        b"guide v2",
        expected_sha256=guide.content_sha256,
    )
    guide = next(item for item in current.file_entries if item.file_path == "guide.txt")
    current = service.rename_file(
        identity,
        skill.skill_id,
        1,
        "guide.txt",
        "references/guide.txt",
        expected_sha256=guide.content_sha256,
    )
    guide = next(item for item in current.file_entries if item.file_path == "references/guide.txt")
    assert service.read_file(identity, skill.skill_id, 1, guide.file_path) == b"guide v2"

    current = service.delete_file(
        identity,
        skill.skill_id,
        1,
        guide.file_path,
        expected_sha256=guide.content_sha256,
    )
    assert [item.file_path for item in current.file_entries] == ["SKILL.md"]


def test_root_entry_cannot_be_renamed_or_deleted_and_published_files_are_read_only(workspace) -> None:
    identity, catalog, skill, _, service = workspace
    current = service.create_file(identity, skill.skill_id, 1, "SKILL.md", b"entry")
    root = current.file_entries[0]
    with pytest.raises(DomainError):
        service.rename_file(
            identity, skill.skill_id, 1, "SKILL.md", "ENTRY.md", expected_sha256=root.content_sha256
        )
    with pytest.raises(DomainError):
        service.delete_file(identity, skill.skill_id, 1, "SKILL.md", expected_sha256=root.content_sha256)

    catalog.repository.save_skill(identity, current.publish())
    with pytest.raises(DomainError) as immutable:
        service.update_file(
            identity, skill.skill_id, 1, "SKILL.md", b"new entry", expected_sha256=root.content_sha256
        )
    assert immutable.value.code == "IMMUTABLE_VERSION"


def test_inline_skill_root_edit_remains_compatible_and_cross_tenant_is_hidden(workspace) -> None:
    identity, _, skill, _, service = workspace
    old_sha = hashlib.sha256("INLINE 初始指令".encode()).hexdigest()
    updated = service.update_file(
        identity,
        skill.skill_id,
        1,
        "SKILL.md",
        "INLINE 编辑指令".encode(),
        expected_sha256=old_sha,
    )
    assert updated.content_format is SkillContentFormat.INLINE
    assert updated.instruction == "INLINE 编辑指令"

    other = FakeIdentityProvider().resolve_identity("admin-other")
    with pytest.raises(DomainError) as hidden:
        service.list_files(other, skill.skill_id, 1)
    assert hidden.value.code == "RESOURCE_NOT_FOUND"


def test_file_workspace_api_supports_list_read_create_update_rename_and_delete() -> None:
    from fastapi.testclient import TestClient

    from agent_platform.api.app import create_app

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    skill = catalog.create_skill(identity, "文件工作区 API", SkillType.INSTRUCTION, "INLINE 初始指令", ())
    storage = MemoryObjectStore()
    client = TestClient(
        create_app(mode="fake", catalog_repository=catalog.repository, object_store=storage)
    )
    headers = {"X-Fake-User": identity.user_id}
    prefix = f"/api/v1/skills/{skill.skill_id}/versions/1/files"
    listed_inline = client.get(prefix, headers=headers)
    assert listed_inline.status_code == 200
    assert listed_inline.json()["files"][0]["virtual"] is True

    root = client.post(
        prefix,
        headers=headers,
        json={"file_path": "SKILL.md", "content_text": "新入口"},
    )
    assert root.status_code == 201
    assert root.json()["content_format"] == "FILE_PACKAGE"
    root_file = client.get(prefix + "/content", headers=headers, params={"file_path": "SKILL.md"})
    assert root_file.json()["content_text"] == "新入口"

    created = client.post(
        prefix,
        headers=headers,
        json={"file_path": "guide.txt", "content_text": "guide v1"},
    )
    assert created.status_code == 201
    guide = next(item for item in client.get(prefix, headers=headers).json()["files"] if item["path"] == "guide.txt")
    updated = client.put(
        prefix,
        headers=headers,
        json={
            "file_path": "guide.txt",
            "content_text": "guide v2",
            "expected_sha256": guide["sha256"],
        },
    )
    assert updated.status_code == 200
    stale = client.put(
        prefix,
        headers=headers,
        json={
            "file_path": "guide.txt",
            "content_text": "lost update",
            "expected_sha256": guide["sha256"],
        },
    )
    assert stale.status_code == 409

    guide_v2 = next(item for item in client.get(prefix, headers=headers).json()["files"] if item["path"] == "guide.txt")
    renamed = client.post(
        prefix + "/rename",
        headers=headers,
        json={
            "file_path": "guide.txt",
            "new_file_path": "references/guide.txt",
            "expected_sha256": guide_v2["sha256"],
        },
    )
    assert renamed.status_code == 200
    renamed_file = next(
        item
        for item in client.get(prefix, headers=headers).json()["files"]
        if item["path"] == "references/guide.txt"
    )
    deleted = client.delete(
        prefix,
        headers=headers,
        params={"file_path": "references/guide.txt", "expected_sha256": renamed_file["sha256"]},
    )
    assert deleted.status_code == 200

    root_sha = client.get(prefix, headers=headers).json()["files"][0]["sha256"]
    denied_root_delete = client.delete(
        prefix,
        headers=headers,
        params={"file_path": "SKILL.md", "expected_sha256": root_sha},
    )
    assert denied_root_delete.status_code == 422
