from dataclasses import dataclass

from agent_platform.adapters.runtime.harness import HarnessRuntimeAdapter
from agent_platform.ports.runtime import RuntimeRequest


@dataclass
class FakeHarness:
    started: bool = False

    def start(self) -> None:
        self.started = True

    def create_session(self):
        return type("Ref", (), {"session_id": "harness-session-1"})()

    def run(self, request, on_event=None):
        return type("Result", (), {"text": f"harness: {request.prompt}", "result_ref": "result-1"})()

    def close(self) -> None:
        self.started = False


def test_harness_adapter_only_translates_lifecycle_and_keeps_engine_loop_opaque() -> None:
    harness = FakeHarness()
    adapter = HarnessRuntimeAdapter(lambda: harness, engine_name="fake")
    session = adapter.create_session()
    result = adapter.run(RuntimeRequest("run-1", session, "hello", (), ()))
    assert session == "harness-session-1"
    assert result.text == "harness: hello"
