from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.raw_payload import InMemoryRawPayloadStore, RawPayloadPolicy


def test_disabled_payload_policy_keeps_metadata_only() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    store = InMemoryRawPayloadStore()
    assert store.save(identity, b"secret", RawPayloadPolicy(enabled=False)) is None


def test_in_memory_fake_does_not_claim_payload_encryption() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    store = InMemoryRawPayloadStore()
    ref = store.save(identity, b"secret", RawPayloadPolicy(enabled=True, retention_days=7))
    assert ref is not None
    assert ref.encrypted is False
    assert ref.object_ref.startswith("payload:plt-local:tenant-a:")
    assert store.read(identity, ref.object_ref) == b"secret"


def test_local_payload_storage_requires_encryption_and_tenant_authorizes_reads(tmp_path) -> None:
    import base64

    import pytest

    from agent_platform.application.raw_payload import LocalRawPayloadStore
    from agent_platform.domain.common import DomainError
    from agent_platform.ports.storage import LocalObjectStore, ObjectStoreError, SecretRef
    from agent_platform.ports.workspaces import FakeSecretProvider

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    other = FakeIdentityProvider().resolve_identity("admin-other")
    plaintext = b"do not store this in MySQL"
    disabled_root = tmp_path / "disabled"
    disabled = LocalRawPayloadStore(LocalObjectStore(disabled_root))
    assert disabled.save(identity, plaintext, RawPayloadPolicy()) is None
    assert list(disabled_root.iterdir()) == []

    unencrypted = LocalRawPayloadStore(LocalObjectStore(tmp_path / "unencrypted"))
    with pytest.raises(ObjectStoreError, match="encrypted"):
        unencrypted.save(identity, plaintext, RawPayloadPolicy(enabled=True))

    encryption_key = bytes(range(32))
    provider = FakeSecretProvider(
        {"secret/raw-payload@v1": {"key_base64": base64.b64encode(encryption_key).decode("ascii")}}
    )
    store = LocalObjectStore(
        tmp_path / "encrypted",
        require_encryption=True,
        encryption_secret_ref=SecretRef("secret/raw-payload"),
        encryption_key_version="v1",
        key_provider=provider,
    )
    payloads = LocalRawPayloadStore(store)
    ref = payloads.save(identity, plaintext, RawPayloadPolicy(enabled=True))
    assert ref is not None and ref.encrypted is True
    assert payloads.read(identity, ref.object_ref) == plaintext
    with pytest.raises(DomainError) as cross_tenant:
        payloads.read(other, ref.object_ref)
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"
    payloads.delete(identity, ref.object_ref)
    with pytest.raises(DomainError):
        payloads.read(identity, ref.object_ref)
