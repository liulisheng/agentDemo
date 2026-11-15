"""环境 SecretProvider 必须按版本恢复仍被对象引用的旧密钥。"""

import pytest

from agent_platform.adapters.secrets import EnvironmentSecretProvider


def test_environment_secret_provider_resolves_current_and_previous_key_versions(monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_KEY_B64", "current-key")
    monkeypatch.setenv("OBJECT_KEY_B64_V1", "previous-key")
    provider = EnvironmentSecretProvider(
        "secret/object-key", current_key_version="v2", environment_variable="OBJECT_KEY_B64"
    )

    assert provider.resolve("secret/object-key@v1")["key_base64"] == "previous-key"
    assert provider.resolve("secret/object-key@v2")["key_base64"] == "current-key"
    with pytest.raises(KeyError):
        provider.resolve("secret/other-key@v2")
