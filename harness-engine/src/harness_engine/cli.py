"""Small offline CLI for proving the engine contract."""

import argparse
import os
import sys
from pathlib import Path

from .core import EngineRequest
from .doctor import main as doctor_main
from .engines.deepseek import DeepSeekHarnessEngine
from .engines.fake import FakeEngine
from .tools import build_tool_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness")
    parser.add_argument("prompt", nargs="?", help="text sent to the selected engine")
    parser.add_argument("--engine", default="fake", choices=("fake", "deepseek"))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="workspace passed to DeepSeek Harness",
    )
    parser.add_argument(
        "--dsh-home",
        type=Path,
        help="explicit Harness home; defaults to DSH_HOME or .artifacts/dsh-home",
    )
    parser.add_argument("--profile")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--session-id")
    parser.add_argument(
        "--startup-only",
        action="store_true",
        help="start and close the selected engine without sending a prompt",
    )
    parser.add_argument(
        "--mcp-tool",
        action="append",
        default=[],
        help="enable a Python MCP tool for DeepSeek; may be repeated",
    )
    parser.add_argument(
        "--local-config",
        type=Path,
        help="local provider JSON, defaulting to .artifacts/local-openai.json when present",
    )
    return parser


def _tools_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="harness tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="list enabled tools")
    list_parser.add_argument(
        "--enable",
        action="append",
        default=[],
        help="entry-point name to enable; may be repeated",
    )
    args = parser.parse_args(argv)
    if args.command == "list":
        registry = build_tool_registry(args.enable)
        for tool in registry.list_tools():
            print(f"{tool.name}\t{tool.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "doctor":
        return doctor_main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "persistence-demo":
        from .persistence_demo import main as persistence_demo_main

        return persistence_demo_main(raw_argv[1:])
    if len(raw_argv) >= 2 and raw_argv[:2] == ["tools", "list"]:
        return _tools_main(raw_argv[1:])
    args = build_parser().parse_args(raw_argv)
    if not args.prompt:
        build_parser().error("prompt is required")
    if args.engine == "fake":
        engine = FakeEngine()
    else:
        local_config = args.local_config
        if local_config is None:
            candidate = Path(".artifacts/local-openai.json")
            local_config = candidate if candidate.exists() else None
        dsh_home = args.dsh_home or Path(
            os.environ.get("DSH_HOME") or ".artifacts/dsh-home"
        )
        engine = DeepSeekHarnessEngine(
            dsh_home=dsh_home,
            cwd=args.workspace,
            profile=args.profile,
            provider=args.provider,
            model=args.model,
            max_tokens=args.max_tokens,
            mcp_tools=tuple(args.mcp_tool),
            local_config=local_config,
        )
    try:
        engine.start()
        if args.startup_only:
            return 0
        result = engine.run(
            EngineRequest(prompt=args.prompt, session_id=args.session_id)
        )
        print(result.text)
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
