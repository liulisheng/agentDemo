-- Agent Platform control-plane schema.
-- 目标版本：MySQL 8.4 / InnoDB / utf8mb4。
-- 只能通过显式 migration runner 执行；应用启动禁止执行 DDL。

CREATE TABLE schema_migration_history (
    version SMALLINT UNSIGNED NOT NULL COMMENT '迁移版本号',
    migration_name VARCHAR(128) NOT NULL COMMENT '迁移文件名称',
    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '迁移文件 SHA-256 校验值',
    applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '迁移成功时间',
    PRIMARY KEY (version),
    UNIQUE KEY uk_schema_migration_name (migration_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据库迁移版本与文件校验记录';

CREATE TABLE digital_employees (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工稳定标识',
    owner_user_id CHAR(26) NULL COMMENT '数字员工负责人用户标识',
    name VARCHAR(128) NOT NULL COMMENT '数字员工名称',
    role_name VARCHAR(128) NOT NULL COMMENT '数字员工岗位名称',
    description VARCHAR(2048) NOT NULL COMMENT '数字员工用途描述',
    status VARCHAR(32) NOT NULL COMMENT '数字员工生命周期状态',
    published_to_gallery BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否发布到员工广场',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '数字员工创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '数字员工更新时间',
    PRIMARY KEY (platform_id, tenant_id, employee_id),
    KEY idx_digital_employee_owner (platform_id, tenant_id, owner_user_id, status),
    CONSTRAINT fk_digital_employee_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_digital_employee_owner FOREIGN KEY (platform_id, tenant_id, owner_user_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_digital_employee_status CHECK (status IN ('DRAFT', 'ACTIVE', 'DISABLED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内的数字员工主数据';

CREATE TABLE skill_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    skill_id CHAR(26) NOT NULL COMMENT 'Skill 稳定标识',
    version INT UNSIGNED NOT NULL COMMENT 'Skill 不可变版本号',
    name VARCHAR(128) NOT NULL COMMENT 'Skill 名称',
    skill_type VARCHAR(32) NOT NULL COMMENT 'Skill 类型',
    instruction MEDIUMTEXT NOT NULL COMMENT 'Skill 指令正文',
    input_schema_json JSON NOT NULL COMMENT 'Skill 输入 JSON Schema',
    output_schema_json JSON NOT NULL COMMENT 'Skill 输出 JSON Schema',
    trigger_rule VARCHAR(512) NULL COMMENT 'Skill 触发规则',
    workflow_steps_json JSON NULL COMMENT 'Workflow 步骤定义',
    memory_policy_json JSON NOT NULL COMMENT 'Skill 记忆策略快照',
    approval_policy_json JSON NOT NULL COMMENT 'Skill 审批策略快照',
    status VARCHAR(32) NOT NULL COMMENT 'Skill 版本生命周期状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '版本创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '版本发布时间',
    PRIMARY KEY (platform_id, tenant_id, skill_id, version),
    KEY idx_skill_catalog (platform_id, tenant_id, status, name),
    CONSTRAINT fk_skill_version_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_skill_type CHECK (skill_type IN ('instruction', 'tool-bundle', 'workflow')),
    CONSTRAINT ck_skill_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内不可变的 Skill 版本';

CREATE TABLE tool_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    tool_id CHAR(26) NOT NULL COMMENT 'Tool 稳定标识',
    version INT UNSIGNED NOT NULL COMMENT 'Tool 不可变版本号',
    name VARCHAR(128) NOT NULL COMMENT 'Tool 名称',
    protocol VARCHAR(32) NOT NULL COMMENT 'Tool 调用协议',
    description VARCHAR(1024) NOT NULL COMMENT 'Tool 用途说明',
    input_schema_json JSON NOT NULL COMMENT 'Tool 输入 JSON Schema',
    output_schema_json JSON NOT NULL COMMENT 'Tool 输出 JSON Schema',
    auth_secret_ref VARCHAR(255) NULL COMMENT '凭据 Secret 引用，不保存密钥明文',
    timeout_ms INT UNSIGNED NOT NULL COMMENT '单次 Tool 调用超时毫秒数',
    retry_policy_json JSON NOT NULL COMMENT 'Tool 重试策略',
    risk_policy VARCHAR(32) NOT NULL COMMENT 'Tool 风险等级策略',
    handler_name VARCHAR(128) NULL COMMENT '受控 Tool handler 名称',
    status VARCHAR(32) NOT NULL COMMENT 'Tool 版本生命周期状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '版本创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '版本发布时间',
    PRIMARY KEY (platform_id, tenant_id, tool_id, version),
    KEY idx_tool_catalog (platform_id, tenant_id, status, name),
    CONSTRAINT fk_tool_version_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_tool_protocol CHECK (protocol IN ('builtin', 'api', 'mcp', 'a2a')),
    CONSTRAINT ck_tool_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内不可变的 Tool 版本';

CREATE TABLE skill_tool_declarations (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    skill_id CHAR(26) NOT NULL COMMENT '声明工具的 Skill 标识',
    skill_version INT UNSIGNED NOT NULL COMMENT '声明工具的 Skill 版本号',
    tool_id CHAR(26) NOT NULL COMMENT 'Skill 声明允许的 Tool 标识',
    tool_version INT UNSIGNED NOT NULL COMMENT 'Skill 声明允许的 Tool 版本号',
    sequence_no INT UNSIGNED NOT NULL COMMENT 'Tool 在 Skill 声明中的顺序',
    PRIMARY KEY (platform_id, tenant_id, skill_id, skill_version, tool_id, tool_version),
    UNIQUE KEY uk_skill_tool_declaration_order (platform_id, tenant_id, skill_id, skill_version, sequence_no),
    CONSTRAINT fk_skill_tool_skill FOREIGN KEY (platform_id, tenant_id, skill_id, skill_version)
        REFERENCES skill_versions(platform_id, tenant_id, skill_id, version),
    CONSTRAINT fk_skill_tool_tool FOREIGN KEY (platform_id, tenant_id, tool_id, tool_version)
        REFERENCES tool_versions(platform_id, tenant_id, tool_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Skill 版本声明并允许调用的 Tool 版本列表';

CREATE TABLE model_routes (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    route_id VARCHAR(64) NOT NULL COMMENT '模型路由稳定标识',
    alias VARCHAR(128) NOT NULL COMMENT '业务使用的模型别名',
    provider VARCHAR(64) NOT NULL COMMENT '模型 Provider 标识',
    model_name VARCHAR(128) NOT NULL COMMENT 'Provider 模型名称',
    endpoint VARCHAR(1024) NOT NULL COMMENT '模型服务入口地址',
    protocol VARCHAR(64) NOT NULL COMMENT 'Provider 协议类型',
    auth_secret_ref VARCHAR(255) NULL COMMENT '凭据 Secret 引用，不保存密钥明文',
    auth_secret_scope VARCHAR(64) NULL COMMENT '模型凭据 Secret 的授权作用域',
    auth_secret_provider VARCHAR(64) NULL COMMENT '模型凭据 Secret 的解析 Provider',
    pricing_snapshot_ref VARCHAR(255) NULL COMMENT '定价快照引用',
    parameters_json JSON NOT NULL COMMENT '非敏感模型调用参数',
    status VARCHAR(32) NOT NULL COMMENT '模型路由状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '路由创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '路由更新时间',
    PRIMARY KEY (platform_id, tenant_id, route_id),
    UNIQUE KEY uk_model_route_alias (platform_id, tenant_id, alias),
    CONSTRAINT fk_model_route_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_model_route_status CHECK (status IN ('ACTIVE', 'DISABLED', 'REVOKED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内显式配置的模型路由';

CREATE TABLE workspace_profiles (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    workspace_profile_id CHAR(26) NOT NULL COMMENT '工作区配置标识',
    version INT UNSIGNED NOT NULL COMMENT '工作区配置版本号',
    mode VARCHAR(32) NOT NULL COMMENT '工作区模式',
    project_id VARCHAR(64) NULL COMMENT '关联项目标识',
    policy_json JSON NOT NULL COMMENT '工作区文件、仓库和配额策略',
    status VARCHAR(32) NOT NULL COMMENT '工作区配置状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '配置创建时间',
    PRIMARY KEY (platform_id, tenant_id, workspace_profile_id, version),
    CONSTRAINT fk_workspace_profile_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_workspace_mode CHECK (mode IN ('no-files', 'ephemeral', 'persistent')),
    CONSTRAINT ck_workspace_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内的工作区策略版本';

CREATE TABLE sandbox_profiles (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    sandbox_profile_id CHAR(26) NOT NULL COMMENT '沙箱配置标识',
    version INT UNSIGNED NOT NULL COMMENT '沙箱配置版本号',
    network_policy VARCHAR(64) NOT NULL COMMENT '沙箱网络策略',
    resource_limits_json JSON NOT NULL COMMENT 'CPU、内存和执行时间限制',
    security_policy_json JSON NOT NULL COMMENT '宿主路径、设备、权限和凭据限制',
    status VARCHAR(32) NOT NULL COMMENT '沙箱配置状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '配置创建时间',
    PRIMARY KEY (platform_id, tenant_id, sandbox_profile_id, version),
    CONSTRAINT fk_sandbox_profile_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_sandbox_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内的沙箱策略版本';

CREATE TABLE execution_profiles (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    profile_id CHAR(26) NOT NULL COMMENT 'ExecutionProfile 稳定标识',
    version INT UNSIGNED NOT NULL COMMENT 'ExecutionProfile 不可变版本号',
    engine_name VARCHAR(64) NOT NULL COMMENT 'Runtime 引擎名称',
    model_alias VARCHAR(128) NOT NULL COMMENT '默认模型别名',
    model_route_id VARCHAR(64) NULL COMMENT '绑定的模型路由标识',
    workspace_profile_id CHAR(26) NULL COMMENT '绑定的工作区配置标识',
    workspace_profile_version INT UNSIGNED NULL COMMENT '绑定的工作区配置版本',
    sandbox_profile_id CHAR(26) NULL COMMENT '绑定的沙箱配置标识',
    sandbox_profile_version INT UNSIGNED NULL COMMENT '绑定的沙箱配置版本',
    tool_policy_json JSON NOT NULL COMMENT '允许调用的 Tool 策略',
    approval_policy_json JSON NOT NULL COMMENT '审批策略',
    context_policy_json JSON NOT NULL COMMENT '业务上下文预算策略',
    status VARCHAR(32) NOT NULL COMMENT 'ExecutionProfile 生命周期状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '配置创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '配置发布时间',
    PRIMARY KEY (platform_id, tenant_id, profile_id, version),
    CONSTRAINT fk_execution_profile_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_execution_profile_model_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT fk_execution_profile_workspace FOREIGN KEY (platform_id, tenant_id, workspace_profile_id, workspace_profile_version)
        REFERENCES workspace_profiles(platform_id, tenant_id, workspace_profile_id, version),
    CONSTRAINT fk_execution_profile_sandbox FOREIGN KEY (platform_id, tenant_id, sandbox_profile_id, sandbox_profile_version)
        REFERENCES sandbox_profiles(platform_id, tenant_id, sandbox_profile_id, version),
    CONSTRAINT ck_execution_profile_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内不可变的 Runtime 执行配置';

CREATE TABLE execution_profile_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    binding_id CHAR(26) NOT NULL COMMENT 'Profile 绑定标识',
    scope_type VARCHAR(16) NOT NULL COMMENT '绑定作用域类型',
    scope_id VARCHAR(64) NOT NULL COMMENT '平台、租户或用户作用域标识',
    profile_id CHAR(26) NOT NULL COMMENT 'ExecutionProfile 标识',
    profile_version INT UNSIGNED NOT NULL COMMENT 'ExecutionProfile 版本号',
    status VARCHAR(16) NOT NULL COMMENT '绑定状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '绑定创建时间',
    PRIMARY KEY (platform_id, tenant_id, binding_id),
    UNIQUE KEY uk_execution_profile_scope (platform_id, tenant_id, scope_type, scope_id),
    CONSTRAINT fk_execution_profile_binding_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_execution_profile_binding_profile FOREIGN KEY (platform_id, tenant_id, profile_id, profile_version)
        REFERENCES execution_profiles(platform_id, tenant_id, profile_id, version),
    CONSTRAINT ck_execution_profile_scope CHECK (scope_type IN ('platform', 'tenant', 'user', 'project')),
    CONSTRAINT ck_execution_profile_binding_status CHECK (status IN ('ACTIVE', 'DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='ExecutionProfile 的作用域绑定';

CREATE TABLE digital_employee_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工稳定标识',
    version INT UNSIGNED NOT NULL COMMENT '数字员工不可变版本号',
    system_prompt MEDIUMTEXT NOT NULL COMMENT '员工版本系统指令',
    execution_profile_id CHAR(26) NOT NULL COMMENT '绑定的 ExecutionProfile 标识',
    execution_profile_version INT UNSIGNED NOT NULL COMMENT '绑定的 ExecutionProfile 版本号',
    model_route_id VARCHAR(64) NULL COMMENT '员工版本固定的模型路由标识',
    workspace_profile_id CHAR(26) NULL COMMENT '员工版本固定的工作区配置标识',
    workspace_profile_version INT UNSIGNED NULL COMMENT '员工版本固定的工作区配置版本',
    sandbox_profile_id CHAR(26) NULL COMMENT '员工版本固定的沙箱配置标识',
    sandbox_profile_version INT UNSIGNED NULL COMMENT '员工版本固定的沙箱配置版本',
    approval_policy_json JSON NOT NULL COMMENT '员工版本审批策略快照',
    status VARCHAR(32) NOT NULL COMMENT '员工版本生命周期状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '员工版本创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '员工版本发布时间',
    PRIMARY KEY (platform_id, tenant_id, employee_id, version),
    KEY idx_employee_version_status (platform_id, tenant_id, employee_id, status),
    CONSTRAINT fk_employee_version_employee FOREIGN KEY (platform_id, tenant_id, employee_id)
        REFERENCES digital_employees(platform_id, tenant_id, employee_id),
    CONSTRAINT fk_employee_version_profile FOREIGN KEY (platform_id, tenant_id, execution_profile_id, execution_profile_version)
        REFERENCES execution_profiles(platform_id, tenant_id, profile_id, version),
    CONSTRAINT fk_employee_version_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT fk_employee_version_workspace FOREIGN KEY (platform_id, tenant_id, workspace_profile_id, workspace_profile_version)
        REFERENCES workspace_profiles(platform_id, tenant_id, workspace_profile_id, version),
    CONSTRAINT fk_employee_version_sandbox FOREIGN KEY (platform_id, tenant_id, sandbox_profile_id, sandbox_profile_version)
        REFERENCES sandbox_profiles(platform_id, tenant_id, sandbox_profile_id, version),
    CONSTRAINT ck_employee_version_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数字员工不可变配置版本';

CREATE TABLE deployments (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    deployment_id CHAR(26) NOT NULL COMMENT 'Deployment 稳定标识',
    employee_id CHAR(26) NOT NULL COMMENT '被部署的数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '被部署的数字员工版本号',
    status VARCHAR(32) NOT NULL COMMENT 'Deployment 生命周期状态',
    created_by CHAR(26) NULL COMMENT '执行部署操作的用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Deployment 创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Deployment 更新时间',
    PRIMARY KEY (platform_id, tenant_id, deployment_id),
    KEY idx_deployment_employee_version (platform_id, tenant_id, employee_id, employee_version),
    CONSTRAINT fk_deployment_employee_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT fk_deployment_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_deployment_status CHECK (status IN ('ACTIVE', 'DISABLED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='固定数字员工发布版本的租户部署记录';

CREATE TABLE employee_skill_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '数字员工版本号',
    skill_id CHAR(26) NOT NULL COMMENT 'Skill 稳定标识',
    skill_version INT UNSIGNED NOT NULL COMMENT 'Skill 不可变版本号',
    sequence_no INT UNSIGNED NOT NULL COMMENT 'Skill 在员工版本中的执行顺序',
    input_mapping_json JSON NOT NULL COMMENT '员工输入到 Skill 输入的映射',
    output_policy_json JSON NOT NULL COMMENT 'Skill 输出处理策略',
    PRIMARY KEY (platform_id, tenant_id, employee_id, employee_version, skill_id, skill_version),
    UNIQUE KEY uk_employee_skill_order (platform_id, tenant_id, employee_id, employee_version, sequence_no),
    CONSTRAINT fk_employee_skill_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT fk_employee_skill_resource FOREIGN KEY (platform_id, tenant_id, skill_id, skill_version)
        REFERENCES skill_versions(platform_id, tenant_id, skill_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数字员工版本绑定的有序 Skill 资源';

CREATE TABLE employee_tool_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '数字员工版本号',
    tool_id CHAR(26) NOT NULL COMMENT 'Tool 稳定标识',
    tool_version INT UNSIGNED NOT NULL COMMENT 'Tool 不可变版本号',
    sequence_no INT UNSIGNED NOT NULL COMMENT 'Tool 在员工版本中的展示顺序',
    config_json JSON NOT NULL COMMENT '员工版本绑定 Tool 的配置',
    PRIMARY KEY (platform_id, tenant_id, employee_id, employee_version, tool_id, tool_version),
    UNIQUE KEY uk_employee_tool_order (platform_id, tenant_id, employee_id, employee_version, sequence_no),
    CONSTRAINT fk_employee_tool_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT fk_employee_tool_resource FOREIGN KEY (platform_id, tenant_id, tool_id, tool_version)
        REFERENCES tool_versions(platform_id, tenant_id, tool_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数字员工版本绑定的 Tool 资源';

CREATE TABLE capability_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    binding_id CHAR(26) NOT NULL COMMENT 'CapabilityBinding 稳定标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '数字员工版本号',
    slot VARCHAR(32) NOT NULL COMMENT '能力槽位类型',
    module_id VARCHAR(64) NOT NULL COMMENT '能力模块标识',
    module_version INT UNSIGNED NOT NULL COMMENT '能力模块版本号',
    config_revision INT UNSIGNED NOT NULL COMMENT '绑定配置修订号',
    resource_ref VARCHAR(512) NOT NULL COMMENT '能力资源的 opaque 引用',
    config_json JSON NOT NULL COMMENT '能力绑定非敏感配置',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '能力绑定创建时间',
    PRIMARY KEY (platform_id, tenant_id, binding_id),
    UNIQUE KEY uk_capability_slot (platform_id, tenant_id, employee_id, employee_version, slot, module_id),
    CONSTRAINT fk_capability_employee_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT ck_capability_slot CHECK (slot IN ('knowledge', 'skill', 'tool', 'sop'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='员工版本组合知识、Skill、Tool 和 SOP 的能力绑定';

ALTER TABLE sessions
    ADD COLUMN deployment_id CHAR(26) NULL COMMENT 'Session 所属的员工 Deployment',
    ADD COLUMN employee_id CHAR(26) NULL COMMENT 'Session 固定的数字员工标识',
    ADD COLUMN employee_version INT UNSIGNED NULL COMMENT 'Session 固定的员工版本号',
    ADD COLUMN execution_profile_id CHAR(26) NULL COMMENT 'Session 固定的 ExecutionProfile 标识',
    ADD COLUMN execution_profile_version INT UNSIGNED NULL COMMENT 'Session 固定的 ExecutionProfile 版本号',
    ADD COLUMN model_route_id VARCHAR(64) NULL COMMENT 'Session 固定的模型路由标识',
    ADD COLUMN trace_id VARCHAR(64) NULL COMMENT 'Session 级 Trace 标识',
    ADD COLUMN composition_snapshot_json JSON NULL COMMENT 'Runtime 启动时固化的能力组成快照',
    ADD COLUMN runtime_thread_ref VARCHAR(512) NULL COMMENT 'Runtime 私有 Thread 的 opaque 引用',
    ADD COLUMN runtime_workspace_ref VARCHAR(512) NULL COMMENT 'Runtime 私有 Workspace 的 opaque 引用',
    ADD COLUMN runtime_result_ref VARCHAR(512) NULL COMMENT 'Runtime Session 结果的 opaque 引用',
    ADD KEY idx_session_deployment (platform_id, tenant_id, deployment_id, updated_at),
    ADD CONSTRAINT fk_session_deployment FOREIGN KEY (platform_id, tenant_id, deployment_id)
        REFERENCES deployments(platform_id, tenant_id, deployment_id),
    ADD CONSTRAINT fk_session_employee_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    ADD CONSTRAINT fk_session_execution_profile FOREIGN KEY (platform_id, tenant_id, execution_profile_id, execution_profile_version)
        REFERENCES execution_profiles(platform_id, tenant_id, profile_id, version),
    ADD CONSTRAINT fk_session_model_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    COMMENT='Harness Runtime 会话及其平台目录绑定';

ALTER TABLE runs
    ADD COLUMN employee_id CHAR(26) NULL COMMENT 'Run 固定的数字员工标识',
    ADD COLUMN employee_version INT UNSIGNED NULL COMMENT 'Run 固定的员工版本号',
    ADD COLUMN execution_profile_id CHAR(26) NULL COMMENT 'Run 固定的 ExecutionProfile 标识',
    ADD COLUMN execution_profile_version INT UNSIGNED NULL COMMENT 'Run 固定的 ExecutionProfile 版本号',
    ADD COLUMN model_route_id VARCHAR(64) NULL COMMENT 'Run 固定的模型路由标识',
    ADD COLUMN trace_id VARCHAR(64) NULL COMMENT 'Run 级 Trace 标识',
    ADD COLUMN snapshot_json JSON NULL COMMENT 'Run 启动时固化的目录和策略快照',
    ADD COLUMN context_snapshot_json JSON NULL COMMENT 'Run 使用的业务上下文引用快照',
    ADD COLUMN runtime_session_ref VARCHAR(512) NULL COMMENT 'Runtime Session 的 opaque 引用',
    ADD COLUMN runtime_result_ref VARCHAR(512) NULL COMMENT 'Runtime 最终结果的 opaque 引用',
    DROP INDEX uk_run_idempotency,
    ADD UNIQUE KEY uk_run_idempotency (platform_id, tenant_id, session_id, idempotency_key),
    ADD KEY idx_run_trace (platform_id, tenant_id, trace_id, created_at),
    ADD CONSTRAINT fk_run_employee_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    ADD CONSTRAINT fk_run_execution_profile FOREIGN KEY (platform_id, tenant_id, execution_profile_id, execution_profile_version)
        REFERENCES execution_profiles(platform_id, tenant_id, profile_id, version),
    ADD CONSTRAINT fk_run_model_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    COMMENT='Run 状态、幂等、租约和平台执行快照';

CREATE TABLE conversations (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT '用户可见 Conversation 标识',
    title VARCHAR(255) NOT NULL COMMENT 'Conversation 标题',
    is_group BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否为群聊 Conversation',
    status VARCHAR(32) NOT NULL COMMENT 'Conversation 生命周期状态',
    created_by CHAR(26) NOT NULL COMMENT 'Conversation 创建者用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Conversation 创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Conversation 更新时间',
    PRIMARY KEY (platform_id, tenant_id, conversation_id),
    KEY idx_conversation_page (platform_id, tenant_id, status, updated_at),
    CONSTRAINT fk_conversation_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_conversation_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_conversation_status CHECK (status IN ('ACTIVE', 'ARCHIVED', 'DELETED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户可见的多消息对话容器';

ALTER TABLE messages
    ADD COLUMN sender_id CHAR(26) NULL COMMENT '创建消息的租户用户标识',
    ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'user' COMMENT '消息来源类型',
    ADD COLUMN routing_depth INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '消息触发路由的递归深度',
    ADD COLUMN conversation_id VARCHAR(64) NULL COMMENT '消息所属的用户可见 Conversation',
    ADD COLUMN retention_policy VARCHAR(32) NOT NULL DEFAULT 'conversation' COMMENT '消息正文和附件的保留策略',
    ADD KEY idx_message_conversation (platform_id, tenant_id, conversation_id, sequence_no),
    ADD CONSTRAINT fk_message_sender FOREIGN KEY (platform_id, tenant_id, sender_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    ADD CONSTRAINT ck_message_source CHECK (source IN ('user', 'agent', 'system', 'channel')),
    ADD CONSTRAINT fk_message_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    COMMENT='Conversation 消息索引和正文对象引用';

CREATE TABLE conversation_participants (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT 'Conversation 标识',
    user_id CHAR(26) NOT NULL COMMENT '参与者用户标识',
    role VARCHAR(32) NOT NULL COMMENT '参与者在 Conversation 中的角色',
    joined_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '参与者加入时间',
    PRIMARY KEY (platform_id, tenant_id, conversation_id, user_id),
    CONSTRAINT fk_conversation_participant_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_conversation_participant_user FOREIGN KEY (platform_id, tenant_id, user_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_conversation_participant_role CHECK (role IN ('owner', 'member', 'viewer'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Conversation 的用户参与者及权限角色';

CREATE TABLE agent_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    binding_id CHAR(26) NOT NULL COMMENT 'AgentBinding 稳定标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT 'Conversation 标识',
    employee_id CHAR(26) NOT NULL COMMENT '绑定的数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '绑定的数字员工版本号',
    deployment_id CHAR(26) NOT NULL COMMENT '绑定的数字员工 Deployment',
    session_id CHAR(26) NOT NULL COMMENT '该 Agent 在 Conversation 中独立使用的 Session',
    status VARCHAR(16) NOT NULL COMMENT 'AgentBinding 状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'AgentBinding 创建时间',
    PRIMARY KEY (platform_id, tenant_id, binding_id),
    UNIQUE KEY uk_conversation_employee (platform_id, tenant_id, conversation_id, employee_id),
    CONSTRAINT fk_agent_binding_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_agent_binding_employee_version FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT fk_agent_binding_deployment FOREIGN KEY (platform_id, tenant_id, deployment_id)
        REFERENCES deployments(platform_id, tenant_id, deployment_id),
    CONSTRAINT fk_agent_binding_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT ck_agent_binding_status CHECK (status IN ('ACTIVE', 'DISABLED', 'REMOVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Conversation 中员工与独立 Session 的绑定';

CREATE TABLE media_assets (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    asset_id VARCHAR(64) NOT NULL COMMENT 'MediaAsset 稳定标识',
    object_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'LocalObjectStore 生成的 opaque 对象引用',
    filename VARCHAR(255) NOT NULL COMMENT '原始文件名，仅用于显示',
    mime_type VARCHAR(128) NOT NULL COMMENT '媒体内容类型',
    size_bytes BIGINT UNSIGNED NOT NULL COMMENT '媒体对象字节数',
    content_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '媒体对象 SHA-256 摘要',
    retention_policy VARCHAR(32) NOT NULL COMMENT '媒体对象保留策略',
    retention_expires_at TIMESTAMP(6) NULL COMMENT '媒体对象计划自动过期时间',
    status VARCHAR(32) NOT NULL COMMENT '媒体对象扫描和处理状态',
    encryption_key_version VARCHAR(64) NULL COMMENT '加密密钥版本引用',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '媒体对象登记时间',
    deleted_at TIMESTAMP(6) NULL COMMENT '应用读取权限撤销时间',
    PRIMARY KEY (platform_id, tenant_id, asset_id),
    UNIQUE KEY uk_media_object_ref (platform_id, tenant_id, object_ref),
    KEY idx_media_asset_status (platform_id, tenant_id, status, created_at),
    CONSTRAINT fk_media_asset_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_media_retention CHECK (retention_policy IN ('ephemeral', 'session', 'conversation', 'knowledge_base')),
    CONSTRAINT ck_media_status CHECK (status IN ('INIT', 'UPLOADING', 'UPLOADED', 'SCANNING', 'PROCESSING', 'READY', 'FAILED', 'REJECTED', 'DELETED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='本地对象内容的租户授权、校验和保留元数据';

CREATE TABLE platform_trace_spans (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    run_id CHAR(26) NOT NULL COMMENT '关联 Run 标识',
    span_id VARCHAR(64) NOT NULL COMMENT 'Trace Span 稳定标识',
    trace_id VARCHAR(64) NOT NULL COMMENT 'Trace 链路标识',
    parent_span_id VARCHAR(64) NULL COMMENT '父级 Span 标识',
    span_type VARCHAR(32) NOT NULL COMMENT 'Span 业务类型',
    status VARCHAR(32) NOT NULL COMMENT 'Span 执行状态',
    sequence_no INT UNSIGNED NOT NULL COMMENT 'Trace 中的稳定排序号',
    attributes_json JSON NOT NULL COMMENT '脱敏后的 Span 属性',
    started_at TIMESTAMP(6) NULL COMMENT 'Span 开始时间',
    finished_at TIMESTAMP(6) NULL COMMENT 'Span 结束时间',
    error_category VARCHAR(128) NULL COMMENT '稳定错误类别',
    PRIMARY KEY (platform_id, tenant_id, span_id),
    UNIQUE KEY uk_trace_sequence (platform_id, tenant_id, trace_id, sequence_no),
    KEY idx_trace_run (platform_id, tenant_id, run_id, sequence_no),
    CONSTRAINT fk_trace_span_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Session 和 Run 的租户范围 Trace 时间线';

CREATE TABLE usage_ledger (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    call_id VARCHAR(64) NOT NULL COMMENT '模型调用记录标识',
    run_id CHAR(26) NOT NULL COMMENT '关联 Run 标识',
    session_id CHAR(26) NOT NULL COMMENT '关联 Session 标识',
    user_id CHAR(26) NOT NULL COMMENT '发起调用的用户标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工标识',
    model_route_id VARCHAR(64) NULL COMMENT '模型路由标识',
    provider_request_id VARCHAR(255) NULL COMMENT 'Provider 请求标识',
    input_tokens BIGINT UNSIGNED NOT NULL COMMENT '输入 Token 数',
    output_tokens BIGINT UNSIGNED NOT NULL COMMENT '输出 Token 数',
    cached_input_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '缓存输入 Token 数',
    reasoning_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '推理 Token 数',
    total_tokens BIGINT UNSIGNED NOT NULL COMMENT '总 Token 数',
    cost_amount DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '本次调用费用',
    currency CHAR(3) NOT NULL DEFAULT 'CNY' COMMENT '费用币种',
    pricing_version VARCHAR(64) NOT NULL COMMENT '本次调用采用的价格版本',
    usage_status VARCHAR(16) NOT NULL COMMENT '用量为精确、估算或不可用',
    estimation_method VARCHAR(128) NULL COMMENT '估算用量的方法版本',
    duration_ms BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '模型调用耗时毫秒',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '用量记录创建时间',
    PRIMARY KEY (platform_id, tenant_id, call_id),
    KEY idx_usage_run (platform_id, tenant_id, run_id, created_at),
    KEY idx_usage_user (platform_id, tenant_id, user_id, created_at),
    KEY idx_usage_employee (platform_id, tenant_id, employee_id, created_at),
    CONSTRAINT fk_usage_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id),
    CONSTRAINT fk_usage_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT fk_usage_user FOREIGN KEY (platform_id, tenant_id, user_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_usage_employee FOREIGN KEY (platform_id, tenant_id, employee_id)
        REFERENCES digital_employees(platform_id, tenant_id, employee_id),
    CONSTRAINT ck_usage_status CHECK (usage_status IN ('EXACT', 'ESTIMATED', 'UNAVAILABLE')),
    CONSTRAINT ck_usage_total_tokens CHECK (total_tokens = input_tokens + output_tokens)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='不可变的模型调用 Token、费用和定价快照台账';

CREATE TABLE usage_adjustments (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    adjustment_id CHAR(26) NOT NULL COMMENT '用量调整记录标识',
    original_call_id VARCHAR(64) NOT NULL COMMENT '被更正的原始模型调用标识',
    reason VARCHAR(512) NOT NULL COMMENT '用量更正原因',
    delta_tokens BIGINT NOT NULL COMMENT 'Token 调整值，可为负数',
    delta_cost_amount DECIMAL(20,8) NOT NULL COMMENT '费用调整值，可为负数',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '调整记录创建时间',
    PRIMARY KEY (platform_id, tenant_id, adjustment_id),
    KEY idx_usage_adjustment_original (platform_id, tenant_id, original_call_id),
    CONSTRAINT fk_usage_adjustment_original FOREIGN KEY (platform_id, tenant_id, original_call_id)
        REFERENCES usage_ledger(platform_id, tenant_id, call_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='追加式用量更正记录，不覆盖原始用量';

ALTER TABLE content_upload_intents
    ADD COLUMN original_filename VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传文件原始名称，仅用于展示',
    ADD COLUMN mime_type VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream' COMMENT '上传内容的声明媒体类型',
    ADD COLUMN retention_expires_at TIMESTAMP(6) NULL COMMENT '上传对象计划自动过期时间',
    ADD COLUMN expected_size_bytes BIGINT UNSIGNED NULL COMMENT '上传意图声明的对象字节数',
    ADD COLUMN expected_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '上传意图声明的对象 SHA-256',
    ADD COLUMN retention_policy VARCHAR(32) NOT NULL DEFAULT 'conversation' COMMENT '上传对象的保留策略',
    ADD COLUMN encryption_key_version VARCHAR(64) NULL COMMENT '上传对象加密密钥版本引用',
    DROP CHECK ck_content_upload_object_type,
    ADD CONSTRAINT ck_content_upload_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE', 'MEDIA_ASSET', 'RAW_PAYLOAD')
    ),
    ADD CONSTRAINT ck_content_upload_retention CHECK (
        retention_policy IN ('ephemeral', 'session', 'conversation', 'knowledge_base')
    ),
    COMMENT='租户范围内有租约的对象上传意图';

ALTER TABLE content_gc_queue
    DROP CHECK ck_content_gc_object_type,
    ADD CONSTRAINT ck_content_gc_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE', 'MEDIA_ASSET', 'RAW_PAYLOAD')
    ),
    COMMENT='可重试的对象删除和孤儿回收任务';

INSERT INTO schema_version (version) VALUES (3);
