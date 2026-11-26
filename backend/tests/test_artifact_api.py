from fastapi.testclient import TestClient

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.api.app import create_app
from agent_platform.application.artifacts import ArtifactService
from agent_platform.domain.orchestration import OrchestrationRun
from agent_platform.infrastructure.artifact_repository import InMemoryArtifactRepository


def test_artifact_query_download_and_delivery_are_tenant_scoped() -> None:
    app = create_app()
    client = TestClient(app)
    admin = {"X-Fake-User": "admin-local"}
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    artifact = app.state.artifact_service.create_text(
        identity, "conversation-1", "orchestration-1", "task-1", "run-1", "report.md", "report"
    )
    app.state.orchestration_service.repository.save_orchestration(
        identity,
        OrchestrationRun("orchestration-1", identity.platform_id, identity.tenant_id, "conversation-1", "team-1", 1),
    )

    detail = client.get(f"/api/v1/artifacts/{artifact.artifact_id}", headers=admin)
    download = client.get(f"/api/v1/artifacts/{artifact.artifact_id}/download", headers=admin)
    delivered = client.post(
        "/api/v1/orchestrations/orchestration-1/deliver",
        headers=admin,
        json={"artifact_ids": [artifact.artifact_id], "channel_name": "web", "idempotency_key": "delivery-1"},
    )

    assert detail.status_code == 200
    assert detail.json()["filename"] == "report.md"
    assert download.status_code == 200
    assert download.content == b"report"
    assert delivered.status_code == 200
    assert delivered.json()["items"][0]["status"] == "DELIVERED"

    viewer = client.post(
        "/api/v1/orchestrations/orchestration-1/deliver",
        headers={"X-Fake-User": "member-local"},
        json={"artifact_ids": [artifact.artifact_id], "channel_name": "web", "idempotency_key": "delivery-viewer"},
    )
    assert viewer.status_code == 403


def test_artifact_preview_and_bundle_are_source_backed() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryArtifactRepository()
    service = ArtifactService(repository)
    first = service.create_text(identity, "conversation-1", "orchestration-1", "task-1", "run-1", "a.md", "A")
    second = service.create_text(identity, "conversation-1", "orchestration-1", "task-2", "run-2", "b.md", "B")

    assert service.read_preview(identity, first.artifact_id) == "A"
    bundle = service.bundle(identity, [first.artifact_id, second.artifact_id])
    assert bundle.filename == "delivery.zip"
    assert bundle.size_bytes > 0
