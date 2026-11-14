from pathlib import Path


def test_backend_declares_optional_harness_dependency_and_harness_has_no_backend_import() -> None:
    root = Path(__file__).parents[2]
    requirement = (root / "backend" / "requirements-harness.txt").read_text()
    assert "../harness-engine" in requirement
    harness_source = "\n".join(path.read_text(errors="ignore") for path in (root / "harness-engine" / "src").rglob("*.py"))
    assert "agent_platform" not in harness_source
