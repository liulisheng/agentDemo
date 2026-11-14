from agent_platform.adapters.identity.fake import FakeAuthorizationProvider, FakeIdentityProvider
from agent_platform.domain.identity import IdentityContext


def test_fake_identity_resolves_active_user_and_tenant() -> None:
    identity = FakeIdentityProvider()

    context = identity.resolve_identity("admin-local")

    assert isinstance(context, IdentityContext)
    assert context.platform_id == "plt-local"
    assert context.tenant_id == "tenant-a"
    assert context.user_id == "admin-local"
    assert "admin" in context.roles

    other_context = identity.resolve_identity("admin-other")
    assert other_context.tenant_id == "tenant-b"


def test_fake_identity_rejects_disabled_user() -> None:
    identity = FakeIdentityProvider()

    try:
        identity.resolve_identity("disabled-local")
    except PermissionError as error:
        assert str(error) == "user is not active"
    else:
        raise AssertionError("disabled user must be rejected")


def test_fake_authorization_is_tenant_scoped() -> None:
    identity = FakeIdentityProvider()
    authorization = FakeAuthorizationProvider()
    context = identity.resolve_identity("admin-local")

    assert authorization.can(context, "digital_employee:read", "tenant-a:employee-admin")
    assert not authorization.can(context, "digital_employee:read", "tenant-b:employee-other")
