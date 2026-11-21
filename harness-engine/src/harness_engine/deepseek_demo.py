"""Run a small, isolated DeepSeek Harness SDK validation.

This module deliberately talks to the vendor SDK directly.  It is a validation
spike, so the result is reported as JSON and no SDK type crosses into the
engine-neutral contracts in :mod:`harness_engine.core`.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import tempfile
import traceback
import uuid
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_PROFILE = "sdk-minimal"
DEFAULT_PROVIDER = "deepseek-official"
DEFAULT_LOCAL_CONFIG = Path(".artifacts/local-openai.json")
SDK_DISTRIBUTIONS = (
    "deepseek-harness-sdk",
    "deepseek-harness-runtime-bin",
)


class MissingSdkError(RuntimeError):
    """Raised when the optional DeepSeek SDK is not installed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepseek-harness-verify",
        description="Run an isolated DeepSeek Harness SDK startup and prompt check.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Reply with exactly: deepseek-harness-ok",
        help="prompt sent to the Harness runtime (unless --startup-only is set)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="agent workspace; it must already exist (default: current directory)",
    )
    parser.add_argument(
        "--dsh-home",
        type=Path,
        help="explicit Harness home; omitted means a temporary home removed on exit",
    )
    parser.add_argument("--local-config", type=Path, default=DEFAULT_LOCAL_CONFIG)
    parser.add_argument("--profile")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument(
        "--session-id",
        default=None,
        help="session id; omitted means a fresh verify-<uuid> id",
    )
    parser.add_argument(
        "--startup-only",
        action="store_true",
        help="start and close the runtime without making a model request",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path for the JSON report",
    )
    parser.add_argument(
        "--verbose-errors",
        action="store_true",
        help="include a traceback in a failed report",
    )
    return parser


def _distribution_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in SDK_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    """Convert SDK result values into bounded JSON-safe data."""

    if depth > 4:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth=depth + 1) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(), depth=depth + 1)
        except (TypeError, ValueError, AttributeError):
            pass
    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value), depth=depth + 1)
        except TypeError:
            pass
    return str(value)


def _load_harness() -> Any:
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError as exc:
        raise MissingSdkError(
            "DeepSeek Harness SDK is unavailable. Install the SDK wheel with "
            "`python -m pip install deepseek-harness-sdk`; it supplies the "
            "matching deepseek-harness-runtime-bin wheel."
        ) from exc
    return DeepSeekHarness


def _load_local_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"local config is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"local config must contain a JSON object: {path}")
    return value


def _resolve_settings(
    args: argparse.Namespace, local_config: dict[str, Any]
) -> dict[str, Any]:
    provider = str(args.provider or local_config.get("provider") or DEFAULT_PROVIDER)
    model = str(
        args.model
        or local_config.get("model")
        or os.environ.get("DSH_MODEL", DEFAULT_MODEL)
    )
    profile = str(
        args.profile
        or local_config.get("profile")
        or ("sdk" if local_config else DEFAULT_PROFILE)
    )
    return {
        "profile": profile,
        "provider": provider,
        "model": model,
        "base_url": str(local_config.get("base_url", "")).strip(),
        "api_key": str(local_config.get("api_key", "")).strip(),
        "api_key_env": str(
            local_config.get("api_key_env") or "LOCAL_OPENAI_API_KEY"
        ).strip(),
        "api": str(local_config.get("api") or "openai-completions").strip(),
    }


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_provider_patch(dsh_home: Path, settings: dict[str, Any]) -> Path | None:
    if not settings["base_url"] and not settings["api_key"]:
        return None
    if not settings["base_url"] or not settings["api_key"]:
        raise ValueError(
            "custom provider config requires both base_url and api_key"
        )
    if settings["provider"] == DEFAULT_PROVIDER:
        return None
    if settings["api"] not in {
        "openai-completions",
        "openai-responses",
        "anthropic-messages",
    }:
        raise ValueError(f"unsupported custom provider API protocol: {settings['api']}")

    provider = _yaml_scalar(settings["provider"])
    if settings["profile"] == "sdk-minimal":
        row = [
            "- insert:",
            "  - id: llm-pi-ai",
            "    name: '@deepseek-ai/dsh-llm-pi-ai'",
            "    config:",
            "      providers:",
            f"        {provider}:",
            f"          apiKeyEnv: {_yaml_scalar(settings['api_key_env'])}",
            f"          api: {_yaml_scalar(settings['api'])}",
            f"          baseURL: {_yaml_scalar(settings['base_url'])}",
            "          models:",
            f"            - id: {_yaml_scalar(settings['model'])}",
            "",
        ]
    else:
        row = [
            "- id: llm-pi-ai",
            "  config:",
            "    providers:",
            f"      {provider}:",
            f"        apiKeyEnv: {_yaml_scalar(settings['api_key_env'])}",
            f"        api: {_yaml_scalar(settings['api'])}",
            f"        baseURL: {_yaml_scalar(settings['base_url'])}",
            "        models:",
            f"          - id: {_yaml_scalar(settings['model'])}",
            "",
        ]
    patch = "\n".join(row)
    patch_path = dsh_home / "local-provider.patch.yml"
    patch_path.write_text(patch, encoding="utf-8")
    return patch_path


def _validate_paths(workspace: Path, dsh_home: Path) -> None:
    if not workspace.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {workspace}")
    dsh_home.mkdir(parents=True, exist_ok=True)


def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    workspace = args.workspace.expanduser().resolve()
    session_id = args.session_id or f"verify-{uuid.uuid4().hex}"
    local_config_path = args.local_config.expanduser().resolve()
    try:
        local_config = _load_local_config(local_config_path)
        settings = _resolve_settings(args, local_config)
    except Exception as exc:  # noqa: BLE001 — Demo 输出稳定结构化 SDK/运行时失败。
        return 1, {
            "status": "failed",
            "phase": "preflight",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "local_config": str(local_config_path),
        }
    credential_env = settings["api_key_env"] if local_config else "DEEPSEEK_API_KEY"
    api_key_configured = bool(
        settings["api_key"] or os.environ.get(credential_env)
    )
    report: dict[str, Any] = {
        "status": "failed",
        "phase": "preflight",
        "workspace": str(workspace),
        "profile": settings["profile"],
        "provider": settings["provider"],
        "model": settings["model"],
        "session_id": session_id,
        "startup_only": args.startup_only,
        "api_key_configured": api_key_configured,
        "base_url_configured": bool(
            settings["base_url"] or os.environ.get("DEEPSEEK_BASE_URL")
        ),
        "local_config": str(local_config_path) if local_config else None,
        "sdk_distributions": _distribution_versions(),
    }

    try:
        harness_type = _load_harness()
    except MissingSdkError as exc:
        report.update(
            status="blocked",
            error_type=type(exc).__name__,
            error=str(exc),
            install_command="python -m pip install deepseek-harness-sdk",
        )
        return 2, report

    with ExitStack() as stack:
        if args.dsh_home is None:
            dsh_home = Path(
                stack.enter_context(tempfile.TemporaryDirectory(prefix="dsh-verify-"))
            ).resolve()
            report["temporary_dsh_home"] = True
        else:
            dsh_home = args.dsh_home.expanduser().resolve()
            report["temporary_dsh_home"] = False
        report["dsh_home"] = str(dsh_home)

        try:
            _validate_paths(workspace, dsh_home)
            report["phase"] = "startup"
            harness_kwargs: dict[str, Any] = {
                "dsh_home": str(dsh_home),
                "cwd": str(workspace),
                "profile": settings["profile"],
                "provider": settings["provider"],
                "model": settings["model"],
            }
            patch_path = _write_provider_patch(dsh_home, settings)
            if patch_path is not None:
                harness_kwargs["patches"] = (str(patch_path),)
                harness_kwargs["env"] = {settings["api_key_env"]: settings["api_key"]}
                report["provider_patch"] = str(patch_path)
            if args.max_tokens is not None:
                if args.max_tokens <= 0:
                    raise ValueError("--max-tokens must be positive")
                harness_kwargs["max_tokens"] = args.max_tokens

            with harness_type(**harness_kwargs) as harness:
                report["startup"] = "passed"
                if args.startup_only:
                    report["model_call"] = "skipped"
                else:
                    report["phase"] = "model_call"
                    result = harness.run(args.prompt, session_id=session_id)
                    final_response = getattr(result, "final_response", "")
                    finish_reason = getattr(result, "finish_reason", None)
                    report["final_response"] = str(final_response)
                    report["finish_reason"] = _jsonable(finish_reason)
                    events = getattr(result, "events", ()) or ()
                    report["event_count"] = len(events)
                    report["notification_count"] = len(
                        getattr(result, "notifications", ()) or ()
                    )
                    if finish_reason != "completed":
                        report["event_tail"] = _jsonable(list(events)[-5:])
                        raise RuntimeError(
                            "Harness returned a non-completed finish_reason: "
                            f"{finish_reason!r}"
                        )
                    if not str(final_response).strip():
                        raise RuntimeError("Harness returned an empty final_response")
                    report["model_call"] = "passed"
            report["close"] = "passed"
            report["status"] = "passed"
            report["phase"] = "complete"
            return 0, report
        except Exception as exc:  # noqa: BLE001 — Demo 输出稳定结构化运行时失败。
            report.update(
                phase=report.get("phase", "runtime"),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            if args.verbose_errors:
                report["traceback"] = traceback.format_exc()
            return 1, report


def _write_report(report: dict[str, Any], path: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if path is not None:
        destination = path.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code, report = _run(args)
    _write_report(report, args.report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
