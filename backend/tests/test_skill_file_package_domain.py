from __future__ import annotations

import hashlib

import pytest

from agent_platform.domain.catalog import (
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillType,
    SkillVersion,
    compute_skill_file_manifest_sha256,
)
from agent_platform.domain.common import DomainError


def _file(path: str, content: bytes) -> SkillFileEntry:
    return SkillFileEntry(
        file_path=path,
        media_asset_id=f"asset-{path}",
        mime_type="text/markdown",
        size_bytes=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


def test_inline_skill_remains_backward_compatible() -> None:
    skill = SkillVersion(
        "skill-1", "tenant-1", "旧 Skill", SkillType.INSTRUCTION, "原有指令"
    )
    assert skill.content_format is SkillContentFormat.INLINE
    assert skill.entry_path is None
    assert skill.file_manifest_sha256 is None
    assert skill.instruction == "原有指令"


def test_file_package_requires_exact_root_skill_markdown_entry_and_stable_manifest() -> None:
    files = (_file("assets/example.txt", b"sample"), _file("SKILL.md", "入口指令".encode()))
    digest = compute_skill_file_manifest_sha256(files)
    skill = SkillVersion(
        "skill-2",
        "tenant-1",
        "文件 Skill",
        SkillType.INSTRUCTION,
        "",
        version=2,
        status=LifecycleStatus.DRAFT,
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256=digest,
        file_entries=files,
    )

    assert skill.entry_path == "SKILL.md"
    assert skill.file_manifest_sha256 == digest
    assert compute_skill_file_manifest_sha256(tuple(reversed(files))) == digest
    assert len(skill.file_entries) == 2


@pytest.mark.parametrize(
    "files,entry_path",
    [
        ((_file("docs/SKILL.md", b"nested"),), "docs/SKILL.md"),
        ((_file("guide.md", b"no root"),), "SKILL.md"),
        ((_file("SKILL.md", b"one"), _file("SKILL.md", b"two")), "SKILL.md"),
        ((_file("folder/SKILL.md", b"nested"), _file("SKILL.md", b"root")), "SKILL.md"),
    ],
)
def test_file_package_rejects_missing_or_ambiguous_entry(files, entry_path) -> None:
    digest = compute_skill_file_manifest_sha256(files)
    with pytest.raises(ValueError):
        SkillVersion(
            "skill-3",
            "tenant-1",
            "非法文件 Skill",
            SkillType.INSTRUCTION,
            "",
            content_format=SkillContentFormat.FILE_PACKAGE,
            entry_path=entry_path,
            file_manifest_sha256=digest,
            file_entries=files,
        )


@pytest.mark.parametrize("path", ["/absolute.md", "../escape.md", "a/../../escape.md", "a\\b.md", "a//b.md"])
def test_skill_file_entry_rejects_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValueError):
        _file(path, b"data")


def test_file_manifest_digest_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="manifest digest"):
        SkillVersion(
            "skill-4",
            "tenant-1",
            "摘要错误",
            SkillType.INSTRUCTION,
            "",
            content_format=SkillContentFormat.FILE_PACKAGE,
            entry_path="SKILL.md",
            file_manifest_sha256="0" * 64,
            file_entries=(_file("SKILL.md", b"content"),),
        )


def test_published_file_package_is_immutable_like_inline_skill() -> None:
    files = (_file("SKILL.md", b"content"),)
    skill = SkillVersion(
        "skill-5",
        "tenant-1",
        "已发布文件 Skill",
        SkillType.INSTRUCTION,
        "",
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256=compute_skill_file_manifest_sha256(files),
        file_entries=files,
    ).publish()

    with pytest.raises(DomainError) as error:
        skill.assert_mutable()
    assert getattr(error.value, "code", None) == "IMMUTABLE_VERSION"
