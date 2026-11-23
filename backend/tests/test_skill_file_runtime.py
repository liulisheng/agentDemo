from __future__ import annotations

import io
import zipfile

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.control_plane import ControlPlaneService
from agent_platform.application.skill_files import SkillFilePackageService
from agent_platform.domain.catalog import SkillType
from agent_platform.ports.storage import LocalObjectStore
from tests._runtime_config_helpers import configure_in_memory_deployment


class CapturingRuntime(FakeRuntimeAdapter):
    last_request = None

    def run(self, request):
        self.last_request = request
        return super().run(request)


def test_fake_runtime_receives_fixed_entry_and_read_only_file_refs_without_executing_them(
    tmp_path,
) -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    skill = catalog.create_skill(
        identity, "文件执行技能", SkillType.INSTRUCTION, "旧 INLINE 指令不参与此运行", ()
    )
    storage = LocalObjectStore(tmp_path / "objects")
    file_service = SkillFilePackageService(catalog, storage)
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "只按此固定入口处理请求。")
        archive.writestr("helpers/do_not_execute.py", 'print("此内容不应执行")')
    archive = archive_data.getvalue()
    preview = file_service.preview_zip(identity, skill.skill_id, skill.version, archive)
    package = file_service.commit_zip(
        identity, skill.skill_id, skill.version, preview.staging_id, archive
    )
    catalog.publish_skill(identity, package.skill_id)
    employee = catalog.create_employee(identity, "文件员工", "助手", "处理文件包 Skill")
    employee_version = catalog.create_employee_version(
        identity, employee.employee_id, "员工系统指令", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    deployment = catalog.deploy(identity, employee.employee_id, employee_version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    runtime = CapturingRuntime()
    control_plane = ControlPlaneService(catalog, runtime, object_store=storage)
    session = control_plane.create_session(identity, deployment.deployment_id)

    run = control_plane.create_run(identity, session.session_id, "请执行 Skill")

    assert run.snapshot.skill_file_packages[0].manifest_sha256 == package.file_manifest_sha256
    assert {item.file_path for item in run.snapshot.skill_file_packages[0].files} == {
        "SKILL.md",
        "helpers/do_not_execute.py",
    }
    assert runtime.last_request.system_prompt == "员工系统指令"
    assert runtime.last_request.skill_instructions == ("只按此固定入口处理请求。",)
    assert runtime.last_request.skill_file_packages == run.snapshot.skill_file_packages
    assert "此内容不应执行" not in run.result_text

    trace = control_plane.get_trace(identity, run.run_id)
    skill_span = next(span for span in trace.spans if span.span_type.value == "skill")
    assert skill_span.attributes["file_manifest_sha256"] == package.file_manifest_sha256
    assert skill_span.attributes["file_count"] == 2
