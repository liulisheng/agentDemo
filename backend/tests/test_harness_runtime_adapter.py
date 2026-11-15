from dataclasses import dataclass

from agent_platform.adapters.runtime.harness import HarnessRuntimeAdapter
from agent_platform.domain.observability import SkillFilePackageSnapshot, SkillFileSnapshotEntry
from agent_platform.ports.runtime import RuntimeRequest


@dataclass
class FakeHarness:
    started: bool = False
    last_request: object | None = None

    def start(self) -> None:
        self.started = True

    def create_session(self):
        return type("Ref", (), {"session_id": "harness-session-1"})()

    def run(self, request, on_event=None):
        self.last_request = request
        return type("Result", (), {"text": f"harness: {request.prompt}", "result_ref": "result-1"})()

    def close(self) -> None:
        self.started = False


def test_harness_adapter_only_translates_lifecycle_and_keeps_engine_loop_opaque() -> None:
    harness = FakeHarness()
    adapter = HarnessRuntimeAdapter(lambda: harness, engine_name="fake")
    session = adapter.create_session()
    file_package = SkillFilePackageSnapshot(
        "skill-1",
        1,
        "SKILL.md",
        "a" * 64,
        (SkillFileSnapshotEntry("SKILL.md", "asset-1", "text/markdown", 5, "b" * 64),),
    )
    result = adapter.run(
        RuntimeRequest(
            "run-1",
            session,
            "hello",
            ("处理步骤",),
            ("echo",),
            system_prompt="员工系统指令",
            skill_file_packages=(file_package,),
        )
    )
    assert session == "harness-session-1"
    assert result.text == "harness: hello"
    assert harness.last_request.system_prompt == "员工系统指令"
    assert harness.last_request.skill_instructions == ("处理步骤",)
    assert harness.last_request.allowed_tools == ("echo",)
    assert harness.last_request.skill_file_packages == (file_package,)
