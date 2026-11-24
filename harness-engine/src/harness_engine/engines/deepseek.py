"""DeepSeek Harness adapter.

The vendor SDK is loaded only when this adapter is constructed. The rest of
the package therefore remains usable offline and only this module knows about
``deepseek_harness.DeepSeekHarness`` or its configuration details.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Self
from uuid import uuid4

from ..core import (
    CancelResult,
    CancelStatus,
    EmployeeContext,
    EngineCapabilities,
    EngineModelConfig,
    EngineRequest,
    EngineResult,
    EngineToolDefinition,
    EventHandler,
    HarnessEvent,
    LifecycleEngine,
    RunStatus,
    SessionMismatchError,
    SessionRef,
    UnsupportedCapabilityError,
)
from ..mcp_proxy import McpProxyLaunch, schema_sha256
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
_RUN_DISABLED_TOOL_PLUGIN_IDS = (
    "attachment-local",
    "tool-fs",
    "tool-working-directory",
    "mcp-resources",
    "terminal-bash",
    "terminal-pwsh",
    "persistent-bash",
    "persistent-pwsh",
)


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


def _compose_system_prompt(
    system_prompt: str,
    skill_instructions: Sequence[str],
    skill_files: Sequence[tuple[str, str]],
    *,
    inherited_prompt: str = "",
) -> str:
    """将员工和 Skill 上下文合成为 Harness 启动时的系统提示。"""

    sections = [value.strip() for value in (inherited_prompt, system_prompt) if value.strip()]
    instructions = [value.strip() for value in skill_instructions if value.strip()]
    if instructions:
        sections.append("Skill instructions:\n" + "\n".join(instructions))
    if skill_files:
        rendered_files: list[str] = []
        for path, content in skill_files:
            if not path.strip() or "\x00" in path or not isinstance(content, str):
                raise DeepSeekConfigurationError("Skill file context is invalid")
            rendered_files.append(f"[{path}]\n{content}")
        sections.append("Skill files:\n" + "\n\n".join(rendered_files))
    return "\n\n".join(sections)


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


def _usage_mapping(raw: object) -> Mapping[str, object] | None:
    """只向平台暴露有效的 Token 计数，丢弃供应商对象和未知字段。"""

    names = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    )
    values: dict[str, object] = {}
    for name in names:
        value = raw.get(name) if isinstance(raw, Mapping) else getattr(raw, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values[name] = value
    if not any(name in values for name in ("input_tokens", "prompt_tokens")):
        return None
    if not any(name in values for name in ("output_tokens", "completion_tokens")):
        return None
    return values


def _provider_request_id(result: object) -> str | None:
    """提取可公开审计的上游请求标识，不保留原始响应对象。"""

    for name in ("provider_request_id", "request_id", "id"):
        value = getattr(result, name, None)
        if value is not None and str(value).strip():
            return str(value).strip()[:255]
    return None


def _write_provider_patch(
    config_root: Path,
    settings: Mapping[str, Any],
    *,
    api_key_available: bool = False,
) -> Path | None:
    """Create a profile patch for a configured non-DeepSeek provider."""

    base_url = settings["base_url"]
    api_key = settings["api_key"]
    provider = settings["provider"]
    if not base_url and not api_key and not api_key_available:
        return None
    if not base_url or (not api_key and not api_key_available):
        raise DeepSeekConfigurationError(
            "custom provider config requires endpoint and configured credential"
        )
    if provider == DEFAULT_PROVIDER:
        return None
    api = settings["api"]
    if api not in SUPPORTED_CUSTOM_APIS:
        raise DeepSeekConfigurationError(f"unsupported provider API protocol: {api}")

    parameters = settings.get("parameters", {})
    parameter_json = (
        json.dumps(dict(parameters), ensure_ascii=False)
        if isinstance(parameters, Mapping) and parameters
        else None
    )

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
            *([f"              params: {parameter_json}"] if parameter_json else []),
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
            *([f"            params: {parameter_json}"] if parameter_json else []),
            "",
        ]

    patch_path = config_root / "local-provider.patch.yml"
    patch_path.write_text("\n".join(rows), encoding="utf-8")
    return patch_path


def _write_mcp_patch(config_root: Path, enabled_tools: tuple[str, ...]) -> Path | None:
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
    patch_path = config_root / "mcp-tools.patch.yml"
    patch_path.write_text("\n".join(rows), encoding="utf-8")
    return patch_path


def _write_run_tool_policy_patch(config_root: Path) -> Path:
    """按当前 Run 的固定工具定义禁用 SDK 自带的通用工具。"""

    rows: list[str] = []
    for plugin_id in _RUN_DISABLED_TOOL_PLUGIN_IDS:
        rows.extend((f"- id: {plugin_id}", "  disabled: true"))
    patch_path = config_root / "run-tool-policy.patch.yml"
    patch_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return patch_path


def _write_mcp_proxy_patch(
    config_root: Path, launches: Sequence[McpProxyLaunch]
) -> tuple[Path | None, dict[str, str]]:
    """为每个固定 Profile 写独立 filtered Stdio proxy overlay。"""

    if not launches:
        return None, {}
    server_names = tuple(launch.server_name for launch in launches)
    if len(set(server_names)) != len(server_names):
        raise DeepSeekConfigurationError("MCP Run proxy server names must be unique")
    rows = ["- insert:"]
    process_environment: dict[str, str] = {}
    for index, launch in enumerate(launches):
        config_path = config_root / f"mcp-proxy-{launch.server_name}.json"
        config_path.write_text(
            json.dumps(launch.config.to_public_dict(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        proxy_module = Path(__file__).resolve().parents[1] / "mcp_proxy.py"
        proxy_args = (
            str(proxy_module),
            "--config",
            str(config_path),
        )
        patch_id = f"mcp-run-{index}-{launch.server_name}"
        rows.extend(
            [
                f"  - id: {_yaml_scalar(patch_id)}",
                f"    name: {_yaml_scalar(MCP_CLIENT_PLUGIN)}",
                "    config:",
                f"      serverName: {_yaml_scalar(launch.server_name)}",
                "      transport: stdio",
                f"      command: {_yaml_scalar(sys.executable)}",
                "      args:",
                *[f"        - {_yaml_scalar(argument)}" for argument in proxy_args],
                f"      cwd: {_yaml_scalar(str(config_root))}",
                "      failOnStartupError: true",
                f"      toolCallTimeoutMs: {launch.config.tool_timeout_ms}",
            ]
        )
        if launch.environment:
            rows.append("      env:")
            for secret_index, (alias, value) in enumerate(sorted(launch.environment.items())):
                runtime_name = (
                    "HARNESS_MCP_"
                    + hashlib.sha256(
                        f"{launch.server_name}:{alias}".encode()
                    ).hexdigest()[:16].upper()
                    + f"_{secret_index}"
                )
                process_environment[runtime_name] = value
                rows.append(
                    f"        {_yaml_scalar(alias)}: !!js process.env.{runtime_name}"
                )
        rows.append("")
    patch_path = config_root / "mcp-proxy.patch.yml"
    patch_path.write_text("\n".join(rows), encoding="utf-8")
    patch_path.chmod(0o600)
    return patch_path, process_environment


def _isolated_runtime_environment(
    run_environment: Mapping[str, str], session_home: Path
) -> dict[str, str]:
    """清空 Worker 环境中的数据库/存储凭据，只恢复 Harness 所需运行变量。"""

    safe_inherited_names = {
        "PATH",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_OPTIONS",
        "NODE_PATH",
        "DSH_BIN",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LOCAL_OPENAI_API_KEY",
        "PYTHONPATH",
    }
    isolated = {name: "" for name in os.environ}
    for name in safe_inherited_names:
        value = os.environ.get(name)
        if value is not None:
            isolated[name] = value
    isolated.update(run_environment)
    isolated["HOME"] = str(session_home)
    isolated["DSH_HOME"] = str(session_home)
    return isolated


class DeepSeekHarnessEngine(LifecycleEngine):
    """Engine-neutral wrapper around the official synchronous SDK."""

    @classmethod
    def for_run_request(
        cls,
        request: EngineRequest,
        *,
        harness_state_root: str | Path,
        profile: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        env: Mapping[str, str] | None = None,
        mcp_proxy_launches: Sequence[McpProxyLaunch] = (),
        local_config: str | Path | None = None,
        model_config: EngineModelConfig | None = None,
        initialize_timeout_seconds: float | None = None,
        request_timeout_seconds: float | None = None,
        shutdown_timeout_seconds: float | None = None,
    ) -> DeepSeekHarnessEngine:
        """从受信 RuntimeRequest 创建本 Run 独占配置和租户 Session Home。"""

        home_ref = request.harness_home_ref or ""
        match = re.fullmatch(r"harness-session:([0-9a-f]{64})", home_ref)
        if match is None:
            raise DeepSeekConfigurationError("Run 缺少受控 Harness Session Home 引用")
        if request.employee_context is None:
            raise DeepSeekConfigurationError("Run 缺少固定 EmployeeContext")
        pinned_model_config = model_config or request.model_config
        root = Path(harness_state_root).expanduser().resolve()
        sessions_root = root / "harness-sessions"
        sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        sessions_root.chmod(0o700)
        session_home = sessions_root / match.group(1)
        session_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        session_home.chmod(0o700)
        return cls(
            dsh_home=session_home,
            cwd=session_home,
            profile=profile,
            provider=(
                pinned_model_config.provider_kind
                if pinned_model_config is not None
                else provider
            ),
            # 迁移前固定在 ExecutionProfile 的 alias 仅在没有独立 ModelRoute 时兼容。
            model=(
                pinned_model_config.model_id
                if pinned_model_config is not None
                else request.employee_context.model_alias or model
            ),
            reasoning_effort=request.reasoning_effort,
            max_tokens=request.max_tokens,
            mcp_tools=tuple(
                item.name
                for item in request.tool_definitions
                if item.remote_tool_name is None
            ),
            employee_context=request.employee_context,
            tool_definitions=request.tool_definitions,
            env=env,
            mcp_proxy_launches=mcp_proxy_launches,
            local_config=local_config,
            model_config=pinned_model_config,
            initialize_timeout_seconds=initialize_timeout_seconds,
            request_timeout_seconds=request_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )

    capabilities = EngineCapabilities(
        # 固定版 DeepSeek SDK 的 Python 回调只能提供已完成的会话事件，
        # 不能提供 runtime 进程内的 ``agent/assistant-stream`` 增量。
        # 在确认有序增量传输前，不声明实时流式能力。
        streaming=False,
        resume=True,
        approvals=True,
        mcp=True,
        cancellation=True,
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
        system_prompt: str = "",
        skill_instructions: Sequence[str] = (),
        skill_files: Sequence[tuple[str, str]] = (),
        employee_context: EmployeeContext | None = None,
        tool_definitions: Sequence[EngineToolDefinition] = (),
        mcp_proxy_launches: Sequence[McpProxyLaunch] = (),
        env: Mapping[str, str] | None = None,
        local_config: str | Path | None = None,
        model_config: EngineModelConfig | None = None,
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
        resolved_base_url = (
            model_config.endpoint
            if model_config is not None
            else str(local.get("base_url") or "").strip()
        )
        # Run 有固定模型快照时，凭据只能来自 Worker 按 SecretRef 注入的环境，
        # 避免兼容旧版本地配置中的 API Key 覆盖租户连接凭据。
        resolved_api_key = (
            "" if model_config is not None else str(local.get("api_key") or "").strip()
        )
        resolved_api_key_env = str(
            "AGENT_PLATFORM_MODEL_API_KEY"
            if model_config is not None
            else local.get("api_key_env") or "LOCAL_OPENAI_API_KEY"
        ).strip()
        protocol_aliases = {
            "openai-chat-completions": "openai-completions",
            "openai-completions": "openai-completions",
            "openai-responses": "openai-responses",
            "anthropic-messages": "anthropic-messages",
        }
        resolved_api = (
            protocol_aliases.get(model_config.protocol, model_config.protocol)
            if model_config is not None
            else str(local.get("api") or "openai-completions").strip()
        )

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
            "parameters": dict(model_config.parameters) if model_config is not None else {},
        }
        resolved_env = dict(env or {})
        inherited_system_prompt = resolved_env.pop("DSH_SYSTEM_PROMPT", "")
        definitions = tuple(tool_definitions)
        if any(not isinstance(item, EngineToolDefinition) for item in definitions):
            raise TypeError("tool_definitions must contain EngineToolDefinition values")
        if len({item.name for item in definitions}) != len(definitions):
            raise DeepSeekConfigurationError("Run tool definitions must have unique names")
        configured_tools = tuple(str(name).strip() for name in mcp_tools)
        proxy_launches = tuple(mcp_proxy_launches)
        remote_definitions: tuple[EngineToolDefinition, ...] = ()
        if definitions:
            builtin_definitions = tuple(
                item for item in definitions if item.remote_tool_name is None
            )
            remote_definitions = tuple(
                item for item in definitions if item.remote_tool_name is not None
            )
            builtin_names = tuple(item.name for item in builtin_definitions)
            if configured_tools and configured_tools != builtin_names:
                raise DeepSeekConfigurationError(
                    "demo MCP names must match builtin Run tool definitions in order"
                )
            configured_tools = builtin_names
        if remote_definitions and not proxy_launches:
            raise DeepSeekConfigurationError(
                "MCP ToolVersion definitions require a filtered Stdio proxy"
            )
        if proxy_launches:
            definition_by_remote: dict[str, EngineToolDefinition] = {}
            for definition in remote_definitions:
                remote_name = str(definition.remote_tool_name)
                if remote_name in definition_by_remote:
                    raise DeepSeekConfigurationError(
                        "Run MCP remote tool names must be unique across Profiles"
                    )
                definition_by_remote[remote_name] = definition
            proxy_allowed: set[str] = set()
            for launch in proxy_launches:
                if proxy_allowed.intersection(launch.config.run_allowed_tool_names):
                    raise DeepSeekConfigurationError(
                        "Run MCP proxy tool allowlists must not overlap"
                    )
                proxy_allowed.update(launch.config.run_allowed_tool_names)
                for remote_name, expected_digest in (
                    launch.config.expected_tool_schema_sha256.items()
                ):
                    definition = definition_by_remote.get(remote_name)
                    if definition is None or schema_sha256(definition.input_schema) != expected_digest:
                        raise DeepSeekConfigurationError(
                            "MCP proxy schema digests must match the fixed RunSnapshot tools"
                        )
            if proxy_allowed != set(definition_by_remote):
                raise DeepSeekConfigurationError(
                    "MCP proxy allowlists must match the fixed RunSnapshot remote tools"
                )
        if employee_context is not None:
            if system_prompt and system_prompt != employee_context.system_prompt:
                raise DeepSeekConfigurationError(
                    "Harness system prompt must match the fixed EmployeeContext"
                )
            if skill_instructions and tuple(skill_instructions) != employee_context.skill_instructions:
                raise DeepSeekConfigurationError(
                    "Harness Skill instructions must match the fixed EmployeeContext"
                )
            if skill_files:
                supplied_files = tuple((path, content) for path, content in skill_files)
                context_files = tuple((item.path, item.content) for item in employee_context.skill_files)
                if supplied_files != context_files:
                    raise DeepSeekConfigurationError(
                        "Harness Skill files must match the fixed EmployeeContext"
                    )
            system_prompt = employee_context.system_prompt
            skill_instructions = employee_context.skill_instructions
            skill_files = tuple(
                (item.path, item.content) for item in employee_context.skill_files
            )
        if definitions and any(item.risk_level not in {"READ", "PREVIEW"} for item in definitions):
            raise DeepSeekConfigurationError("Run tools must be READ or PREVIEW")
        composed_system_prompt = _compose_system_prompt(
            system_prompt,
            skill_instructions,
            skill_files,
            inherited_prompt=inherited_system_prompt,
        )
        if composed_system_prompt:
            resolved_env["DSH_SYSTEM_PROMPT"] = composed_system_prompt
        self._run_config_directory = tempfile.TemporaryDirectory(
            prefix="agent-harness-run-config-"
        )
        run_config_root = Path(self._run_config_directory.name)
        provider_patch = _write_provider_patch(
            run_config_root,
            settings,
            api_key_available=bool(resolved_env.get(resolved_api_key_env)),
        )
        mcp_patches: list[Path] = []
        mcp_patch = _write_mcp_patch(run_config_root, configured_tools)
        if mcp_patch is not None:
            mcp_patches.append(mcp_patch)
        if proxy_launches:
            proxy_patch, proxy_environment = _write_mcp_proxy_patch(
                run_config_root, proxy_launches
            )
            resolved_env.update(proxy_environment)
            if proxy_patch is not None:
                mcp_patches.append(proxy_patch)
        run_tool_policy_patch = _write_run_tool_policy_patch(run_config_root)

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
            if resolved_api_key:
                resolved_env[resolved_api_key_env] = resolved_api_key
        if mcp_patches:
            existing_patches = tuple(harness_kwargs.get("patches", ()))
            harness_kwargs["patches"] = (*existing_patches, *(str(item) for item in mcp_patches))
        existing_patches = tuple(harness_kwargs.get("patches", ()))
        harness_kwargs["patches"] = (*existing_patches, str(run_tool_policy_patch))
        harness_kwargs["env"] = _isolated_runtime_environment(
            resolved_env, resolved_home
        )
        if initialize_timeout_seconds is not None:
            harness_kwargs["initialize_timeout_seconds"] = initialize_timeout_seconds
        if request_timeout_seconds is not None:
            harness_kwargs["request_timeout_seconds"] = request_timeout_seconds
        if shutdown_timeout_seconds is not None:
            harness_kwargs["shutdown_timeout_seconds"] = shutdown_timeout_seconds

        super().__init__()
        self._harness = DeepSeekHarness(**harness_kwargs)
        self._sessions: dict[str, Any] = {}
        self._employee_context = employee_context
        self._tool_definitions = definitions
        self._active_runs: dict[str, str] = {}
        self._cancel_requested: set[str] = set()
        self._run_lock = RLock()
        self._closed = False

    def create_session(self, session_id: str | None = None) -> SessionRef:
        self._require_started()
        start_session = getattr(self._harness, "start_session", None)
        if not callable(start_session):
            raise UnsupportedCapabilityError(
                "DeepSeek Harness SDK has no verified start_session operation"
            )
        generated_id = session_id or f"deepseek-{uuid4().hex}"
        session = start_session(generated_id)
        session_id = str(getattr(session, "session_id", generated_id))
        if not callable(getattr(session, "run", None)):
            raise DeepSeekConfigurationError(
                "DeepSeek Harness SDK start_session returned an invalid Session"
            )
        self._sessions[session_id] = session
        return SessionRef(session_id=session_id, engine_name="deepseek")

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
            raise DeepSeekConfigurationError(
                "DeepSeek Harness SDK returned a platform SessionRef instead of an SDK Session"
            )
        sdk_session_id = getattr(raw_ref, "session_id", None)
        if sdk_session_id is None and isinstance(raw_ref, str):
            sdk_session_id = raw_ref
        if not callable(getattr(raw_ref, "run", None)):
            raise DeepSeekConfigurationError(
                "DeepSeek Harness SDK resume_session returned an invalid Session"
            )
        resolved_session_id = str(sdk_session_id or ref.session_id)
        self._sessions[resolved_session_id] = raw_ref
        return SessionRef(
            session_id=resolved_session_id,
            engine_name="deepseek",
            metadata=ref.metadata,
        )

    def run(
        self,
        request: EngineRequest,
        on_event: EventHandler | None = None,
    ) -> EngineResult:
        self._require_started()
        if request.employee_context != self._employee_context:
            raise UnsupportedCapabilityError(
                "DeepSeek Harness instance does not match this Run's EmployeeContext"
            )
        if tuple(request.tool_definitions) != self._tool_definitions:
            raise UnsupportedCapabilityError(
                "DeepSeek Harness instance does not match this Run's tool allowlist"
            )
        session_ref = request.session
        session_id = request.resolved_session_id
        if session_ref is not None and session_ref.engine_name != "deepseek":
            raise SessionMismatchError(
                f"session belongs to engine {session_ref.engine_name!r}, not 'deepseek'"
            )
        if session_id is None:
            session_ref = self.create_session()
            session_id = session_ref.session_id
        elif session_id.startswith("harness-pending:"):
            pending_id = session_id.removeprefix("harness-pending:")
            session_id = f"deepseek-{pending_id}"
            session_ref = self.create_session(session_id)
        sdk_session = self._sessions.get(session_id)
        if sdk_session is None:
            self.resume_session(
                session_ref
                or SessionRef(session_id=session_id, engine_name="deepseek")
            )
            sdk_session = self._sessions[session_id]
        run_id = request.run_id or f"deepseek-run-{uuid4().hex}"
        with self._run_lock:
            self._active_runs[session_id] = run_id
        try:
            result = sdk_session.run(request.prompt)
        except Exception:
            with self._run_lock:
                cancelled = session_id in self._cancel_requested
            if not cancelled:
                raise
            return EngineResult(
                text="",
                finish_reason="cancelled",
                completed_at=datetime.now(UTC),
                capabilities=self.capabilities,
                run_id=run_id,
                session=SessionRef(session_id=session_id, engine_name="deepseek"),
                status=RunStatus.CANCELLED,
            )
        finally:
            with self._run_lock:
                self._active_runs.pop(session_id, None)
                self._cancel_requested.discard(session_id)
        final_response = str(
            getattr(result, "final_response", None)
            or getattr(result, "text", None)
            or ""
        )
        finish_reason = getattr(result, "finish_reason", None)
        # OpenAI-compatible端点常用 stop/length/tool_calls，部分 SDK 只返回最终正文。
        # 有正文且没有明确失败原因时按成功收敛，避免把真实答复错误标成 UNKNOWN。
        if not finish_reason and final_response.strip():
            finish_reason = "completed"
        finish_reason = finish_reason or "unknown"
        finish_reason_text = str(finish_reason)
        if finish_reason_text in {"completed", "succeeded", "stop", "length", "tool_calls", "content_filter"}:
            status = RunStatus.SUCCEEDED
        elif finish_reason_text in {"cancelled", "canceled"}:
            status = RunStatus.CANCELLED
        elif finish_reason_text == "unknown":
            status = RunStatus.UNKNOWN
        else:
            status = RunStatus.FAILED
        run_id = str(
            getattr(result, "run_id", None)
            or run_id
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
            text=final_response,
            finish_reason=finish_reason_text,
            completed_at=datetime.now(UTC),
            capabilities=self.capabilities,
            run_id=run_id,
            session=session_ref
            or SessionRef(session_id=session_id, engine_name="deepseek"),
            status=status,
            usage=_usage_mapping(getattr(result, "usage", None)),
            provider_request_id=_provider_request_id(result),
        )

    def cancel(self, session_id: str) -> CancelResult:
        self._require_started()
        with self._run_lock:
            run_id = self._active_runs.get(session_id)
            if run_id is None:
                return CancelResult(
                    status=CancelStatus.NOT_RUNNING,
                    session_id=session_id,
                )
            self._cancel_requested.add(session_id)
        # 此 Engine 仅服务一个 Run；关闭 SDK 进程会停止该 Run 的 Harness/MCP client。
        self.close()
        return CancelResult(
            status=CancelStatus.CANCELLED,
            session_id=session_id,
            run_id=run_id,
        )

    def _close_impl(self) -> None:
        try:
            self._harness.close()
        finally:
            sdk_config = getattr(self._harness, "config", None)
            sdk_environment = getattr(sdk_config, "env", None)
            clear_environment = getattr(sdk_environment, "clear", None)
            if callable(clear_environment):
                clear_environment()
            self._sessions.clear()
            self._closed = True
            self._run_config_directory.cleanup()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
