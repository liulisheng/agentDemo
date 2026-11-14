from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.raw_payload import InMemoryRawPayloadStore, RawPayloadPolicy


def test_disabled_payload_policy_keeps_metadata_only() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    store = InMemoryRawPayloadStore()
    assert store.save(identity, b"secret", RawPayloadPolicy(enabled=False)) is None


def test_enabled_payload_policy_returns_encrypted_tenant_reference() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    store = InMemoryRawPayloadStore()
    ref = store.save(identity, b"secret", RawPayloadPolicy(enabled=True, retention_days=7))
    assert ref is not None
    assert ref.encrypted is True
    assert ref.object_ref.startswith("payload:plt-local:tenant-a:")
    assert store.read(identity, ref.object_ref) == b"secret"
