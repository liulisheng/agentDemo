# P2 Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the engine lifecycle contract required by P2 while preserving the existing offline CLI surface and leaving extension points for future skills and subagents.

**Architecture:** Add engine-neutral lifecycle types and a small lifecycle base class in `core.py`. FakeEngine owns an in-memory session/run state machine for deterministic contract tests. DeepSeekHarnessEngine adapts the same protocol, maps the currently supported `session_id` path, and rejects unverified cancellation explicitly. Skill and subagent support is represented only by metadata, parent run IDs, and typed events.

**Tech Stack:** Python 3.12+, dataclasses, enums, typing protocols, pytest; no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-11-10-lifecycle-design.md`

## Global Constraints

- Core modules must not import vendor SDK types.
- `start()` and `close()` are idempotent; operations after close raise a lifecycle error.
- Unsupported capabilities raise `UnsupportedCapabilityError`.
- Existing `EngineRequest`, `EngineResult`, and CLI invocation remain usable.
- No MySQL, lease, sandbox, plugin executor, or persistent session work is included.

## Review Focus

- Run before start and after close must produce lifecycle errors; tests belong to Tasks 1 and 2.
- Resume of an unknown Fake session must fail without creating a session; test belongs to Task 2.
- A DeepSeek adapter without a verified SDK cancel method must reject cancellation; test belongs to Task 3.
- Event callbacks must carry run/session IDs and parent run metadata; test belongs to Task 1.
- CLI must start and close the selected engine even when using the new protocol; test belongs to Task 4.

### Task 1: Core lifecycle contracts

**Files:**
- Modify: `harness-engine/src/harness_engine/core.py`
- Test: `harness-engine/tests/test_core_contracts.py`

**Interfaces:**
- Produces `EngineState`, `RunStatus`, `CancelStatus`, lifecycle exceptions, `SessionRef`, `CancelResult`, `HarnessEvent`, `LifecycleEngine`, and an expanded `HarnessEngine` protocol.
- `EngineRequest` keeps `session_id` and gains optional `session`, `run_id`, and `parent_run_id`.
- `EngineResult` keeps existing fields and gains optional `run_id`, `session`, and `status`.

- [x] Write failing tests for value validation, lifecycle state guards, event payloads, and request/session consistency.
- [x] Run `python -m pytest tests/test_core_contracts.py -q` and observe the expected missing-type or behavior failures.
- [x] Implement the core types and `LifecycleEngine` state helpers without importing engine implementations.
- [x] Re-run the focused tests and then `python -m pytest tests/test_core_contracts.py -q`.

### Task 2: Fake engine lifecycle

**Files:**
- Modify: `harness-engine/src/harness_engine/engines/fake.py`
- Modify: `harness-engine/tests/test_core_contracts.py`
- Create: `harness-engine/tests/test_fake_lifecycle.py`

**Interfaces:**
- `FakeEngine.create_session() -> SessionRef`
- `FakeEngine.resume_session(ref: SessionRef) -> SessionRef`
- `FakeEngine.cancel(session_id: str) -> CancelResult`
- `FakeEngine.run(request: EngineRequest, on_event: EventHandler | None = None) -> EngineResult`

- [x] Write failing tests for explicit start, idempotent start/close, session creation/resume, unknown-session rejection, cancellation, and event ordering.
- [x] Run `python -m pytest tests/test_fake_lifecycle.py -q` and verify failures are caused by missing lifecycle behavior.
- [x] Implement the minimal in-memory session and active-run state machine.
- [x] Run the focused Fake tests and the full offline suite `python -m pytest -q`.

### Task 3: DeepSeek adapter lifecycle boundary

**Files:**
- Modify: `harness-engine/src/harness_engine/engines/deepseek.py`
- Modify: `harness-engine/tests/test_deepseek_engine.py`

**Interfaces:**
- `DeepSeekHarnessEngine.start()` and `close()` use the shared lifecycle state.
- `create_session()` creates an engine-neutral reference for the SDK's existing `session_id` entry point.
- `resume_session()` calls a verified SDK resume method; when the SDK does not expose one, it raises `UnsupportedCapabilityError`.
- `cancel()` calls a verified SDK cancel method when present; otherwise raises `UnsupportedCapabilityError`.

- [x] Write failing adapter tests for start/close state, session reference mapping, and cancellation rejection when the fake SDK lacks cancel.
- [x] Run `python -m pytest tests/test_deepseek_engine.py -q` and observe the expected failures.
- [x] Implement the adapter mapping and preserve provider/profile configuration behavior.
- [x] Run the focused adapter tests and the full offline suite.

### Task 4: CLI and compatibility surface

**Files:**
- Modify: `harness-engine/src/harness_engine/cli.py`
- Modify: `harness-engine/src/harness_engine/__init__.py`
- Modify: `harness-engine/tests/test_cli_deepseek.py`
- Modify: `harness-engine/tests/test_core_contracts.py`

**Interfaces:**
- CLI starts the selected engine before running and closes it in `finally`.
- Existing `--session-id` remains accepted; `EngineRequest` carries it unchanged.

- [x] Write failing test proving CLI starts an injected engine and closes it on success and failure.
- [x] Run the focused CLI test and observe the missing start call.
- [x] Implement the smallest CLI lifecycle wrapper.
- [x] Run `python -m pytest -q` and perform an import check with `python -c "import harness_engine"`.

## Completion Evidence

The plan is complete when all four task test cycles pass, the full offline pytest suite passes, the package import succeeds, and the implementation contains no vendor SDK imports in `core.py`.
