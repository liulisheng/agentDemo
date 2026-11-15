from pathlib import Path


def test_backend_declares_optional_harness_dependency_and_harness_has_no_backend_import() -> None:
    root = Path(__file__).parents[2]
    requirement = (root / "backend" / "requirements-harness.txt").read_text()
    assert "../harness-engine" in requirement
    harness_source = "\n".join(path.read_text(errors="ignore") for path in (root / "harness-engine" / "src").rglob("*.py"))
    assert "agent_platform" not in harness_source


def test_application_and_domain_do_not_import_harness_persistence_models() -> None:
    import ast

    root = Path(__file__).parents[2]
    source_root = root / "backend" / "src" / "agent_platform"
    forbidden = []
    for package in ("application", "domain", "ports"):
        for path in (source_root / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("harness_engine.persistence"):
                        forbidden.append((path, node.lineno, node.module))
                elif isinstance(node, ast.Import):
                    forbidden.extend(
                        (path, node.lineno, alias.name)
                        for alias in node.names
                        if alias.name.startswith("harness_engine.persistence")
                    )
    assert forbidden == []
