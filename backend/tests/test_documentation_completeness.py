from pathlib import Path


def test_feature_docs_have_status_diagram_and_validation_command() -> None:
    root = Path(__file__).parents[2]
    docs = [
        root / "docs/catalog-and-versioning.md",
        root / "docs/model-routes.md",
        root / "docs/conversations-and-channels.md",
        root / "docs/context-and-memory.md",
        root / "docs/workspaces-and-sandboxes.md",
        root / "docs/execution-observability.md",
        root / "docs/usage-accounting.md",
        root / "docs/执行引擎适配设计.md",
        root / "docs/adapter-integration-checklist.md",
    ]
    for path in docs:
        text = path.read_text()
        assert "状态" in text or "Status" in text
        assert "验证" in text or "验证命令" in text
        assert "mermaid" in text or "|" in text
