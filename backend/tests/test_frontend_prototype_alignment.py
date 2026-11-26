from pathlib import Path


def test_team_workbench_uses_the_prototype_visual_baseline_tokens_and_labels() -> None:
    root = Path(__file__).parents[2]
    prototype = (root / "docs/原型/agent团队群聊.html").read_text(encoding="utf-8")
    styles = (root / "frontend/src/styles.css").read_text(encoding="utf-8")
    component = (root / "frontend/src/features/platform/TeamWorkbench.tsx").read_text(encoding="utf-8")

    for token in ("--wb-ink", "--wb-muted", "--wb-line", "--wb-canvas", "--wb-pine", "--wb-pine-soft"):
        assert token in prototype
        assert token in styles
    for label in ("数字员工对话", "团队协作", "当前团队", "当前活动", "查看任务", "查看最近事件"):
        assert label in prototype
    assert "团队协作工作台" in component
    assert "当前团队" in component
    assert "查看任务" in component
    assert "查看最近事件" in component
