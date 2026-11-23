"""测试用 Deployment 运行配置准备器；生产代码必须通过租户管理员 API 保存。"""

from agent_platform.domain.deployment_runtime import (
    DeploymentRuntimeConfig,
    DeploymentRuntimeMode,
)


def configure_in_memory_deployment(catalog, identity, deployment):
    """把旧测试中的员工 Profile 固定成显式 Deployment 覆盖。"""

    employee_version = deployment.employee_version
    profile_id = employee_version.execution_profile_id
    profile_version = employee_version.execution_profile_version
    if profile_id is None:
        profiles = catalog.repository.list_profiles(identity)
        profile = profiles[0] if profiles else catalog.create_profile(identity, "fake", "fake-model")
        profile_id = profile.profile_id
        profile_version = profile.version
    profile = catalog.repository.get_profile_version(identity, profile_id, profile_version or 1)
    route_id = employee_version.model_route_id or profile.model_route_id
    workspace_id = employee_version.workspace_profile_id or profile.workspace_profile_id
    workspace_version = employee_version.workspace_profile_version or profile.workspace_profile_version
    sandbox_id = employee_version.sandbox_profile_id or profile.sandbox_profile_id
    sandbox_version = employee_version.sandbox_profile_version or profile.sandbox_profile_version
    config_version = (deployment.runtime_config_version or 0) + 1
    config = DeploymentRuntimeConfig(
        platform_id=identity.platform_id,
        tenant_id=identity.tenant_id,
        deployment_id=deployment.deployment_id,
        config_version=config_version,
        config_mode=DeploymentRuntimeMode.OVERRIDE,
        execution_profile_id=profile_id,
        execution_profile_version=profile_version,
        model_route_id=route_id,
        workspace_profile_id=workspace_id,
        workspace_profile_version=workspace_version,
        sandbox_profile_id=sandbox_id,
        sandbox_profile_version=sandbox_version,
        approval_policy={"name": employee_version.approval_policy},
    )
    catalog.repository.save_deployment_runtime_config(
        identity,
        deployment.deployment_id,
        deployment.runtime_config_version,
        config,
    )
    return catalog.repository.get_deployment(identity, deployment.deployment_id)


def configure_api_deployment(client, headers, deployment, *, route_id=None):
    """通过真实 API 为旧测试显式固定接收方的 Deployment 配置。"""

    providers = client.get("/api/v1/engine-providers", headers=headers)
    assert providers.status_code == 200, providers.text
    available = {
        item["engine_name"] for item in providers.json().get("items", [])
    }
    if not available:
        raise AssertionError("测试应用没有已注册的 EngineProvider")
    preferred_engine = "fake" if "fake" in available else min(available)
    profiles = client.get("/api/v1/execution-profiles", headers=headers)
    assert profiles.status_code == 200, profiles.text
    profile_items = profiles.json().get("items", [])
    selected_profiles = [
        item
        for item in profile_items
        if item["engine_name"] in available
    ]
    profile = next(
        (item for item in selected_profiles if item["engine_name"] == preferred_engine),
        selected_profiles[0] if selected_profiles else None,
    )
    if profile is None:
        created = client.post(
            "/api/v1/execution-profiles",
            headers=headers,
            json={"engine_name": preferred_engine},
        )
        assert created.status_code == 201, created.text
        profile = created.json()
    profile_id = profile["id"]
    profile_version = profile["version"]
    if route_id is None:
        route_id = profile.get("model_route_id")
    response = client.put(
        f"/api/v1/deployments/{deployment['id']}/runtime-config",
        headers=headers,
        json={
            "expected_config_version": deployment.get("runtime_config_version"),
            "mode": "OVERRIDE",
            "execution_profile_id": profile_id,
            "execution_profile_version": profile_version,
            "model_route_id": route_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
