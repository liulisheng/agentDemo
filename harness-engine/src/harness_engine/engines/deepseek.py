"""DeepSeek Harness adapter.

The vendor SDK is loaded only when this adapter is constructed. The rest of
the package therefore remains usable offline and only this module knows about
``deepseek_harness.DeepSeekHarness`` or its configuration details.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from ..core import (
    CancelResult,
    CancelStatus,
    EngineCapabilities,
    EngineRequest,
    EngineResult,
    EventHandler,
    HarnessEvent,
    LifecycleEngine,
    RunStatus,
    SessionMismatchError,
    SessionRef,
    UnsupportedCapabilityError,
)
from ..mcp_server import stdio_command

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PROFILE = "sdk-minimal"
DEFAULT_PROVIDER = "deepseek-official"
SUPPORTED_CUSTOM_APIS = {
    "openai-completions",
    "openai-responses",
    "anthropic-messages",
}
MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client"
MCP_SERVER_ID = "mcp-python-tools"
MCP_SERVER_NAME = "python-tools"


def _coerce_event(raw: Any, *, session_id: str | None, run_id: str) -> HarnessEvent:
    """Convert an SDK event without exposing its vendor object to callers."""

    if isinstance(raw, HarnessEvent):
        return raw
    if isinstance(raw, Mapping):
        event_type = str(raw.get("event_type") or raw.get("type") or "vendor_event")
        event_session_id = raw.get("session_id", session_id)
        event_run_id = raw.get("run_id", run_id)
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            payload = {
                str(key): value
                for key, value in raw.items()
                if key not in {"event_type", "type", "session_id", "run_id"}
            }
    else:
        event_type = str(
            getattr(raw, "event_type", None)
            or getattr(raw, "type", None)
            or "vendor_event"
        )
        event_session_id = getattr(raw, "session_id", None) or session_id
        event_run_id = getattr(raw, "run_id", None) or run_id
        payload = getattr(raw, "payload", None)
        if not isinstance(payload, Mapping):
            payload = getattr(raw, "data", None)
        if not isinstance(payload, Mapping):
            payload = {}
    return HarnessEvent(
        event_type=event_type,
        session_id=str(event_session_id) if event_session_id is not None else None,
        run_id=str(event_run_id) if event_run_id is not None else None,
        payload=dict(payload),
    )


class DeepSeekSdkUnavailable(RuntimeError):
    """Raised when the optional DeepSeek SDK is unavailable."""


class DeepSeekConfigurationError(ValueError):
    """Raised when the adapter configuration cannot create a Harness client."""


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_local_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeepSeekConfigurationError(
            f"local config is not valid JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise DeepSeekConfigurationError(
            f"local config must contain a JSON object: {path}"
        )
    return value


def _write_provider_patch(dsh_home: Path, settings: Mapping[str, str]) -> Path | None:
    """Create a profile patch for a configured non-DeepSeek provider."""

    base_url = settings["base_url"]
    api_key = settings["api_key"]
    provider = settings["provider"]
    if not base_url and not api_key:
        return None
    if not base_url or not api_key:
        raise DeepSeekConfigurationError(
            "custom provider config requires both base_url and api_key"
        )
    if provider == DEFAULT_PROVIDER:
        return None
    api = settings["api"]
    if api not in SUPPORTED_CUSTOM_APIS:
        raise DeepSeekConfigurationError(f"unsupported provider API protocol: {api}")

    if settings["profile"] == "sdk-minimal":
        rows = [
            "- insert:",
            "  - id: llm-pi-ai",
            "    name: '@deepseek-ai/dsh-llm-pi-ai'",
            "    config:",
            "      providers:",
            f"        {_yaml_scalar(provider)}:",
            f"          apiKeyEnv: {_yaml_scalar(settings['api_key_env'])}",
            f"          api: {_yaml_scalar(api)}",
            f"          baseURL: {_yaml_scalar(base_url)}",
            "          models:",
            f"            - id: {_yaml_scalar(settings['model'])}",
            "",
        ]
    else:
        rows = [
            "- id: llm-pi-ai",
            "  config:",
            "    providers:",
            f"      {_yaml_scalar(provider)}:",
            f"        apiKeyEnv: {_yaml_scalar(settings['api_key_env'])}",
            f"        api: {_yaml_scalar(api)}",
            f"        baseURL: {_yaml_scalar(base_url)}",
            "        models:",
            f"          - id: {_yaml_scalar(settings['model'])}",
            "",
        ]

    patch_path = dsh_home / "local-provider.patch.yml"
    patch_path.write_text("\n".join(rows), encoding="utf-8")
    return patch_path


def _write_mcp_patch(dsh_home: Path, enabled_tools: tuple[str, ...]) -> Path | None:
    """Create a profile patch for the local Python MCP stdio service."""

    if not enabled_tools:
        return None
    command = stdio_command(enabled_tools)
    rows = [
        "- insert:",
        f"  - id: {MCP_SERVER_ID}",
        f"    name: {_yaml_scalar(MCP_CLIENT_PLUGIN)}",
        "    config:",
        f"      serverName: {_yaml_scalar(MCP_SERVER_NAME)}",
        "      transport: stdio",
        f"      command: {_yaml_scalar(command[0])}",
        "      args:",
    ]
    rows.extend(f"        - {_yaml_scalar(argument)}" for argument in command[1:])
    rows.extend(
        [
            "      failOnStartupError: true",
            "",
        ]
    )
    patch_path = dsh_home / "mcp-tools.patch.yml"
    patch_path.write_text("\n".join(rows), encoding="utf-8")
    return patch_path


class DeepSeekHarnessEngine(LifecycleEngine):
    """Engine-neutral wrapper around the official synchronous SDK."""

    capabilities = EngineCapabilities(
        # 固定版 DeepSeek SDK 的 Python 回调只能提供已完成的会话事件，
        # 不能提供 runtime 进程内的 ``agent/assistant-stream`` 增量。
        # 在确认有序增量传输前，不声明实时流式能力。
        streaming=False,
        resume=False,
        approvals=True,
        mcp=True,
    )

    def __init__(
        self,
        *,
        dsh_home: str | Path | None = None,
        cwd: str | Path | None = None,
        profile: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        patches: tuple[str, ...] = (),
        mcp_tools: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        local_config: str | Path | None = None,
        initialize_timeout_seconds: float | None = None,
        request_timeout_seconds: float | None = None,
        shutdown_timeout_seconds: float | None = None,
    ) -> None:
        try:
            from deepseek_harness import DeepSeekHarness
        except ImportError as exc:
            raise DeepSeekSdkUnavailable(
                "install the pinned deepseek-harness-sdk version before using this adapter"
            ) from exc

        config_path = Path(local_config).expanduser().resolve() if local_config else None
        local = _load_local_config(config_path)
        resolved_provider = str(provider or local.get("provider") or DEFAULT_PROVIDER)
        resolved_model = str(model or local.get("model") or DEFAULT_MODEL)
        resolved_profile = str(
            profile or local.get("profile") or ("sdk" if local else DEFAULT_PROFILE)
        )
        resolved_base_url = str(local.get("base_url") or "").strip()
        resolved_api_key = str(local.get("api_key") or "").strip()
        resolved_api_key_env = str(
            local.get("api_key_env") or "LOCAL_OPENAI_API_KEY"
        ).strip()
        resolved_api = str(local.get("api") or "openai-completions").strip()

        if dsh_home is None:
            configured_home = os.environ.get("DSH_HOME", "").strip()
            if not configured_home:
                raise DeepSeekConfigurationError(
                    "dsh_home is required; pass --dsh-home or set DSH_HOME"
                )
            resolved_home = Path(configured_home).expanduser().resolve()
        else:
            resolved_home = Path(dsh_home).expanduser().resolve()
        resolved_home.mkdir(parents=True, exist_ok=True)

        settings = {
            "profile": resolved_profile,
            "provider": resolved_provider,
            "model": resolved_model,
            "base_url": resolved_base_url,
            "api_key": resolved_api_key,
            "api_key_env": resolved_api_key_env,
            "api": resolved_api,
        }
        provider_patch = _write_provider_patch(resolved_home, settings)
        mcp_patch = _write_mcp_patch(resolved_home, tuple(mcp_tools))

        harness_kwargs: dict[str, Any] = {
            "dsh_home": str(resolved_home),
            "profile": resolved_profile,
            "provider": resolved_provider,
            "model": resolved_model,
        }
        if cwd is not None:
            harness_kwargs["cwd"] = str(Path(cwd).expanduser().resolve())
        configured_dsh_bin = os.environ.get("DSH_BIN", "").strip()
        if configured_dsh_bin:
            harness_kwargs["dsh_bin"] = str(Path(configured_dsh_bin).expanduser().resolve())
        if reasoning_effort is not None:
            harness_kwargs["reasoning_effort"] = reasoning_effort
        if max_tokens is not None:
            if max_tokens <= 0:
                raise DeepSeekConfigurationError("max_tokens must be positive")
            harness_kwargs["max_tokens"] = max_tokens
        if patches:
            harness_kwargs["patches"] = tuple(
                str(Path(p).expanduser().resolve()) for p in patches
            )
        if provider_patch is not None:
            existing_patches = tuple(harness_kwargs.get("patches", ()))
            harness_kwargs["patches"] = (*existing_patches, str(provider_patch))
            injected_env = dict(env or {})
            injected_env[resolved_api_key_env] = resolved_api_key
            harness_kwargs["env"] = injected_env
        elif env is not None:
            harness_kwargs["env"] = dict(env)
        if mcp_patch is not None:
            existing_patches = tuple(harness_kwargs.get("patches", ()))
            harness_kwargs["patches"] = (*existing_patches, str(mcp_patch))
        if initialize_timeout_seconds is not None:
            harness_kwargs["initialize_timeout_seconds"] = initialize_timeout_seconds
        if request_timeout_seconds is not None:
            harness_kwargs["request_timeout_seconds"] = request_timeout_seconds
        if shutdown_timeout_seconds is not None:
            harness_kwargs["shutdown_timeout_seconds"] = shutdown_timeout_seconds

        super().__init__()
        self._harness = DeepSeekHarness(**harness_kwargs)
        self._closed = False

    def create_session(self) -> SessionRef:
        self._require_started()
        return SessionRef(
            session_id=f"deepseek-{uuid4().hex}",
            engine_name="deepseek",
        )

    def resume_session(self, ref: SessionRef) -> SessionRef:
        self._require_started()
        if ref.engine_name != "deepseek":
            raise SessionMismatchError(
                f"session belongs to engine {ref.engine_name!r}, not 'deepseek'"
            )
        resume = getattr(self._harness, "resume_session", None)
        if not callable(resume):
            raise UnsupportedCapabilityError(
                "DeepSeek Harness SDK has no verified resume operation"
            )
        raw_ref = resume(ref.session_id)
        if isinstance(raw_ref, SessionRef):
            return raw_ref
        sdk_session_id = getattr(raw_ref, "session_id", None)
        if sdk_session_id is None and isinstance(raw_ref, str):
            sdk_session_id = raw_ref
        return SessionRef(
            session_id=str(sdk_session_id or ref.session_id),
            engine_name="deepseek",
            metadata=ref.metadata,
        )

    def run(
        self,
        request: EngineRequest,
        on_event: EventHandler | None = None,
    ) -> EngineResult:
        self._require_started()
        session = request.session
        if session is not None and session.engine_name != "deepseek":
            raise SessionMismatchError(
                f"session belongs to engine {session.engine_name!r}, not 'deepseek'"
            )
        session_id = request.resolved_session_id
        result = self._harness.run(request.prompt, session_id=session_id)
        finish_reason = getattr(result, "finish_reason", None) or "unknown"
        finish_reason_text = str(finish_reason)
        if finish_reason_text in {"completed", "succeeded"}:
            status = RunStatus.SUCCEEDED
        elif finish_reason_text in {"cancelled", "canceled"}:
            status = RunStatus.CANCELLED
        elif finish_reason_text == "unknown":
            status = RunStatus.UNKNOWN
        else:
            status = RunStatus.FAILED
        run_id = str(
            getattr(result, "run_id", None)
            or request.run_id
            or f"deepseek-run-{uuid4().hex}"
        )
        if on_event is not None:
            for event in getattr(result, "events", ()) or ():
                on_event(
                    _coerce_event(
                        event,
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
        return EngineResult(
            text=str(getattr(result, "final_response", "")),
            finish_reason=finish_reason_text,
            completed_at=datetime.now(UTC),
            capabilities=self.capabilities,
            run_id=run_id,
            session=session
            or (
                SessionRef(session_id=session_id, engine_name="deepseek")
                if session_id is not None
                else None
            ),
            status=status,
        )

    def cancel(self, session_id: str) -> CancelResult:
        self._require_started()
        cancel = getattr(self._harness, "cancel", None)
        if not callable(cancel):
            cancel = getattr(self._harness, "cancel_session", None)
        if not callable(cancel):
            raise UnsupportedCapabilityError(
                "DeepSeek Harness SDK has no verified cancel operation"
            )
        raw_result = cancel(session_id)
        if isinstance(raw_result, CancelResult):
            return raw_result
        status = CancelStatus.CANCELLED if raw_result is True else CancelStatus.UNKNOWN
        return CancelResult(status=status, session_id=session_id)

    def _close_impl(self) -> None:
        self._harness.close()
        self._closed = True

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
