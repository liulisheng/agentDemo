"""Runtime 产出物收集、状态和幂等交付服务。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace

from agent_platform.domain.artifacts import (
    ArtifactDelivery,
    ArtifactStatus,
    ArtifactVisibility,
    ExecutionArtifact,
)
from agent_platform.domain.common import DomainError, generate_database_id
from agent_platform.domain.identity import IdentityContext
from agent_platform.ports.artifact_repository import ArtifactRepository
from agent_platform.ports.storage import ObjectStore, UploadRequest


class ArtifactService:
    """将 Runtime 文本结果登记为租户 Artifact；文件正文可由 ObjectStore 适配器接管。"""

    def __init__(self, repository: ArtifactRepository, object_store: ObjectStore | None = None) -> None:
        self.repository = repository
        self.object_store = object_store
        self._contents: dict[tuple[str, str, str], bytes] = {}

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """只允许文件名，不把 Runtime 提供的路径结构带入对象存储或 ZIP。"""

        normalized = filename.replace("\\", "/").split("/")[-1]
        normalized = "".join(character for character in normalized if ord(character) >= 32 and ord(character) != 127)
        if normalized in {"", ".", ".."}:
            raise DomainError("INVALID_ARGUMENT", "产出物文件名无效")
        return normalized[:255]

    def create_text(
        self,
        identity: IdentityContext,
        conversation_id: str,
        orchestration_id: str | None,
        task_id: str | None,
        run_id: str | None,
        filename: str,
        content: str,
        source_engine: str | None = None,
    ) -> ExecutionArtifact:
        payload = content.encode("utf-8")
        return self.create_bytes(
            identity,
            conversation_id,
            orchestration_id,
            task_id,
            run_id,
            filename,
            payload,
            "text/plain; charset=utf-8",
            source_engine,
        )

    def create_bytes(
        self,
        identity: IdentityContext,
        conversation_id: str,
        orchestration_id: str | None,
        task_id: str | None,
        run_id: str | None,
        filename: str,
        payload: bytes,
        mime_type: str,
        source_engine: str | None = None,
        *,
        visibility: ArtifactVisibility | str = ArtifactVisibility.TEAM,
    ) -> ExecutionArtifact:
        filename = self._safe_filename(filename)
        object_ref = f"artifact:{identity.platform_id}:{identity.tenant_id}:{generate_database_id()}"
        media_asset_id = None
        if self.object_store is not None:
            upload = self.object_store.create_upload_intent(
                UploadRequest(
                    platform_id=identity.platform_id,
                    tenant_id=identity.tenant_id,
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=len(payload),
                    retention_policy="conversation",
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
            asset = self.object_store.complete_upload(upload.upload_id, payload)
            object_ref = asset.object_ref
            media_asset_id = asset.asset_id
        artifact = ExecutionArtifact(
            artifact_id=generate_database_id(),
            platform_id=identity.platform_id,
            tenant_id=identity.tenant_id,
            conversation_id=conversation_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            object_ref=object_ref,
            task_id=task_id,
            run_id=run_id,
            orchestration_id=orchestration_id,
            source_engine=source_engine,
            visibility=visibility,
            status=ArtifactStatus.READY,
            media_asset_id=media_asset_id,
        )
        self.repository.save_artifact(identity, artifact)
        self._contents[(identity.platform_id, identity.tenant_id, artifact.artifact_id)] = payload
        return artifact

    def read_preview(self, identity: IdentityContext, artifact_id: str, *, max_bytes: int = 1024 * 1024) -> str:
        artifact = self.repository.get_artifact(identity, artifact_id)
        content = self.read_bytes(identity, artifact_id, max_bytes=max_bytes)
        if len(content) > max_bytes:
            raise DomainError("INVALID_ARGUMENT", "产出物预览超过大小限制")
        if artifact.mime_type.startswith("text/") or artifact.filename.endswith((".md", ".json", ".sql", ".csv", ".txt")):
            return content.decode("utf-8", errors="replace")
        return f"[{artifact.mime_type} · {artifact.size_bytes} bytes]"

    def read_bytes(self, identity: IdentityContext, artifact_id: str, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        """从租户 ObjectStore 恢复正文；内存只作为 Fake 模式的临时回退。"""

        artifact = self.repository.get_artifact(identity, artifact_id)
        if self.object_store is not None:
            try:
                content = self.object_store.read(
                    artifact.object_ref,
                    identity.platform_id,
                    identity.tenant_id,
                    max_bytes=max_bytes,
                )
            except PermissionError as error:
                raise DomainError("RESOURCE_NOT_FOUND", "产出物不存在", {"resource": "artifact"}) from error
        else:
            content = self._contents.get((identity.platform_id, identity.tenant_id, artifact_id))
            if content is None:
                raise DomainError("RESOURCE_NOT_FOUND", "产出物正文不存在", {"resource": "artifact_content"})
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.content_sha256:
            raise DomainError("ARTIFACT_INTEGRITY_ERROR", "产出物正文与登记摘要不一致")
        return content

    def bundle(self, identity: IdentityContext, artifact_ids: list[str], filename: str = "delivery.zip") -> ExecutionArtifact:
        if not artifact_ids:
            raise DomainError("INVALID_ARGUMENT", "至少选择一个产出物后才能打包")
        buffer = io.BytesIO()
        manifest_items: list[dict[str, object]] = []
        used_names: set[str] = {"delivery-manifest.json"}
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for artifact_id in artifact_ids:
                artifact = self.repository.get_artifact(identity, artifact_id)
                content = self.read_bytes(identity, artifact_id)
                safe_name = self._safe_filename(artifact.filename)
                unique_name = safe_name
                index = 2
                while unique_name in used_names:
                    stem, dot, suffix = safe_name.rpartition(".")
                    unique_name = f"{stem or safe_name}-{index}{dot}{suffix if dot else ''}"
                    index += 1
                used_names.add(unique_name)
                archive.writestr(unique_name, content)
                manifest_items.append({
                    "artifact_id": artifact.artifact_id,
                    "filename": unique_name,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                    "content_sha256": artifact.content_sha256,
                    "task_id": artifact.task_id,
                    "run_id": artifact.run_id,
                    "source_engine": artifact.source_engine,
                })
            archive.writestr(
                "delivery-manifest.json",
                json.dumps({"version": 1, "artifacts": manifest_items}, ensure_ascii=False, indent=2),
            )
        payload = buffer.getvalue()
        source = self.repository.get_artifact(identity, artifact_ids[0])
        return self.create_bytes(
            identity,
            source.conversation_id,
            source.orchestration_id,
            None,
            None,
            filename,
            payload,
            "application/zip",
            visibility=ArtifactVisibility.USER,
        )

    def deliver(
        self, identity: IdentityContext, artifact_id: str, channel_name: str, idempotency_key: str
    ) -> ArtifactDelivery:
        existing = self.repository.get_delivery_by_idempotency(
            identity, artifact_id, channel_name, idempotency_key
        )
        if existing is not None:
            return existing
        artifact = self.repository.get_artifact(identity, artifact_id)
        if artifact.status not in {ArtifactStatus.READY, ArtifactStatus.DELIVERED}:
            raise DomainError("INVALID_STATE", "产出物尚未就绪，不能发起交付")
        delivery = ArtifactDelivery.new(
            artifact.artifact_id,
            identity.platform_id,
            identity.tenant_id,
            artifact.conversation_id,
            channel_name,
            idempotency_key,
        )
        # Web 工作台本身就是受租户控制的交付渠道，记录写入后即可标记交付完成。
        if channel_name == "web":
            delivery = replace(delivery, status="DELIVERED")
        self.repository.save_delivery(identity, delivery)
        if channel_name == "web" and artifact.status is ArtifactStatus.READY:
            self.transition(identity, artifact.artifact_id, ArtifactStatus.DELIVERING)
            self.transition(identity, artifact.artifact_id, ArtifactStatus.DELIVERED)
        return delivery

    def transition(
        self, identity: IdentityContext, artifact_id: str, target: ArtifactStatus | str
    ) -> ExecutionArtifact:
        artifact = self.repository.get_artifact(identity, artifact_id)
        target_status = ArtifactStatus(target)
        allowed = {
            ArtifactStatus.CREATED: {ArtifactStatus.UPLOADING, ArtifactStatus.SCANNING, ArtifactStatus.READY, ArtifactStatus.FAILED},
            ArtifactStatus.UPLOADING: {ArtifactStatus.SCANNING, ArtifactStatus.FAILED},
            ArtifactStatus.SCANNING: {ArtifactStatus.READY, ArtifactStatus.FAILED},
            ArtifactStatus.READY: {ArtifactStatus.DELIVERING, ArtifactStatus.EXPIRED},
            ArtifactStatus.DELIVERING: {ArtifactStatus.DELIVERED, ArtifactStatus.FAILED},
        }
        if target_status not in allowed.get(artifact.status, set()):
            raise DomainError("INVALID_STATE", f"产出物不允许从 {artifact.status} 转为 {target_status}")
        updated = replace(artifact, status=target_status)
        self.repository.save_artifact(identity, updated)
        return updated
