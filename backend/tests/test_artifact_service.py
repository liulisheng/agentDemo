import io
import json
import zipfile

import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.artifacts import ArtifactService
from agent_platform.domain.artifacts import ArtifactStatus
from agent_platform.infrastructure.artifact_repository import InMemoryArtifactRepository
from agent_platform.ports.storage import LocalObjectStore


def test_artifact_collector_registers_ready_text_with_provenance() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    service = ArtifactService(repository)

    artifact = service.create_text(
        identity,
        conversation_id="conversation-1",
        orchestration_id="orchestration-1",
        task_id="task-1",
        run_id="run-1",
        filename="report.md",
        content="分析结果",
        source_engine="deepseek_harness",
    )

    assert artifact.status is ArtifactStatus.READY
    assert artifact.content_sha256
    assert artifact.orchestration_id == "orchestration-1"


def test_artifact_delivery_is_idempotent() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    service = ArtifactService(repository)
    artifact = service.create_text(identity, "conversation-1", "orchestration-1", "task-1", "run-1", "report.md", "结果")

    first = service.deliver(identity, artifact.artifact_id, "web", "delivery-1")
    replay = service.deliver(identity, artifact.artifact_id, "web", "delivery-1")

    assert first.delivery_id == replay.delivery_id
    assert replay.status == "DELIVERED"
    assert repository.get_artifact(identity, artifact.artifact_id).status is ArtifactStatus.DELIVERED


def test_artifact_lifecycle_can_scan_and_deliver() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    service = ArtifactService(repository)
    artifact = service.create_text(identity, "conversation-1", None, None, None, "report.md", "结果")

    scanning = service.transition(identity, artifact.artifact_id, ArtifactStatus.DELIVERING)
    delivered = service.transition(identity, scanning.artifact_id, ArtifactStatus.DELIVERED)

    assert delivered.status is ArtifactStatus.DELIVERED


def test_artifact_collector_writes_and_reads_tenant_scoped_object_store(tmp_path) -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    object_store = LocalObjectStore(tmp_path / "objects")
    service = ArtifactService(repository, object_store)

    artifact = service.create_text(identity, "conversation-1", None, "task-1", "run-1", "report.md", "结果")

    assert object_store.read(artifact.object_ref, identity.platform_id, identity.tenant_id) == "结果".encode()
    object_store.delete(artifact.object_ref, identity.platform_id, identity.tenant_id)
    with pytest.raises(PermissionError):
        object_store.read(artifact.object_ref, identity.platform_id, identity.tenant_id)


def test_artifact_preview_survives_service_restart_using_object_store(tmp_path) -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    object_store = LocalObjectStore(tmp_path / "objects")
    first_process = ArtifactService(repository, object_store)
    artifact = first_process.create_text(identity, "conversation-1", "orchestration-1", "task-1", "run-1", "report.md", "持久化报告正文")

    restarted_process = ArtifactService(repository, object_store)

    assert restarted_process.read_preview(identity, artifact.artifact_id) == "持久化报告正文"


def test_artifact_bundle_contains_source_manifest_and_safe_filenames() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    service = ArtifactService(repository)
    first = service.create_text(identity, "conversation-1", "orchestration-1", "task-1", "run-1", "../../data.md", "A")
    second = service.create_text(identity, "conversation-1", "orchestration-1", "task-2", "run-2", "data.md", "B")

    bundle = service.bundle(identity, [first.artifact_id, second.artifact_id])

    assert first.filename == "data.md"
    with zipfile.ZipFile(io.BytesIO(service.read_bytes(identity, bundle.artifact_id))) as archive:
        assert set(archive.namelist()) == {"data.md", "data-2.md", "delivery-manifest.json"}
        manifest = json.loads(archive.read("delivery-manifest.json"))
        assert {item["artifact_id"] for item in manifest["artifacts"]} == {first.artifact_id, second.artifact_id}
