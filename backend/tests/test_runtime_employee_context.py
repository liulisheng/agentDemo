"""验证 Runtime 输入只包含固定员工上下文和可暴露的 READ/PREVIEW 工具定义。"""

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
from agent_platform.application.resolvers import AgentResolver
from agent_platform.domain.catalog import (
    LifecycleStatus,
    SkillContentFormat,
    SkillFileEntry,
    SkillType,
    SkillVersion,
    ToolVersion,
    compute_skill_file_manifest_sha256,
)


def test_resolved_employee_context_exposes_schema_only_for_read_and_preview_tools() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    definitions = (
        ("projects_list", "列出项目", "READ"),
        ("workflow_preview", "预览工作流", "PREVIEW"),
        ("workflow_create", "创建工作流", "WRITE"),
    )
    tools = []
    for name, description, risk_level in definitions:
        tool = ToolVersion(
            tool_id=f"tool-{name}",
            tenant_id=identity.tenant_id,
            name=name,
            description=description,
            input_schema={"type": "object", "properties": {"project": {"type": "string"}}},
            mcp_risk_level=risk_level,
            preview_tool_name="workflow_preview" if risk_level == "WRITE" else None,
        ).publish()
        catalog.repository.save_tool(identity, tool)
        tools.append(tool)

    skill = catalog.create_skill(
        identity,
        "工作流 Skill",
        SkillType.INSTRUCTION,
        "先只读查询，再生成预览",
        [tool.tool_id for tool in tools],
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Dolphin 员工", "数仓开发", "安全工具解析")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "固定员工指令",
        [skill.skill_id],
        [tool.tool_id for tool in tools],
        execution_profile_id=profile.profile_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)

    resolved = AgentResolver(catalog).resolve(identity, deployment)

    assert set(resolved.allowed_tools) == {"projects_list", "workflow_preview"}
    definitions_by_name = {item.name: item for item in resolved.tool_definitions}
    assert set(definitions_by_name) == {"projects_list", "workflow_preview"}
    assert definitions_by_name["workflow_preview"].risk_level == "PREVIEW"
    assert all(item.risk_level in {"READ", "PREVIEW"} for item in resolved.tool_definitions)
    assert resolved.tool_definitions[0].input_schema["properties"]["project"]["type"] == "string"


def test_resolver_binds_only_selected_read_preview_tools_to_exact_mcp_profile() -> None:
    from agent_platform.adapters.mcp.stdio import McpToolDefinition, compute_tool_catalog_sha256
    from agent_platform.domain.catalog import compute_tool_input_schema_sha256
    from agent_platform.domain.mcp import McpProfileStatus, McpServerProfile

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    catalog_profile = catalog.create_profile(identity, "deepseek", "model-route")
    schemas = {
        "projects_list": {"type": "object", "properties": {"project": {"type": "string"}}},
        "workflow_preview": {"type": "object", "properties": {"project": {"type": "string"}}},
        "workflow_create": {"type": "object", "properties": {"project": {"type": "string"}}},
    }
    remote_catalog = tuple(
        McpToolDefinition(name=name, description=f"远端工具 {name}", input_schema=schema)
        for name, schema in schemas.items()
    )
    mcp_profile = McpServerProfile(
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        profile_id="mcp-dolphin",
        version=3,
        name="Dolphin Stdio",
        transport="stdio",
        command="/opt/dolphin/python",
        args=("-m", "dolphin_mcp"),
        working_directory_ref="env-path://DOLPHIN_MCP_CWD",
        enabled_tool_names=tuple(schemas),
        tool_catalog_sha256=compute_tool_catalog_sha256(remote_catalog),
        environment_secret_refs={"DOLPHIN_TOKEN": "env://DOLPHIN_TOKEN"},
        status=McpProfileStatus.ACTIVE,
    )

    class McpRepository:
        def get_profile(self, request_identity, profile_id, version):
            assert request_identity.tenant_id == identity.tenant_id
            assert (profile_id, version) == (mcp_profile.profile_id, mcp_profile.version)
            return mcp_profile

    tools = []
    for name, risk in (
        ("projects_list", "READ"),
        ("workflow_preview", "PREVIEW"),
        ("workflow_create", "WRITE"),
    ):
        tools.append(
            ToolVersion(
                tool_id=f"tool-{name}",
                tenant_id=identity.tenant_id,
                name=name,
                description=f"工具 {name}",
                input_schema=schemas[name],
                protocol="mcp",
                mcp_server_profile_id=mcp_profile.profile_id,
                mcp_server_profile_version=mcp_profile.version,
                remote_tool_name=name,
                input_schema_sha256=compute_tool_input_schema_sha256(schemas[name]),
                mcp_risk_level=risk,
                preview_tool_name="workflow_preview" if risk == "WRITE" else None,
            ).publish()
        )
    for tool in tools:
        catalog.repository.save_tool(identity, tool)
    skill = catalog.create_skill(
        identity,
        "Dolphin Skill",
        SkillType.INSTRUCTION,
        "先查询，再预览。",
        [tool.tool_id for tool in tools],
    )
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Dolphin 员工", "数仓开发", "Profile 绑定")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "固定数仓指令",
        [skill.skill_id],
        [tool.tool_id for tool in tools],
        execution_profile_id=catalog_profile.profile_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)

    resolved = AgentResolver(catalog, mcp_repository=McpRepository()).resolve(identity, deployment)

    assert len(resolved.mcp_servers) == 1
    run_profile = resolved.mcp_servers[0]
    assert run_profile.profile == mcp_profile
    assert run_profile.run_allowed_tool_names == ("projects_list", "workflow_preview")
    assert set(run_profile.expected_tool_schema_sha256) == {
        "projects_list", "workflow_preview"
    }


def test_resolver_loads_verified_text_skill_support_files_into_runtime_context() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    profile = catalog.create_profile(identity, "fake", "fake-model")
    contents = {
        "SKILL.md": "先检查项目，再生成预览。".encode(),
        "references.md": "OFFLINE 工作流参考字段。".encode(),
    }
    entries = tuple(
        SkillFileEntry(
            file_path=path,
            media_asset_id=f"asset-{path}",
            mime_type="text/markdown; charset=utf-8",
            size_bytes=len(payload),
            content_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        )
        for path, payload in contents.items()
    )
    skill = SkillVersion(
        skill_id="skill-file-package",
        tenant_id=identity.tenant_id,
        name="文件包 Skill",
        skill_type=SkillType.INSTRUCTION,
        instruction="",
        content_format=SkillContentFormat.FILE_PACKAGE,
        entry_path="SKILL.md",
        file_manifest_sha256=compute_skill_file_manifest_sha256(entries),
        file_entries=entries,
        status=LifecycleStatus.PUBLISHED,
    )
    catalog.repository.save_skill(identity, skill)
    employee = catalog.create_employee(identity, "文件包员工", "数仓开发", "验证 Skill 文件")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "固定系统指令",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)

    class _Store:
        def read_asset(self, asset_id, platform_id, tenant_id, *, max_bytes):
            return contents[asset_id.removeprefix("asset-")]

    resolved = AgentResolver(catalog, object_store=_Store()).resolve(identity, deployment)

    assert resolved.skill_instructions == ("先检查项目，再生成预览。",)
    assert len(resolved.skill_file_contents) == 1
    assert resolved.skill_file_contents[0].skill_id == skill.skill_id
    assert resolved.skill_file_contents[0].path == "references.md"
    assert resolved.skill_file_contents[0].content == "OFFLINE 工作流参考字段。"
