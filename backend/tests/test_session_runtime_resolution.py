from fastapi.testclient import TestClient

from agent_platform.api.app import create_app


def _create_model_route(
    client: TestClient,
    headers: dict[str, str],
    model_id: str,
    *,
    protocol: str = "openai-chat-completions",
) -> str:
    connection = client.post(
        "/api/v1/model-connections",
        headers=headers,
        json={
            "name": "接收方模型连接",
            "provider_kind": "openai-compatible",
            "protocol": protocol,
            "endpoint": "http://model.local/v1",
            "catalog_mode": "MANUAL",
        },
    )
    assert connection.status_code == 201, connection.text
    connection_id = connection.json()["connection_id"]
    model = client.post(
        f"/api/v1/model-connections/{connection_id}/models",
        headers=headers,
        json={"model_id": model_id, "display_name": model_id},
    )
    assert model.status_code == 201, model.text
    route = client.post(
        "/api/v1/model-routes",
        headers=headers,
        json={
            "alias": f"{model_id} 路由",
            "connection_id": connection_id,
            "catalog_model_id": model_id,
            "model_name": model_id,
        },
    )
    assert route.status_code == 201, route.text
    return str(route.json()["route_id"])


def test_session_resolves_engine_and_model_independently_and_pins_sources() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}

    profile_codex = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "codex_cli"},
    )
    profile_deepseek = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "deepseek_harness"},
    )
    assert profile_codex.status_code == profile_deepseek.status_code == 201
    route_gpt = _create_model_route(client, admin, "gpt-local")
    route_deepseek = _create_model_route(client, admin, "deepseek-local")

    assert client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={
            "profile_id": profile_codex.json()["id"],
            "profile_version": profile_codex.json()["version"],
        },
    ).status_code == 200
    assert client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=admin,
        json={"route_id": route_gpt},
    ).status_code == 200

    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "可移植 Skill", "skill_type": "instruction", "instruction": "按要求回答"},
    )
    assert skill.status_code == 201
    skill_id = skill.json()["id"]
    assert client.post(f"/api/v1/skills/{skill_id}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "无环境员工", "role_name": "助手", "description": "运行环境由接收方设置"},
    )
    employee_id = employee.json()["id"]
    version = client.post(
        f"/api/v1/digital-employees/{employee_id}/versions",
        headers=admin,
        json={"system_prompt": "使用用户问题回答", "skill_ids": [skill_id]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee_id}/versions/1/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee_id, "version": 1},
    )
    assert deployment.status_code == 201
    deployment_id = deployment.json()["id"]
    invalid_options = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "INHERIT",
            "runtime_options": {"unknown_option": True},
        },
    )
    assert invalid_options.status_code == 422
    assert invalid_options.json()["code"] == "INVALID_ARGUMENT"
    configured = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "INHERIT",
            "runtime_options": {
                "reasoning_mode": "DEEP",
                "reasoning_effort": "HIGH",
                "response_verbosity": "STANDARD",
                "max_tool_steps": 12,
            },
        },
    )
    assert configured.status_code == 200

    first_session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment_id},
    )
    assert first_session.status_code == 201, first_session.text
    first = first_session.json()
    assert first["runtime_config_version"] == 1
    assert first["execution_profile_id"] == profile_codex.json()["id"]
    assert first["model_route_id"] == route_gpt
    assert first["runtime_profile_source"] == "USER_DEFAULT"
    assert first["runtime_model_route_source"] == "TENANT_DEFAULT"
    assert first["runtime_options"]["reasoning_mode"] == "DEEP"
    assert first["runtime_options"]["reasoning_effort"] == "HIGH"

    first_run = client.post(
        f"/api/v1/sessions/{first['id']}/runs",
        headers=admin,
        json={"prompt": "第一次运行", "idempotency_key": "runtime-resolution-1"},
    )
    assert first_run.status_code == 201, first_run.text
    assert first_run.json()["snapshot"]["model_route_id"] == route_gpt
    assert first_run.json()["snapshot"]["model_route_snapshot"]["model_id"] == "gpt-local"
    assert first_run.json()["snapshot"]["runtime_profile_source"] == "USER_DEFAULT"
    assert first_run.json()["snapshot"]["runtime_model_route_source"] == "TENANT_DEFAULT"
    assert first_run.json()["snapshot"]["runtime_options"]["reasoning_mode"] == "DEEP"
    assert first_run.json()["snapshot"]["runtime_options"]["max_tool_steps"] == 12

    updated_config = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": 1,
            "mode": "INHERIT",
            "runtime_options": {"reasoning_mode": "OFF"},
        },
    )
    assert updated_config.status_code == 200, updated_config.text
    updated_session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment_id},
    )
    assert updated_session.status_code == 201, updated_session.text
    assert updated_session.json()["runtime_options"]["reasoning_mode"] == "OFF"

    assert client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={
            "profile_id": profile_deepseek.json()["id"],
            "profile_version": profile_deepseek.json()["version"],
        },
    ).status_code == 200
    second_session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment_id},
    )
    assert second_session.status_code == 201, second_session.text
    second = second_session.json()
    assert second["execution_profile_id"] == profile_deepseek.json()["id"]
    assert second["model_route_id"] == route_gpt
    assert second["runtime_profile_source"] == "USER_DEFAULT"
    assert second["runtime_model_route_source"] == "TENANT_DEFAULT"

    assert client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=admin,
        json={"route_id": route_deepseek},
    ).status_code == 200
    third_session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment_id},
    )
    assert third_session.status_code == 201, third_session.text
    third = third_session.json()
    assert third["execution_profile_id"] == profile_deepseek.json()["id"]
    assert third["model_route_id"] == route_deepseek

    old_session_run = client.post(
        f"/api/v1/sessions/{first['id']}/runs",
        headers=admin,
        json={"prompt": "仍使用旧 Session 配置", "idempotency_key": "runtime-resolution-2"},
    )
    assert old_session_run.status_code == 201, old_session_run.text
    assert old_session_run.json()["snapshot"]["execution_profile_id"] == profile_codex.json()["id"]
    assert old_session_run.json()["snapshot"]["model_route_id"] == route_gpt
    assert old_session_run.json()["snapshot"]["model_route_snapshot"]["model_id"] == "gpt-local"
    assert old_session_run.json()["snapshot"]["runtime_options"]["reasoning_mode"] == "DEEP"
    client.close()


def test_partial_deployment_overrides_inherit_only_the_unset_dimension() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    profile_user = client.post(
        "/api/v1/execution-profiles", headers=admin, json={"engine_name": "codex_cli"}
    ).json()
    profile_employee = client.post(
        "/api/v1/execution-profiles", headers=admin, json={"engine_name": "deepseek_harness"}
    ).json()
    route_employee = _create_model_route(client, admin, "employee-model")
    route_default = _create_model_route(client, admin, "tenant-model")
    route_responses = _create_model_route(
        client, admin, "responses-model", protocol="openai-responses"
    )
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "覆盖 Skill", "skill_type": "instruction", "instruction": "按要求回答"},
    ).json()
    assert client.post(f"/api/v1/skills/{skill['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "部分覆盖员工", "role_name": "助手", "description": "维度分别继承"},
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=admin,
        json={"system_prompt": "处理请求", "skill_ids": [skill["id"]]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee["id"], "version": 1},
    ).json()
    deployment_id = deployment["id"]

    route_only = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": None,
            "mode": "OVERRIDE",
            "model_route_id": route_employee,
        },
    )
    assert route_only.status_code == 200
    missing_engine = client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    )
    assert missing_engine.status_code == 409
    assert missing_engine.json()["code"] == "RUNTIME_CONFIGURATION_REQUIRED"

    assert client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": profile_user["id"], "profile_version": profile_user["version"]},
    ).status_code == 200
    assert client.put(
        "/api/v1/model-route-bindings/tenant",
        headers=admin,
        json={"route_id": route_default},
    ).status_code == 200
    inherited_engine = client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    )
    assert inherited_engine.status_code == 201, inherited_engine.text
    assert inherited_engine.json()["execution_profile_id"] == profile_user["id"]
    assert inherited_engine.json()["model_route_id"] == route_employee
    assert inherited_engine.json()["runtime_profile_source"] == "USER_DEFAULT"
    assert inherited_engine.json()["runtime_model_route_source"] == "DEPLOYMENT_OVERRIDE"

    engine_only = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": 1,
            "mode": "OVERRIDE",
            "execution_profile_id": profile_employee["id"],
            "execution_profile_version": profile_employee["version"],
            "model_route_id": None,
        },
    )
    assert engine_only.status_code == 200
    inherited_route = client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    )
    assert inherited_route.status_code == 201, inherited_route.text
    assert inherited_route.json()["execution_profile_id"] == profile_employee["id"]
    assert inherited_route.json()["model_route_id"] == route_default
    assert inherited_route.json()["runtime_profile_source"] == "DEPLOYMENT_OVERRIDE"
    assert inherited_route.json()["runtime_model_route_source"] == "TENANT_DEFAULT"

    incompatible = client.put(
        f"/api/v1/deployments/{deployment_id}/runtime-config",
        headers=admin,
        json={
            "expected_config_version": 2,
            "mode": "OVERRIDE",
            "execution_profile_id": profile_employee["id"],
            "execution_profile_version": profile_employee["version"],
            "model_route_id": route_responses,
        },
    )
    assert incompatible.status_code == 200
    rejected_session = client.post(
        "/api/v1/sessions", headers=admin, json={"deployment_id": deployment_id}
    )
    assert rejected_session.status_code == 409
    assert rejected_session.json()["code"] == "ENGINE_MODEL_INCOMPATIBLE"
    client.close()


def test_legacy_profile_alias_is_used_only_without_an_independent_route() -> None:
    client = TestClient(create_app())
    admin = {"X-Fake-User": "admin-local"}
    profile = client.post(
        "/api/v1/execution-profiles",
        headers=admin,
        json={"engine_name": "fake", "model_alias": "legacy-model"},
    )
    assert profile.status_code == 201
    assert client.put(
        "/api/v1/execution-profile-bindings/user",
        headers=admin,
        json={"profile_id": profile.json()["id"], "profile_version": profile.json()["version"]},
    ).status_code == 200
    skill = client.post(
        "/api/v1/skills",
        headers=admin,
        json={"name": "兼容 Skill", "skill_type": "instruction", "instruction": "回答问题"},
    ).json()
    assert client.post(f"/api/v1/skills/{skill['id']}/publish", headers=admin).status_code == 200
    employee = client.post(
        "/api/v1/digital-employees",
        headers=admin,
        json={"name": "旧别名员工", "role_name": "助手", "description": "历史模型别名兼容"},
    ).json()
    version = client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions",
        headers=admin,
        json={"system_prompt": "处理请求", "skill_ids": [skill["id"]]},
    )
    assert version.status_code == 201
    assert client.post(
        f"/api/v1/digital-employees/{employee['id']}/versions/1/publish", headers=admin
    ).status_code == 200
    deployment = client.post(
        "/api/v1/deployments",
        headers=admin,
        json={"employee_id": employee["id"], "version": 1},
    ).json()
    assert client.put(
        f"/api/v1/deployments/{deployment['id']}/runtime-config",
        headers=admin,
        json={"expected_config_version": None, "mode": "INHERIT"},
    ).status_code == 200

    session = client.post(
        "/api/v1/sessions",
        headers=admin,
        json={"deployment_id": deployment["id"]},
    )
    assert session.status_code == 201, session.text
    assert session.json()["model_route_id"] is None
    assert session.json()["model_alias"] == "legacy-model"
    assert session.json()["runtime_model_route_source"] == "LEGACY_PROFILE"
    run = client.post(
        f"/api/v1/sessions/{session.json()['id']}/runs",
        headers=admin,
        json={"prompt": "历史别名", "idempotency_key": "legacy-alias-run"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["snapshot"]["model_alias"] == "legacy-model"
    assert run.json()["snapshot"]["model_route_id"] is None
    client.close()


def test_hyphenated_legacy_engine_alias_is_canonical_in_session_and_run_snapshot() -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.engine_providers import EngineProviderRegistry
    from agent_platform.domain.catalog import SkillType
    from tests._runtime_config_helpers import configure_in_memory_deployment

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(InMemoryCatalogRepository())
    catalog.create_profile(identity, "codex-cli", "legacy-codex-model")
    skill = catalog.create_skill(identity, "Engine alias Skill", SkillType.INSTRUCTION, "读取", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Engine alias 员工", "审阅", "测试名称规范化")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "只读分析", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)

    runtime = FakeRuntimeAdapter()
    providers = EngineProviderRegistry.for_runtime(runtime, simulate_known_engines=True)
    service = ControlPlaneService(catalog, runtime, engine_providers=providers)
    session = service.create_session(identity, deployment.deployment_id)
    queued = service.prepare_conversation_turn(
        identity, session.session_id, "conversation-engine-alias", "检查固定引擎名"
    )

    assert session.engine_name == "codex_cli"
    assert queued.snapshot.engine_name == session.engine_name
    assert service._runtime_for_run(session, queued) is providers.resolve("codex_cli").adapter


def test_legacy_profile_with_unwired_workspace_policy_is_rejected_before_session() -> None:
    import pytest

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService, InMemoryCatalogRepository
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.engine_providers import EngineProviderRegistry
    from agent_platform.domain.catalog import ExecutionProfile, SkillType
    from agent_platform.domain.common import DomainError

    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repository = InMemoryCatalogRepository()
    catalog = CatalogService(repository)
    profile = ExecutionProfile(
        profile_id="legacy-workspace-profile",
        tenant_id=identity.tenant_id,
        engine_name="deepseek_harness",
        workspace_profile_id="legacy-workspace",
    ).publish()
    repository.save_profile(identity, profile)
    skill = catalog.create_skill(identity, "Legacy Workspace Skill", SkillType.INSTRUCTION, "读取", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Legacy Workspace 员工", "审阅", "测试旧策略")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "只读分析", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    from tests._runtime_config_helpers import configure_in_memory_deployment

    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    runtime = FakeRuntimeAdapter()
    providers = EngineProviderRegistry.for_runtime(runtime, simulate_known_engines=True)
    service = ControlPlaneService(catalog, runtime, engine_providers=providers)

    with pytest.raises(DomainError) as unsupported:
        service.create_session(identity, deployment.deployment_id)

    assert unsupported.value.code == "ENGINE_CAPABILITY_UNSUPPORTED"
