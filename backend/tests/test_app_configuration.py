"""应用模式必须明确，MySQL 依赖不允许静默回退到 Fake。"""

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app, create_app_from_environment
from agent_platform.application.catalog_service import InMemoryCatalogRepository


def test_mysql_mode_fails_closed_without_all_persistent_adapters() -> None:
    with pytest.raises(ValueError, match="完整注入"):
        create_app(mode="mysql")


def test_environment_entrypoint_requires_explicit_mode(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PLATFORM_MODE", raising=False)
    with pytest.raises(RuntimeError, match="显式设置"):
        create_app_from_environment()


def test_environment_fake_mode_is_explicit_and_needs_no_mysql(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_MODE", "fake")
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    with TestClient(create_app_from_environment()) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_environment_mysql_mode_requires_explicit_connection_config(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PLATFORM_MODE", "mysql")
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_PROVIDER", "fake")
    for name in ("MYSQL_HOST", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="缺少环境变量"):
        create_app_from_environment()


def test_mysql_application_closes_resources_on_shutdown() -> None:
    class Resource:
        closed = 0

        def close(self) -> None:
            self.closed += 1

    resource = Resource()
    app = create_app(
        mode="mysql",
        catalog_repository=InMemoryCatalogRepository(),
        execution_repository=object(),
        conversation_repository=object(),
        resource_center_repository=object(),
        object_store=object(),
        lifecycle_resources=(resource,),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
    assert resource.closed == 1
