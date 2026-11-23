-- 接收方 Deployment 运行配置、EngineProfile/ModelRoute 独立默认及历史数据迁移。
-- 目标版本：MySQL 8.4 / InnoDB / utf8mb4。
-- 只允许由显式 migration runner 执行；历史 V003/V008 文件保持不变。

-- 预检并固化现有作用域默认的路由引用。V003 的租户复合外键保证 Profile/Route 属于同一租户。
CREATE TEMPORARY TABLE tmp_v009_model_route_bindings AS
SELECT
    b.platform_id,
    b.tenant_id,
    CONCAT(
        'rt',
        LEFT(
            SHA2(CONCAT_WS(CHAR(31), b.platform_id, b.tenant_id, b.scope_type, b.scope_id), 256),
            24
        )
    ) AS binding_id,
    b.scope_type,
    b.scope_id,
    p.model_route_id AS route_id,
    b.status,
    CAST(NULL AS CHAR(26)) AS created_by,
    b.created_at
FROM execution_profile_bindings AS b
JOIN execution_profiles AS p
    ON p.platform_id = b.platform_id
    AND p.tenant_id = b.tenant_id
    AND p.profile_id = b.profile_id
    AND p.version = b.profile_version
WHERE b.scope_type IN ('platform', 'tenant', 'user')
    AND p.model_route_id IS NOT NULL;

-- 保留已有 Deployment 的精确本地运行配置，避免升级后改变其引擎、模型或策略。
CREATE TEMPORARY TABLE tmp_v009_deployment_runtime_config AS
SELECT
    legacy.platform_id,
    legacy.tenant_id,
    legacy.deployment_id,
    1 AS config_version,
    'OVERRIDE' AS config_mode,
    legacy.execution_profile_id,
    legacy.execution_profile_version,
    legacy.model_route_id,
    CASE
        WHEN route.route_id IS NULL THEN NULL
        ELSE JSON_OBJECT(
            'tenant_id', route.tenant_id,
            'route_id', route.route_id,
            'route_version', route.route_version,
            'route_alias', route.alias,
            'connection_id', route.model_connection_id,
            'connection_version', connection.version,
            'provider_kind', route.provider,
            'model_id', route.catalog_model_id,
            'endpoint', route.endpoint,
            'protocol', route.protocol,
            'secret_ref', CASE
                WHEN route.auth_secret_ref IS NULL THEN NULL
                ELSE JSON_OBJECT(
                    'secret_id', route.auth_secret_ref,
                    'scope', route.auth_secret_scope,
                    'provider', route.auth_secret_provider
                )
            END,
            'parameters', route.parameters_json,
            'pricing_snapshot_ref', route.pricing_snapshot_ref
        )
    END AS model_route_snapshot_json,
    legacy.workspace_profile_id,
    legacy.workspace_profile_version,
    legacy.sandbox_profile_id,
    legacy.sandbox_profile_version,
    legacy.approval_policy_json,
    JSON_OBJECT() AS runtime_options_json,
    legacy.created_by,
    legacy.created_at
FROM (
    SELECT
        d.platform_id,
        d.tenant_id,
        d.deployment_id,
        ev.execution_profile_id,
        ev.execution_profile_version,
        COALESCE(ev.model_route_id, profile.model_route_id) AS model_route_id,
        COALESCE(ev.workspace_profile_id, profile.workspace_profile_id) AS workspace_profile_id,
        COALESCE(ev.workspace_profile_version, profile.workspace_profile_version) AS workspace_profile_version,
        COALESCE(ev.sandbox_profile_id, profile.sandbox_profile_id) AS sandbox_profile_id,
        COALESCE(ev.sandbox_profile_version, profile.sandbox_profile_version) AS sandbox_profile_version,
        ev.approval_policy_json,
        d.created_by,
        d.created_at
    FROM deployments AS d
    JOIN digital_employee_versions AS ev
        ON ev.platform_id = d.platform_id
        AND ev.tenant_id = d.tenant_id
        AND ev.employee_id = d.employee_id
        AND ev.version = d.employee_version
    JOIN execution_profiles AS profile
        ON profile.platform_id = ev.platform_id
        AND profile.tenant_id = ev.tenant_id
        AND profile.profile_id = ev.execution_profile_id
        AND profile.version = ev.execution_profile_version
    WHERE d.status IN ('ACTIVE', 'DISABLED')
) AS legacy
LEFT JOIN model_routes AS route
    ON route.platform_id = legacy.platform_id
    AND route.tenant_id = legacy.tenant_id
    AND route.route_id = legacy.model_route_id
LEFT JOIN model_connections AS connection
    ON connection.platform_id = route.platform_id
    AND connection.tenant_id = route.tenant_id
    AND connection.connection_id = route.model_connection_id;

CREATE TABLE model_route_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '模型路由默认归属租户标识',
    binding_id CHAR(26) NOT NULL COMMENT '模型路由作用域绑定标识',
    scope_type VARCHAR(16) NOT NULL COMMENT '模型路由默认作用域类型：user、tenant 或 platform',
    scope_id VARCHAR(64) NOT NULL COMMENT '模型路由默认作用域标识',
    route_id VARCHAR(64) NOT NULL COMMENT '当前作用域默认模型路由标识',
    status VARCHAR(16) NOT NULL COMMENT '模型路由默认绑定状态：ACTIVE 或 DISABLED',
    created_by CHAR(26) NULL COMMENT '设置默认模型路由的用户标识；系统回填时为空',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '模型路由默认绑定创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '模型路由默认绑定更新时间',
    PRIMARY KEY (platform_id, tenant_id, binding_id),
    UNIQUE KEY uk_model_route_binding_scope (platform_id, tenant_id, scope_type, scope_id),
    KEY idx_model_route_binding_route (platform_id, tenant_id, route_id, status),
    KEY idx_model_route_binding_creator (platform_id, tenant_id, created_by),
    CONSTRAINT fk_model_route_binding_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_model_route_binding_route FOREIGN KEY (platform_id, tenant_id, route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT fk_model_route_binding_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_model_route_binding_scope CHECK (
        (scope_type = 'tenant' AND scope_id = tenant_id)
        OR (scope_type = 'platform' AND scope_id = platform_id)
        OR (scope_type = 'user' AND CHAR_LENGTH(scope_id) > 0)
    ),
    CONSTRAINT ck_model_route_binding_status CHECK (status IN ('ACTIVE', 'DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户租户平台作用域的模型路由默认绑定';

INSERT INTO model_route_bindings (
    platform_id, tenant_id, binding_id, scope_type, scope_id, route_id, status, created_by, created_at
)
SELECT
    platform_id, tenant_id, binding_id, scope_type, scope_id, route_id, status, created_by, created_at
FROM tmp_v009_model_route_bindings;

CREATE TABLE deployment_runtime_config_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '接收方租户标识',
    deployment_id CHAR(26) NOT NULL COMMENT '配置所属的 Deployment 标识',
    config_version INT UNSIGNED NOT NULL COMMENT 'Deployment 运行配置修订号',
    config_mode VARCHAR(16) NOT NULL COMMENT '运行配置模式：继承默认或固定员工配置',
    execution_profile_id CHAR(26) NULL COMMENT '员工专属 ExecutionProfile 覆盖标识，可空表示继承默认',
    execution_profile_version INT UNSIGNED NULL COMMENT '员工专属 ExecutionProfile 覆盖版本号',
    model_route_id VARCHAR(64) NULL COMMENT '员工专属模型路由覆盖标识，可空表示继承默认',
    model_route_snapshot_json JSON NULL COMMENT '员工专属模型路由快照，含受控 SecretRef 但不含凭据明文',
    workspace_profile_id CHAR(26) NULL COMMENT '员工专属工作区配置覆盖标识',
    workspace_profile_version INT UNSIGNED NULL COMMENT '员工专属工作区配置覆盖版本',
    sandbox_profile_id CHAR(26) NULL COMMENT '员工专属沙箱配置覆盖标识',
    sandbox_profile_version INT UNSIGNED NULL COMMENT '员工专属沙箱配置覆盖版本',
    approval_policy_json JSON NOT NULL COMMENT 'Deployment 附加审批策略，不得弱于安全下限',
    runtime_options_json JSON NOT NULL COMMENT '白名单非敏感运行参数，不得包含凭据或任意环境变量',
    created_by CHAR(26) NULL COMMENT '创建运行配置修订的用户标识；系统回填时为空',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '运行配置修订创建时间',
    CONSTRAINT pk_deployment_runtime_config_versions PRIMARY KEY (
        platform_id, tenant_id, deployment_id, config_version
    ),
    KEY idx_runtime_config_creator (platform_id, tenant_id, created_by),
    CONSTRAINT fk_runtime_config_deployment FOREIGN KEY (platform_id, tenant_id, deployment_id)
        REFERENCES deployments(platform_id, tenant_id, deployment_id),
    CONSTRAINT fk_runtime_config_profile FOREIGN KEY (
        platform_id, tenant_id, execution_profile_id, execution_profile_version
    ) REFERENCES execution_profiles(platform_id, tenant_id, profile_id, version),
    CONSTRAINT fk_runtime_config_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT fk_runtime_config_workspace FOREIGN KEY (
        platform_id, tenant_id, workspace_profile_id, workspace_profile_version
    ) REFERENCES workspace_profiles(platform_id, tenant_id, workspace_profile_id, version),
    CONSTRAINT fk_runtime_config_sandbox FOREIGN KEY (
        platform_id, tenant_id, sandbox_profile_id, sandbox_profile_version
    ) REFERENCES sandbox_profiles(platform_id, tenant_id, sandbox_profile_id, version),
    CONSTRAINT fk_runtime_config_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_runtime_config_mode CHECK (config_mode IN ('INHERIT', 'OVERRIDE')),
    CONSTRAINT ck_runtime_config_inherit_no_engine_route_override CHECK (
        config_mode <> 'INHERIT'
        OR (execution_profile_id IS NULL AND execution_profile_version IS NULL AND model_route_id IS NULL)
    ),
    CONSTRAINT ck_runtime_config_override_has_value CHECK (
        config_mode <> 'OVERRIDE' OR execution_profile_id IS NOT NULL OR model_route_id IS NOT NULL
    ),
    CONSTRAINT ck_runtime_config_profile_version_pair CHECK (
        (execution_profile_id IS NULL AND execution_profile_version IS NULL)
        OR (execution_profile_id IS NOT NULL AND execution_profile_version IS NOT NULL)
    ),
    CONSTRAINT ck_runtime_config_workspace_pair CHECK (
        (workspace_profile_id IS NULL AND workspace_profile_version IS NULL)
        OR (workspace_profile_id IS NOT NULL AND workspace_profile_version IS NOT NULL)
    ),
    CONSTRAINT ck_runtime_config_sandbox_pair CHECK (
        (sandbox_profile_id IS NULL AND sandbox_profile_version IS NULL)
        OR (sandbox_profile_id IS NOT NULL AND sandbox_profile_version IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Deployment 接收租户不可变的运行配置修订';

INSERT INTO deployment_runtime_config_versions (
    platform_id, tenant_id, deployment_id, config_version, config_mode,
    execution_profile_id, execution_profile_version, model_route_id, model_route_snapshot_json,
    workspace_profile_id, workspace_profile_version, sandbox_profile_id, sandbox_profile_version,
    approval_policy_json, runtime_options_json, created_by, created_at
)
SELECT
    platform_id, tenant_id, deployment_id, config_version, config_mode,
    execution_profile_id, execution_profile_version, model_route_id, model_route_snapshot_json,
    workspace_profile_id, workspace_profile_version, sandbox_profile_id, sandbox_profile_version,
    approval_policy_json, runtime_options_json, created_by, created_at
FROM tmp_v009_deployment_runtime_config;

ALTER TABLE deployments
    ADD COLUMN runtime_config_version INT UNSIGNED NULL COMMENT 'Deployment 当前生效的接收方运行配置修订；为空时表示待配置';

UPDATE deployments
JOIN tmp_v009_deployment_runtime_config AS config
    ON config.platform_id = deployments.platform_id
    AND config.tenant_id = deployments.tenant_id
    AND config.deployment_id = deployments.deployment_id
SET deployments.runtime_config_version = config.config_version;

ALTER TABLE deployments
    DROP CHECK ck_deployment_status,
    MODIFY COLUMN status VARCHAR(32) NOT NULL COMMENT 'Deployment 生命周期状态，包括待配置、运行中或已停用',
    ADD CONSTRAINT ck_deployment_status CHECK (status IN ('PENDING_CONFIGURATION', 'ACTIVE', 'DISABLED', 'ARCHIVED')),
    ADD CONSTRAINT fk_deployment_runtime_config FOREIGN KEY (
        platform_id, tenant_id, deployment_id, runtime_config_version
    ) REFERENCES deployment_runtime_config_versions(platform_id, tenant_id, deployment_id, config_version);

ALTER TABLE sessions
    ADD COLUMN runtime_config_version INT UNSIGNED NULL COMMENT 'Session 固定的 Deployment 配置修订；历史 Session 可为空',
    ADD COLUMN runtime_profile_source VARCHAR(32) NULL COMMENT 'Session 解析 ExecutionProfile 的作用域来源',
    ADD COLUMN runtime_model_route_source VARCHAR(32) NULL COMMENT 'Session 解析 ModelRoute 的作用域来源',
    ADD KEY idx_session_runtime_config (platform_id, tenant_id, deployment_id, runtime_config_version),
    ADD CONSTRAINT fk_session_runtime_config FOREIGN KEY (
        platform_id, tenant_id, deployment_id, runtime_config_version
    ) REFERENCES deployment_runtime_config_versions(platform_id, tenant_id, deployment_id, config_version),
    ADD CONSTRAINT ck_session_runtime_profile_source CHECK (
        runtime_profile_source IS NULL OR runtime_profile_source IN (
            'DEPLOYMENT_OVERRIDE', 'USER_DEFAULT', 'TENANT_DEFAULT', 'PLATFORM_DEFAULT', 'SYSTEM_DEFAULT'
        )
    ),
    ADD CONSTRAINT ck_session_runtime_model_route_source CHECK (
        runtime_model_route_source IS NULL OR runtime_model_route_source IN (
            'DEPLOYMENT_OVERRIDE', 'USER_DEFAULT', 'TENANT_DEFAULT', 'PLATFORM_DEFAULT', 'SYSTEM_DEFAULT', 'LEGACY_PROFILE'
        )
    );

ALTER TABLE digital_employee_versions
    MODIFY COLUMN execution_profile_id CHAR(26) NULL COMMENT '历史兼容的 ExecutionProfile 标识；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN execution_profile_version INT UNSIGNED NULL COMMENT '历史兼容的 ExecutionProfile 版本；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN model_route_id VARCHAR(64) NULL COMMENT '历史兼容的模型路由标识；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN workspace_profile_id CHAR(26) NULL COMMENT '历史兼容的工作区配置标识；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN workspace_profile_version INT UNSIGNED NULL COMMENT '历史兼容的工作区配置版本；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN sandbox_profile_id CHAR(26) NULL COMMENT '历史兼容的沙箱配置标识；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN sandbox_profile_version INT UNSIGNED NULL COMMENT '历史兼容的沙箱配置版本；新版本运行配置由 Deployment 或作用域默认解析',
    MODIFY COLUMN approval_policy_json JSON NULL COMMENT '历史兼容的审批策略；新版本运行策略由 Deployment 或作用域默认解析';

-- 新 Profile 不必绑定模型；旧 execution_profiles.model_alias 保留为兼容数据。
ALTER TABLE execution_profiles
    MODIFY COLUMN model_alias VARCHAR(128) NULL COMMENT '历史兼容模型别名；新 Session 模型由独立 ModelRoute 或运行时默认解析';

DROP TEMPORARY TABLE tmp_v009_deployment_runtime_config;
DROP TEMPORARY TABLE tmp_v009_model_route_bindings;

INSERT INTO schema_version (version) VALUES (9);
