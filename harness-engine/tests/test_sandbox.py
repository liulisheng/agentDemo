import pytest

from harness_engine.sandbox import (
    ContainerSecurityPolicy,
    SandboxSpec,
    SandboxUnavailable,
)


def test_sandbox_spec_requires_digest_and_positive_limits():
    with pytest.raises(ValueError, match="digest"):
        SandboxSpec(image="worker:latest", cpu_millis=500, memory_mb=512)

    with pytest.raises(ValueError, match="memory"):
        SandboxSpec(image="worker@sha256:abc", cpu_millis=500, memory_mb=0)


def test_default_container_policy_disallows_privilege_and_host_access():
    policy = ContainerSecurityPolicy.default()

    assert policy.run_as_non_root is True
    assert policy.allow_privilege_escalation is False
    assert policy.read_only_rootfs is True
    assert policy.host_network is False
    assert policy.host_path is False
    assert policy.capabilities_drop_all is True


def test_unavailable_provider_is_explicit():
    error = SandboxUnavailable("kubernetes provider is unavailable")

    assert str(error) == "kubernetes provider is unavailable"
