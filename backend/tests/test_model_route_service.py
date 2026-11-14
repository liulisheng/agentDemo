from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.model_routes import InMemoryModelRouteRepository, ModelRouteService


def test_model_route_service_keeps_secret_reference_only() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    service = ModelRouteService(InMemoryModelRouteRepository())
    route = service.create(identity, "fake-route", "fake-model", "fake", "http://fake", "secret-1")
    assert route.to_public_dict()["profile"]["secret_ref"]["secret_id"] == "secret-1"
    assert "api-key" not in repr(route)
    assert service.list(identity)[0].route_id == route.route_id
