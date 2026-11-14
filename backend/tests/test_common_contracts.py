import pytest

from agent_platform.domain.common import DomainError, RequestMetadata, ResourceRef
from agent_platform.domain.identity import IdentityContext


def test_resource_ref_rejects_blank_identifiers() -> None:
    with pytest.raises(ValueError, match="resource_id"):
        ResourceRef("run", "", "plt-local", "tenant-a")


def test_resource_ref_rejects_cross_tenant_context() -> None:
    ref = ResourceRef("run", "run-1", "plt-local", "tenant-a")
    identity = IdentityContext("plt-local", "tenant-b", "member-local", frozenset({"member"}))

    assert not ref.same_scope(identity)


def test_request_metadata_and_domain_error_are_stable() -> None:
    metadata = RequestMetadata("req-1", "idem-1")
    error = DomainError("RESOURCE_NOT_FOUND", "资源不存在", {"resource": "run"})

    assert metadata.request_id == "req-1"
    assert error.as_dict() == {
        "code": "RESOURCE_NOT_FOUND",
        "message": "资源不存在",
        "details": {"resource": "run"},
    }
