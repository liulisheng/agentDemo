from pathlib import Path

import pytest

from agent_platform.ports.storage import (
    LocalObjectStore,
    MessageAttachment,
    ObjectStoreError,
    RetentionPolicy,
    RuntimeAttachmentRef,
    SecretRef,
    StorageProfile,
    UploadRequest,
)
from agent_platform.ports.workspaces import (
    ArtifactRef,
    FakeSandboxProvider,
    FakeWorkspaceProvider,
    GitCheckoutRequest,
    LogQueryCapability,
    OperationsCapability,
    SandboxProfile,
    SandboxSpec,
    WorkspaceProfile,
)


def test_profiles_reject_unsafe_environment_options() -> None:
    with pytest.raises(ValueError):
        WorkspaceProfile("code", allowed_host_paths=("/etc",))
    with pytest.raises(ValueError):
        SandboxProfile("unsafe", devices=("/dev/kvm",))
    with pytest.raises(ValueError):
        SandboxSpec("run-1", privileged=True)


def test_fake_providers_enforce_project_scope_and_return_opaque_refs() -> None:
    profile = WorkspaceProfile("ephemeral", mode="ephemeral", project_id="p1")
    provider = FakeWorkspaceProvider()
    ref = provider.create(profile, tenant_id="t1", project_id="p1", run_id="r1")
    assert ref.opaque_ref.startswith("workspace:")
    with pytest.raises(PermissionError):
        provider.mount(ref, tenant_id="t2", project_id="p1")

    sandbox = FakeSandboxProvider()
    sref = sandbox.create(SandboxProfile("default"), SandboxSpec("r1"), tenant_id="t1", project_id="p1")
    assert sref.opaque_ref.startswith("sandbox:")


def test_git_contract_binds_commit_and_does_not_write_main() -> None:
    req = GitCheckoutRequest("https://git.example/repo.git", "abc123", branch="main")
    assert req.read_only is True
    assert req.can_write_branch("main") is False
    artifact = ArtifactRef("diff", "obj-1", "t1", "p1")
    assert artifact.ref == "obj-1"


def test_capabilities_gate_mutating_operations() -> None:
    assert LogQueryCapability().query("service-a", "error") == []
    ops = OperationsCapability(approval_checker=lambda action: action == "restart")
    assert ops.execute("restart", "service-a") == "approved"
    with pytest.raises(PermissionError):
        ops.execute("delete", "service-a")


def test_local_store_uses_generated_refs_and_bounded_reads(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    intent = store.create_upload_intent(UploadRequest("plt", "tenant-a", "hello.txt", "text/plain", 5))
    assert str(tmp_path) not in intent.upload_url
    asset = store.complete_upload(intent.upload_id, b"hello")
    assert asset.object_ref == intent.object_ref
    assert store.read(asset.object_ref, "plt", "tenant-a", max_bytes=5) == b"hello"
    with pytest.raises(ObjectStoreError):
        store.read(asset.object_ref, "plt", "tenant-a", max_bytes=4)
    with pytest.raises(PermissionError):
        store.read(asset.object_ref, "plt", "tenant-b")


def test_storage_profile_requires_explicit_provider_and_secret_ref() -> None:
    assert StorageProfile("s3", "s3", "bucket", endpoint="https://s3.example", credential_ref=SecretRef("secret/object"))
    with pytest.raises(ValueError):
        StorageProfile("bad", "filesystem", "bucket")
    with pytest.raises(ValueError):
        StorageProfile("s3", "s3", "bucket", credential_ref="raw-secret")


def test_attachments_keep_platform_asset_and_runtime_copy_separate() -> None:
    attachment = MessageAttachment("att-1", "asset-1", "t1", RetentionPolicy.CONVERSATION)
    ref = RuntimeAttachmentRef.from_attachment(attachment, "workspace-copy/input.txt")
    assert ref.media_asset_ref == "asset-1"
    assert ref.workspace_path == "workspace-copy/input.txt"
    assert ref.authorize_for("t1") is True
    assert ref.authorize_for("t2") is False


def test_storage_binding_prefers_project_then_tenant_then_platform() -> None:
    from agent_platform.ports.storage import StorageBinding, select_storage_profile

    bindings = (
        StorageBinding("PLATFORM", "platform-store", platform_id="plt"),
        StorageBinding("TENANT", "tenant-store", platform_id="plt", tenant_id="t1"),
        StorageBinding("PROJECT", "project-store", platform_id="plt", tenant_id="t1", project_id="p1"),
    )
    assert select_storage_profile(bindings, platform_id="plt", tenant_id="t1", project_id="p1") == "project-store"
    assert select_storage_profile(bindings, platform_id="plt", tenant_id="t1", project_id="p2") == "tenant-store"
    assert select_storage_profile(bindings, platform_id="plt", tenant_id="t2") == "platform-store"


def test_fake_secret_provider_limits_ttl_and_returns_scoped_values() -> None:
    from agent_platform.ports.workspaces import FakeSecretProvider

    provider = FakeSecretProvider({"secret/a": {"token": "short-lived"}})
    assert provider.resolve("secret/a", ttl_seconds=60)["token"] == "short-lived"
    with pytest.raises(ValueError):
        provider.resolve("secret/a", ttl_seconds=3601)
    with pytest.raises(KeyError):
        provider.resolve("secret/missing")


def test_local_store_rejects_path_like_tenant_identifiers(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        UploadRequest("plt", "../other", "x.txt", "text/plain", 1)


def test_fake_sandbox_rejects_workspace_from_other_scope() -> None:
    workspace = FakeWorkspaceProvider().create(WorkspaceProfile("w", project_id="p1"), tenant_id="t1", project_id="p1", run_id="r1")
    with pytest.raises(PermissionError):
        FakeSandboxProvider().create(SandboxProfile("s"), SandboxSpec("r1", workspace_ref=workspace), tenant_id="t2", project_id="p1")


def test_runtime_attachment_rejects_host_absolute_or_parent_paths() -> None:
    attachment = MessageAttachment("att-2", "asset-2", "t1")
    with pytest.raises(ValueError):
        RuntimeAttachmentRef.from_attachment(attachment, "/etc/passwd")
    with pytest.raises(ValueError):
        RuntimeAttachmentRef.from_attachment(attachment, "../outside")


def test_local_store_get_alias_is_tenant_scoped(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    intent = store.create_upload_intent(UploadRequest("plt", "tenant-a", "x", "text/plain", 1))
    asset = store.complete_upload(intent.upload_id, b"x")
    assert store.get(asset.object_ref, "plt", "tenant-a") == b"x"


def test_completed_local_asset_starts_in_uploaded_state(tmp_path: Path) -> None:
    from agent_platform.ports.storage import MediaAssetStatus

    store = LocalObjectStore(tmp_path)
    intent = store.create_upload_intent(UploadRequest("plt", "tenant-a", "x", "text/plain", 1))
    assert store.complete_upload(intent.upload_id, b"x").status is MediaAssetStatus.UPLOADED


def test_media_asset_normalizes_retention_and_status_enums() -> None:
    from agent_platform.ports.storage import MediaAsset, MediaAssetStatus

    asset = MediaAsset("a", "object:p:t:x", "p", "t", "x", "text/plain", 1, "hash", "conversation")
    assert asset.retention_policy is RetentionPolicy.CONVERSATION
    assert asset.status is MediaAssetStatus.UPLOADED
