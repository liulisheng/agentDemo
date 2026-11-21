from __future__ import annotations

import pytest

from agent_platform.adapters.identity.fake import FakeAuthorizationProvider, FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.model_management import ModelManagementService
from agent_platform.application.model_routes import ModelRouteService
from agent_platform.domain.catalog import SkillType
from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus, UsageRecord
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, SecretRef
from agent_platform.ports.runtime import RuntimeCapabilities, RuntimeEvent, RuntimeResult


class _ToggleModelAuthorization:
    authorization_source = "test"

    def __init__(self) -> None:
        self.allowed = True

    def authorize(self, identity, connection, model) -> bool:
        return (
            self.allowed
            and identity.tenant_id == connection.tenant_id == model.tenant_id
            and connection.status.value == "ACTIVE"
            and model.status.value == "ACTIVE"
        )


class _CountingRuntime:
    engine_name = "test-runtime"
    capabilities = RuntimeCapabilities()

    def __init__(self) -> None:
        self.run_calls = 0

    def create_session(self) -> str:
        return "test-session"

    def run(self, request):
        self.run_calls += 1
        return RuntimeResult(
            text="should not run",
            status=RuntimeStatus.SUCCEEDED,
            runtime_session_ref=request.session_ref or "test-session",
            runtime_result_ref="test-result",
            usage=UsageRecord(1, 1, 2),
            events=(RuntimeEvent("run_finished", "RESULT", {"status": "succeeded"}),),
        )

    def resume_session(self, session_ref: str) -> str:
        return session_ref

    def cancel(self, session_ref: str) -> bool:
        return True


def _model_deployment(identity: IdentityContext):
    catalog = CatalogService(InMemoryCatalogRepository())
    repository = catalog.repository
    connection = ModelConnection(
        connection_id="auth-connection",
        tenant_id=identity.tenant_id,
        name="租户本地模型",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://localhost:63072/v1",
        secret_ref=SecretRef("auth-key", identity.tenant_id, "environment"),
    )
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="authorized-model",
            display_name="授权模型",
        ),
    )
    routes = ModelRouteService(repository)
    route = routes.create_from_catalog(
        identity,
        alias="authorized-route",
        connection_id=connection.connection_id,
        model_id="authorized-model",
    )
    profile = catalog.create_profile(
        identity, "deepseek", "authorized-route", model_route_id=route.route_id
    )
    skill = catalog.create_skill(identity, "空技能", SkillType.INSTRUCTION, "回答问题", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "授权助手", "助手", "授权验证")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "按用户问题回答。",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
        model_route_id=route.route_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    return catalog, routes, catalog.deploy(identity, employee.employee_id, published.version)


def test_denied_or_revoked_model_authorization_blocks_before_runtime_dispatch() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog, _routes, deployment = _model_deployment(identity)
    authorization = _ToggleModelAuthorization()
    runtime = _CountingRuntime()
    control_plane = ControlPlaneService(
        catalog,
        runtime,
        model_authorization_provider=authorization,
    )
    session = control_plane.create_session(identity, deployment.deployment_id)

    authorization.allowed = False
    with pytest.raises(DomainError) as denied:
        control_plane.create_run(identity, session.session_id, "請回答")
    assert denied.value.code == "AUTHORIZATION_DENIED"
    assert runtime.run_calls == 0

    authorization.allowed = True
    resolved = control_plane.agent_resolver.resolve(identity, deployment)
    authorization.allowed = False
    with pytest.raises(DomainError) as revoked:
        control_plane.agent_resolver.resolve_snapshot(identity, resolved.snapshot)
    assert revoked.value.code == "AUTHORIZATION_DENIED"
    assert runtime.run_calls == 0


def test_employee_model_selector_uses_current_identity_authorization() -> None:
    identity_provider = FakeIdentityProvider()
    admin = identity_provider.resolve_identity("admin-local")
    member = identity_provider.resolve_identity("member-local")
    catalog, routes, _deployment = _model_deployment(admin)

    class AdminOnlyAuthorization(_ToggleModelAuthorization):
        def authorize(self, identity, connection, model) -> bool:
            return identity.user_id == "admin-local" and super().authorize(
                identity, connection, model
            )

    service = ModelManagementService(
        catalog.repository,
        routes,
        FakeAuthorizationProvider(),
        AdminOnlyAuthorization(),
    )

    assert [route.route_id for route in service.selectable_routes(admin)] == [
        routes.list(admin)[0].route_id
    ]
    assert service.selectable_routes(member) == ()
