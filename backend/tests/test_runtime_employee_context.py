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


def test_run_snapshot_pins_model_connection_and_route_before_later_edits() -> None:
    from dataclasses import replace

    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.model_routes import ModelRouteService
    from agent_platform.domain.model_routes import ModelCatalogItem, ModelConnection, SecretRef
    from agent_platform.domain.observability import SpanType

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    connection = ModelConnection(
        connection_id="connection-snapshot",
        tenant_id=identity.tenant_id,
        name="本地模型",
        provider_kind="openai-compatible",
        protocol="openai-chat-completions",
        endpoint="http://host.docker.internal:63072/v1",
        secret_ref=SecretRef("model-key-ref", scope=identity.tenant_id, provider="environment"),
        version=4,
    )
    repository.save_model_connection(identity, connection)
    repository.save_model_catalog_item(
        identity,
        ModelCatalogItem(
            connection_id=connection.connection_id,
            tenant_id=identity.tenant_id,
            model_id="local-model-a",
            display_name="Local Model A",
        ),
    )
    routes = ModelRouteService(repository)
    route = routes.create_from_catalog(
        identity,
        alias="assistant-model",
        connection_id=connection.connection_id,
        model_id="local-model-a",
        parameters={"temperature": 0.2},
    )
    profile = catalog.create_profile(
        identity, "deepseek", "assistant-model", model_route_id=route.route_id
    )
    skill = catalog.create_skill(identity, "空 Skill", SkillType.INSTRUCTION, "回答问题", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "快照助手", "助手", "模型快照")
    version = catalog.create_employee_version(
        identity,
        employee.employee_id,
        "根据用户问题回答。",
        [skill.skill_id],
        [],
        execution_profile_id=profile.profile_id,
        model_route_id=route.route_id,
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    resolver = AgentResolver(catalog)

    submitted = resolver.resolve(identity, deployment)
    snapshot = submitted.snapshot
    model_snapshot = getattr(snapshot, "model_route_snapshot", None)
    assert model_snapshot is not None, "RunSnapshot must pin the resolved model route"
    assert model_snapshot.endpoint == "http://host.docker.internal:63072/v1"
    assert model_snapshot.connection_version == 4
    assert model_snapshot.route_version == 1
    assert model_snapshot.model_id == "local-model-a"
    assert model_snapshot.provider_kind == "openai-compatible"
    assert model_snapshot.protocol == "openai-chat-completions"
    assert model_snapshot.parameters == {"temperature": 0.2}
    assert model_snapshot.secret_ref.secret_id == "model-key-ref"

    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter())
    session = control_plane.create_session(identity, deployment.deployment_id)
    run = control_plane.create_run(identity, session.session_id, "检查模型路由", "model-trace-1")
    model_span = next(
        span
        for span in control_plane.get_trace(identity, run.run_id).spans
        if span.span_type is SpanType.MODEL
    )
    assert model_span.attributes["model"] == "local-model-a"
    assert model_span.attributes["model_alias"] == "assistant-model"
    assert model_span.attributes["provider"] == "openai-compatible"

    repository.save_model_connection(
        identity,
        replace(connection, endpoint="http://host.docker.internal:63073/v1", version=5),
    )
    routes.update(identity, route.route_id, alias="assistant-model-v2", parameters={"temperature": 0.8})

    resumed = resolver.resolve_snapshot(identity, snapshot)
    assert resumed.snapshot == snapshot
    assert resumed.snapshot.model_route_snapshot.endpoint == "http://host.docker.internal:63072/v1"
    assert resumed.snapshot.model_route_snapshot.connection_version == 4
    assert resumed.snapshot.model_route_snapshot.route_version == 1
    assert resumed.snapshot.model_route_snapshot.parameters == {"temperature": 0.2}
    assert "raw-model-api-key" not in repr(resumed.snapshot.model_route_snapshot)
