import pytest

from agent_platform.adapters.runtime.registry import RuntimeRegistry, UnsupportedRuntimeError


def test_registry_exposes_fake_and_explicitly_unavailable_deepseek() -> None:
    registry = RuntimeRegistry.default()
    assert "fake" in registry.names()
    assert "deepseek" in registry.names()
    assert registry.get("fake").create_session()
    with pytest.raises(UnsupportedRuntimeError):
        registry.get("deepseek").create_session()
