# P3 Tools Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add allowlisted Python tool plugins with JSON input validation and an official-MCP-backed local service boundary.

**Architecture:** `tools.py` owns engine-neutral definitions, entry-point discovery, allowlist enforcement, schema validation, and safe invocation. `mcp_server.py` owns service lifecycle and optional official MCP SDK integration. The DeepSeek adapter emits a verified profile patch for the official MCP client. The CLI gains read-only tool listing while plugin execution remains behind the registry and service boundary.

**Tech Stack:** Python 3.12+, dataclasses, `importlib.metadata`, standard-library JSON/schema validation, optional official `mcp` package, pytest.

**Spec:** `docs/superpowers/specs/2026-11-10-tools-plugin-design.md`

## Global Constraints

- Core tool registration and validation must work without third-party packages.
- Entry point group is exactly `agent_harness.tools`.
- Discovery requires an explicit allowlist; no arbitrary directory scanning or hot reload.
- Invalid arguments never reach the handler.
- MCP stdout is reserved for the official protocol and logs go to stderr.
- No OS sandbox or arbitrary shell execution is added by this task.

## Review Focus

- An entry point outside the allowlist must not be imported; test in Task 1.
- A schema failure must happen before the handler increments its call counter; test in Task 2.
- Handler exception messages must not escape through the public error; test in Task 2.
- Duplicate names across manually registered and discovered tools must fail; test in Task 1.
- Missing MCP dependency must produce an explicit error and leave server state unchanged; test in Task 3.

### Task 1: Tool definitions and registry

**Files:**
- Create: `harness-engine/src/harness_engine/tools.py`
- Create: `harness-engine/tests/test_tools_registry.py`
- Modify: `harness-engine/src/harness_engine/__init__.py`

**Interfaces:**
- `ToolDefinition(name, description, input_schema, handler, plugin_name=None)`
- `ToolRegistry(allowlist: Iterable[str] = ())`
- `ToolRegistry.register(tool: ToolDefinition) -> None`
- `ToolRegistry.discover() -> tuple[str, ...]`
- `ToolRegistry.list_tools() -> tuple[ToolDefinition, ...]`
- `ToolRegistry.get(name: str) -> ToolDefinition`

- [x] Write failing tests for name/schema validation, explicit allowlist discovery, duplicate names, and missing tools.
- [x] Run `python -m pytest tests/test_tools_registry.py -q` and observe expected missing-module failures.
- [x] Implement definitions, entry-point loading, and safe registration.
- [x] Run the focused registry tests.

### Task 2: Schema validation and invocation

**Files:**
- Modify: `harness-engine/src/harness_engine/tools.py`
- Modify: `harness-engine/tests/test_tools_registry.py`
- Create: `harness-engine/tests/test_tools_invocation.py`

**Interfaces:**
- `ToolRegistry.invoke(name: str, arguments: Mapping[str, Any]) -> Any`
- `validate_tool_input(schema: Mapping[str, Any], arguments: Any) -> None`
- `ToolValidationError`, `ToolNotFoundError`, `ToolExecutionError`
- `echo_tool_definition() -> ToolDefinition`

- [x] Write failing tests for required/type/extra/enum validation, successful execution, and sanitized handler errors.
- [x] Run the focused invocation tests and observe failures caused by missing validation/invocation.
- [x] Implement the minimal supported JSON Schema validator and registry invocation path.
- [x] Run invocation tests and the full offline suite.

### Task 3: MCP service boundary

**Files:**
- Create: `harness-engine/src/harness_engine/mcp_server.py`
- Create: `harness-engine/tests/test_mcp_server.py`
- Modify: `harness-engine/src/harness_engine/__init__.py`
- Modify: `harness-engine/pyproject.toml`

**Interfaces:**
- `McpToolServer(registry: ToolRegistry, name: str = "harness-tools")`
- `McpToolServer.state -> EngineState`
- `McpToolServer.start() / close()`
- `McpToolServer.list_tools() -> tuple[dict[str, Any], ...]`
- `McpToolServer.call_tool(name, arguments) -> Any`
- `McpUnavailableError`

- [x] Write failing tests for list/call delegation, idempotent lifecycle, and missing optional dependency behavior.
- [x] Run the focused MCP tests and observe expected failures.
- [x] Implement the registry-backed service and optional official SDK adapter; do not hand-write MCP protocol framing.
- [x] Run focused tests and the full offline suite.

### Task 4: Example plugin, CLI surface, and documentation

**Files:**
- Modify: `harness-engine/src/harness_engine/cli.py`
- Modify: `harness-engine/tests/test_cli_deepseek.py`
- Modify: `harness-engine/pyproject.toml`
- Modify: `docs/design.md`
- Modify: `docs/implementation-plan.md`

**Interfaces:**
- Add `harness tools list` to show only explicitly enabled built-in tools.
- Register the no-side-effect example through `agent_harness.tools` metadata.

- [x] Write failing CLI tests for the tools list output and empty allowlist behavior.
- [x] Run focused CLI tests and observe missing subcommand behavior.
- [x] Implement the example entry point and read-only listing.
- [x] Run the full suite, package import check, compile check, and CLI smoke test.
- [x] Generate a DeepSeek stdio profile patch with the current Python executable and verify one local Harness MCP call.

## Completion Evidence

Complete when all task test cycles pass, the full offline suite passes, package import and CLI checks pass, invalid input never invokes a handler, missing MCP dependency is explicit rather than silently falling back to a custom protocol, and one local DeepSeek Harness run discovers and calls the Python MCP example tool.
