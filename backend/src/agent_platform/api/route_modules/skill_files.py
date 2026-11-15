"""Skill 文件树、在线编辑、ZIP Preview/Commit API。"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, Query, Request

from agent_platform.api.schemas import (
    SkillFileCreateRequest,
    SkillFileRenameRequest,
    SkillFileUpdateRequest,
)
from agent_platform.application.skill_files import SkillZipPreview
from agent_platform.domain.catalog import SkillVersion
from agent_platform.domain.common import DomainError

from .common import Identity, SkillFiles, _check_tenant

router = APIRouter()

def _skill_zip_preview_response(value: SkillZipPreview) -> dict[str, object]:
    return {
        "staging_id": value.staging_id,
        "skill_id": value.skill_id,
        "version": value.version,
        "strategy": value.strategy,
        "archive_sha256": value.archive_sha256,
        "base_manifest_sha256": value.base_manifest_sha256,
        "manifest_sha256": value.manifest_sha256,
        "entry_path": value.entry_path,
        "file_count": value.file_count,
        "expires_in_seconds": value.expires_in_seconds,
        "files": [
            {
                "path": item.file_path,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256": item.content_sha256,
            }
            for item in value.files
        ],
        "diff": {
            "added": list(value.added_paths),
            "updated": list(value.updated_paths),
            "removed": list(value.removed_paths),
        },
    }


def _skill_file_version_response(skill: SkillVersion) -> dict[str, object]:
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "status": skill.status.value,
        "read_only": skill.status.value != "DRAFT",
        "source_mode": (
            "REFERENCE" if skill.instruction.startswith("hub-reference://") else "LOCAL"
        ),
        "fork_allowed": (
            skill.status.value == "PUBLISHED"
            and not skill.instruction.startswith("hub-reference://")
        ),
        "files": [],
    }


def _file_bytes_from_request(request: SkillFileCreateRequest | SkillFileUpdateRequest) -> bytes:
    if (request.content_text is None) == (request.content_base64 is None):
        raise DomainError("INVALID_ARGUMENT", "content_text 与 content_base64 必须且只能提供一个")
    if request.content_text is not None:
        return request.content_text.encode("utf-8")
    try:
        return base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise DomainError("INVALID_ARGUMENT", "content_base64 格式非法") from error


def _skill_file_change_response(skill: SkillVersion) -> dict[str, object]:
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "file_count": len(skill.file_entries),
        "status": skill.status.value,
    }


async def _read_limited_skill_zip(request: Request, max_bytes: int) -> bytes:
    """逐块接收 ZIP，避免在校验压缩包上限前一次性读取任意大请求体。"""

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise DomainError("INVALID_ARGUMENT", "ZIP 压缩包超过大小限制", {"max_bytes": max_bytes})
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/skills/{skill_id}/versions/{version}/files/zip/preview")
async def preview_skill_zip(
    skill_id: str,
    version: int,
    request: Request,
    identity: Identity,
    service: SkillFiles,
    strategy: Annotated[str, Query(pattern="^(replace|merge)$")] = "replace",
) -> dict[str, object]:
    """预览 ZIP 文件树与 replace/merge 差异；请求体为 application/zip 原始字节。"""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
        raise DomainError("INVALID_ARGUMENT", "ZIP 请求 Content-Type 必须是 application/zip")
    archive = await _read_limited_skill_zip(request, service.max_archive_bytes)
    return _skill_zip_preview_response(
        service.preview_zip(identity, skill_id, version, archive, strategy=strategy)
    )


@router.post("/skills/{skill_id}/versions/{version}/files/zip/commit/{staging_id}")
async def commit_skill_zip(
    skill_id: str,
    version: int,
    staging_id: str,
    request: Request,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """重新上传预览过的同一 ZIP，再以完整清单事务写入 Skill DRAFT。"""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"application/zip", "application/x-zip-compressed", "application/octet-stream"}:
        raise DomainError("INVALID_ARGUMENT", "ZIP 请求 Content-Type 必须是 application/zip")
    archive = await _read_limited_skill_zip(request, service.max_archive_bytes)
    skill = service.commit_zip(identity, skill_id, version, staging_id, archive)
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_format": skill.content_format.value,
        "entry_path": skill.entry_path,
        "file_manifest_sha256": skill.file_manifest_sha256,
        "file_count": len(skill.file_entries),
        "status": skill.status.value,
    }


@router.get("/skills/{skill_id}/versions/{version}/files")
def list_skill_files(
    skill_id: str, version: int, identity: Identity, service: SkillFiles
) -> dict[str, object]:
    """返回精确 Skill 版本的文件树和摘要；PUBLISHED/REFERENCE 只读。"""

    skill = service.get_skill_version(identity, skill_id, version)
    response = _skill_file_version_response(skill)
    response["files"] = [
        {
            "path": item.file_path,
            "mime_type": item.mime_type,
            "size_bytes": item.size_bytes,
            "sha256": item.content_sha256,
            "virtual": skill.content_format.value == "INLINE",
        }
        for item in service.list_files(identity, skill_id, version)
    ]
    return response


@router.get("/skills/{skill_id}/versions/{version}/files/content")
def read_skill_file(
    skill_id: str,
    version: int,
    identity: Identity,
    service: SkillFiles,
    file_path: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, object]:
    """返回 UTF-8 文件正文或 Base64 二进制正文，不暴露对象引用。"""

    content = service.read_file(identity, skill_id, version, file_path)
    item = next(
        (item for item in service.list_files(identity, skill_id, version) if item.file_path == file_path),
        None,
    )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return {
        "path": file_path,
        "mime_type": item.mime_type if item is not None else "text/markdown; charset=utf-8",
        "size_bytes": len(content),
        "sha256": item.content_sha256 if item is not None else None,
        "content_text": text,
        "content_base64": None if text is not None else base64.b64encode(content).decode("ascii"),
    }


@router.post("/skills/{skill_id}/versions/{version}/files", status_code=201)
def create_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileCreateRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """在 DRAFT Skill 创建文件；INLINE 转换必须先创建根 SKILL.md。"""

    _check_tenant(identity, request)
    skill = service.create_file(
        identity,
        skill_id,
        version,
        request.file_path,
        _file_bytes_from_request(request),
        mime_type=request.mime_type,
    )
    return _skill_file_change_response(skill)


@router.put("/skills/{skill_id}/versions/{version}/files")
def update_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileUpdateRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """按 expected_sha256 更新一个 DRAFT 文件或兼容 INLINE 根指令。"""

    _check_tenant(identity, request)
    skill = service.update_file(
        identity,
        skill_id,
        version,
        request.file_path,
        _file_bytes_from_request(request),
        expected_sha256=request.expected_sha256,
        mime_type=request.mime_type,
    )
    return _skill_file_change_response(skill)


@router.post("/skills/{skill_id}/versions/{version}/files/rename")
def rename_skill_file(
    skill_id: str,
    version: int,
    request: SkillFileRenameRequest,
    identity: Identity,
    service: SkillFiles,
) -> dict[str, object]:
    """按文件摘要乐观并发重命名 DRAFT 辅助文件。"""

    _check_tenant(identity, request)
    skill = service.rename_file(
        identity,
        skill_id,
        version,
        request.file_path,
        request.new_file_path,
        expected_sha256=request.expected_sha256,
    )
    return _skill_file_change_response(skill)


@router.delete("/skills/{skill_id}/versions/{version}/files")
def delete_skill_file(
    skill_id: str,
    version: int,
    identity: Identity,
    service: SkillFiles,
    file_path: Annotated[str, Query(min_length=1, max_length=512)],
    expected_sha256: Annotated[str, Query(min_length=64, max_length=64)],
) -> dict[str, object]:
    """按 expected_sha256 删除 DRAFT 辅助文件；根入口不可删除。"""

    skill = service.delete_file(
        identity,
        skill_id,
        version,
        file_path,
        expected_sha256=expected_sha256,
    )
    return _skill_file_change_response(skill)
