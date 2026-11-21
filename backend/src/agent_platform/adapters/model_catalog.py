"""OpenAI 兼容模型目录读取适配器。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agent_platform.domain.model_routes import (
    ModelCatalogItem,
    ModelCatalogMode,
    ModelCatalogSource,
    ModelCatalogStatus,
    ModelConnection,
    ModelConnectionStatus,
)


class ModelCatalogError(RuntimeError):
    """目录读取错误，仅公开稳定错误类别。"""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class OpenAICompatibleModelCatalogProvider:
    """通过 GET /models 读取单个 OpenAI 兼容连接的模型目录。"""

    provider_kind = "openai-compatible"
    _safe_capability_keys = frozenset({"chat", "vision", "reasoning", "tools", "streaming", "embedding", "audio"})

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _models_url(connection: ModelConnection) -> str:
        parsed = urlsplit(connection.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ModelCatalogError("MODEL_ENDPOINT_INVALID")
        if connection.status is not ModelConnectionStatus.ACTIVE:
            raise ModelCatalogError("MODEL_CONNECTION_DISABLED")
        if connection.catalog_mode is not ModelCatalogMode.REMOTE:
            raise ModelCatalogError("MODEL_CATALOG_MANUAL")
        if connection.provider_kind not in {"openai-compatible", "direct"}:
            raise ModelCatalogError("MODEL_CATALOG_PROVIDER_UNSUPPORTED")
        return connection.endpoint.rstrip("/") + "/models"

    @classmethod
    def _catalog_item(
        cls, connection: ModelConnection, raw: Mapping[str, Any], last_seen_at: datetime
    ) -> ModelCatalogItem | None:
        model_id = raw.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        display_name = raw.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = model_id
        raw_capabilities = raw.get("capabilities")
        capabilities: dict[str, object] = {}
        if isinstance(raw_capabilities, Mapping):
            capabilities = {
                str(key): value
                for key, value in raw_capabilities.items()
                if key in cls._safe_capability_keys
                and isinstance(value, (bool, str, int, float))
            }
        context_window = raw.get("context_window")
        if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window <= 0:
            context_window = None
        return ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=connection.tenant_id,
            model_id=model_id.strip(),
            display_name=display_name.strip(),
            capabilities=capabilities,
            context_window=context_window,
            catalog_source=ModelCatalogSource.REMOTE,
            status=ModelCatalogStatus.ACTIVE,
            last_seen_at=last_seen_at,
        )

    def list_models(
        self, connection: ModelConnection, *, credential: str | None
    ) -> tuple[ModelCatalogItem, ...]:
        url = self._models_url(connection)
        headers = {"Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            category = (
                "MODEL_CREDENTIAL_INVALID"
                if error.code in {401, 403}
                else "MODEL_CATALOG_UNAVAILABLE"
            )
            raise ModelCatalogError(category) from None
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ModelCatalogError("MODEL_CATALOG_UNAVAILABLE") from None

        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise ModelCatalogError("MODEL_CATALOG_INVALID_RESPONSE")
        last_seen_at = datetime.now(UTC)
        items = tuple(
            item
            for raw in payload["data"]
            if isinstance(raw, Mapping)
            for item in (self._catalog_item(connection, raw, last_seen_at),)
            if item is not None
        )
        return items

    def __repr__(self) -> str:
        return f"OpenAICompatibleModelCatalogProvider(timeout_seconds={self._timeout_seconds!r})"
