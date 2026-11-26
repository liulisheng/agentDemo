-- Agent Team Orchestration forward migration。
-- 只允许显式 migration runner 执行；历史 migration 不改写。

CREATE TABLE teams (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    team_id CHAR(26) NOT NULL COMMENT '团队稳定标识',
    name VARCHAR(255) NOT NULL COMMENT '团队名称',
    status VARCHAR(16) NOT NULL DEFAULT 'DRAFT' COMMENT '团队状态：DRAFT、PUBLISHED 或 ARCHIVED',
    current_version INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '当前发布团队版本号',
    created_by CHAR(26) NULL COMMENT '创建团队的用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '团队创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '团队更新时间',
    PRIMARY KEY (platform_id, tenant_id, team_id),
    KEY idx_team_status (platform_id, tenant_id, status, updated_at),
    CONSTRAINT fk_team_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_team_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_team_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='租户范围内的 Agent 团队稳定定义';

CREATE TABLE team_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    team_id CHAR(26) NOT NULL COMMENT '团队稳定标识',
    version INT UNSIGNED NOT NULL COMMENT '团队不可变配置版本号',
    name VARCHAR(255) NOT NULL COMMENT '该版本的团队名称快照',
    router_member_id CHAR(26) NULL COMMENT '负责拆分任务的路由成员标识',
    finalizer_member_id CHAR(26) NULL COMMENT '负责汇总答复的成员标识',
    orchestration_mode VARCHAR(16) NOT NULL DEFAULT 'HYBRID' COMMENT '编排模式：USER、ROUTER 或 HYBRID',
    approval_mode VARCHAR(16) NOT NULL DEFAULT 'CONFIRM' COMMENT '计划审批模式：CONFIRM、AUTO 或 MANUAL',
    policy_json JSON NOT NULL COMMENT '并发、预算、重试、取消和交付策略快照',
    status VARCHAR(16) NOT NULL DEFAULT 'DRAFT' COMMENT '版本状态：DRAFT、PUBLISHED 或 ARCHIVED',
    created_by CHAR(26) NULL COMMENT '创建该版本的用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '版本创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '版本发布时间',
    PRIMARY KEY (platform_id, tenant_id, team_id, version),
    KEY idx_team_version_status (platform_id, tenant_id, team_id, status, version),
    CONSTRAINT fk_team_version_team FOREIGN KEY (platform_id, tenant_id, team_id)
        REFERENCES teams(platform_id, tenant_id, team_id),
    CONSTRAINT fk_team_version_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_team_version_mode CHECK (orchestration_mode IN ('USER', 'ROUTER', 'HYBRID')),
    CONSTRAINT ck_team_version_approval CHECK (approval_mode IN ('CONFIRM', 'AUTO', 'MANUAL')),
    CONSTRAINT ck_team_version_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='Agent 团队不可变编排与交付策略版本';

CREATE TABLE team_members (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    team_id CHAR(26) NOT NULL COMMENT '团队稳定标识',
    team_version INT UNSIGNED NOT NULL COMMENT '所属团队版本号',
    member_id CHAR(26) NOT NULL COMMENT '团队成员稳定标识',
    employee_id CHAR(26) NOT NULL COMMENT '数字员工标识',
    employee_version INT UNSIGNED NOT NULL COMMENT '数字员工版本号',
    deployment_id CHAR(26) NOT NULL COMMENT '成员运行部署标识',
    role VARCHAR(16) NOT NULL DEFAULT 'MEMBER' COMMENT '团队角色：MEMBER、ROUTER 或 FINALIZER',
    engine_name VARCHAR(64) NOT NULL COMMENT '固定 EngineProvider 标识',
    model_route_id VARCHAR(64) NULL COMMENT '固定 ModelRoute 标识',
    capabilities_snapshot_json JSON NOT NULL COMMENT '成员可用引擎能力快照',
    max_concurrency INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '成员最大并发任务数',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '成员状态：ACTIVE、DISABLED 或 REMOVED',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '成员创建时间',
    PRIMARY KEY (platform_id, tenant_id, team_id, team_version, member_id),
    KEY idx_team_member_employee (platform_id, tenant_id, employee_id, employee_version),
    KEY idx_team_member_status (platform_id, tenant_id, team_id, team_version, status),
    CONSTRAINT fk_team_member_version FOREIGN KEY (platform_id, tenant_id, team_id, team_version)
        REFERENCES team_versions(platform_id, tenant_id, team_id, version),
    CONSTRAINT fk_team_member_employee FOREIGN KEY (platform_id, tenant_id, employee_id, employee_version)
        REFERENCES digital_employee_versions(platform_id, tenant_id, employee_id, version),
    CONSTRAINT fk_team_member_deployment FOREIGN KEY (platform_id, tenant_id, deployment_id)
        REFERENCES deployments(platform_id, tenant_id, deployment_id),
    CONSTRAINT fk_team_member_route FOREIGN KEY (platform_id, tenant_id, model_route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT ck_team_member_role CHECK (role IN ('MEMBER', 'ROUTER', 'FINALIZER')),
    CONSTRAINT ck_team_member_status CHECK (status IN ('ACTIVE', 'DISABLED', 'REMOVED')),
    CONSTRAINT ck_team_member_concurrency CHECK (max_concurrency > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='团队版本中的数字员工成员、引擎和能力快照';

ALTER TABLE team_versions
    ADD CONSTRAINT fk_team_version_router FOREIGN KEY (platform_id, tenant_id, team_id, version, router_member_id)
        REFERENCES team_members(platform_id, tenant_id, team_id, team_version, member_id),
    ADD CONSTRAINT fk_team_version_finalizer FOREIGN KEY (platform_id, tenant_id, team_id, version, finalizer_member_id)
        REFERENCES team_members(platform_id, tenant_id, team_id, team_version, member_id);

ALTER TABLE conversations
    ADD COLUMN conversation_mode VARCHAR(16) NOT NULL DEFAULT 'SINGLE_AGENT' COMMENT 'Conversation 执行模式：SINGLE_AGENT 或 TEAM',
    ADD KEY idx_conversation_mode (platform_id, tenant_id, conversation_mode, status, updated_at),
    ADD CONSTRAINT ck_conversation_mode CHECK (conversation_mode IN ('SINGLE_AGENT', 'TEAM')),
    COMMENT='用户可见的多消息对话容器及单员工/团队执行模式';

CREATE TABLE conversation_team_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT 'Conversation 标识',
    team_id CHAR(26) NOT NULL COMMENT '固定团队标识',
    team_version INT UNSIGNED NOT NULL COMMENT '固定团队版本号',
    orchestration_mode VARCHAR(16) NOT NULL COMMENT 'Conversation 使用的编排模式',
    approval_mode VARCHAR(16) NOT NULL COMMENT 'Conversation 使用的计划审批模式',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '团队绑定创建时间',
    PRIMARY KEY (platform_id, tenant_id, conversation_id),
    KEY idx_conversation_team_version (platform_id, tenant_id, team_id, team_version),
    CONSTRAINT fk_conversation_team_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_conversation_team_version FOREIGN KEY (platform_id, tenant_id, team_id, team_version)
        REFERENCES team_versions(platform_id, tenant_id, team_id, version),
    CONSTRAINT ck_conversation_team_mode CHECK (orchestration_mode IN ('USER', 'ROUTER', 'HYBRID')),
    CONSTRAINT ck_conversation_team_approval CHECK (approval_mode IN ('CONFIRM', 'AUTO', 'MANUAL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='Conversation 与已发布 Agent 团队版本的固定绑定';

CREATE TABLE orchestration_runs (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    orchestration_id CHAR(26) NOT NULL COMMENT '父级编排运行标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT '所属 Conversation 标识',
    input_message_id CHAR(26) NULL COMMENT '触发编排的用户消息标识',
    team_id CHAR(26) NOT NULL COMMENT '固定团队标识',
    team_version INT UNSIGNED NOT NULL COMMENT '固定团队版本号',
    plan_revision INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '当前任务计划修订号',
    router_run_id CHAR(26) NULL COMMENT 'Router Agent 子 Run 标识',
    finalizer_run_id CHAR(26) NULL COMMENT '汇总 Agent 子 Run 标识',
    status VARCHAR(32) NOT NULL COMMENT '编排聚合状态',
    approval_mode VARCHAR(16) NOT NULL COMMENT '计划审批模式',
    idempotency_key VARCHAR(255) NOT NULL COMMENT '用户请求幂等键',
    counters_json JSON NOT NULL COMMENT '任务数量、完成数、失败数和取消数快照',
    created_by CHAR(26) NOT NULL COMMENT '创建编排的用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '编排创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '编排更新时间',
    PRIMARY KEY (platform_id, tenant_id, orchestration_id),
    UNIQUE KEY uk_orchestration_idempotency (platform_id, tenant_id, conversation_id, idempotency_key),
    KEY idx_orchestration_status (platform_id, tenant_id, status, updated_at),
    CONSTRAINT fk_orchestration_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_orchestration_team FOREIGN KEY (platform_id, tenant_id, team_id, team_version)
        REFERENCES team_versions(platform_id, tenant_id, team_id, version),
    CONSTRAINT fk_orchestration_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_orchestration_approval CHECK (approval_mode IN ('CONFIRM', 'AUTO', 'MANUAL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='团队一次用户请求的父级编排运行和聚合状态';

CREATE TABLE orchestration_tasks (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    task_id CHAR(26) NOT NULL COMMENT '任务节点稳定标识',
    orchestration_id CHAR(26) NOT NULL COMMENT '所属父级编排标识',
    client_key VARCHAR(128) NOT NULL COMMENT '计划内任务稳定键',
    title VARCHAR(255) NOT NULL COMMENT '任务名称',
    instruction_ref VARCHAR(512) NOT NULL COMMENT '任务指令对象引用',
    parent_task_id CHAR(26) NULL COMMENT '父任务标识',
    required_flag BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否为编排必需任务',
    required_capabilities_json JSON NOT NULL COMMENT '任务所需引擎能力列表',
    preferred_engine_name VARCHAR(64) NULL COMMENT '任务偏好的 EngineProvider',
    preferred_member_id CHAR(26) NULL COMMENT '用户或 Router 偏好的成员',
    assigned_member_id CHAR(26) NULL COMMENT '当前实际分配成员',
    assignment_source VARCHAR(16) NULL COMMENT '分配来源：USER、ROUTER 或 SCHEDULER',
    status VARCHAR(32) NOT NULL COMMENT '任务状态',
    attempt INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '当前执行尝试次数',
    max_attempts INT UNSIGNED NOT NULL DEFAULT 3 COMMENT '允许的最大尝试次数',
    output_ref VARCHAR(512) NULL COMMENT '任务输出摘要或对象引用',
    error_code VARCHAR(128) NULL COMMENT '最近一次稳定错误类别',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '任务创建时间',
    started_at TIMESTAMP(6) NULL COMMENT '任务开始执行时间',
    finished_at TIMESTAMP(6) NULL COMMENT '任务终态时间',
    PRIMARY KEY (platform_id, tenant_id, task_id),
    UNIQUE KEY uk_orchestration_task_client (platform_id, tenant_id, orchestration_id, client_key),
    KEY idx_orchestration_task_queue (platform_id, tenant_id, orchestration_id, status, created_at),
    CONSTRAINT fk_orchestration_task_run FOREIGN KEY (platform_id, tenant_id, orchestration_id)
        REFERENCES orchestration_runs(platform_id, tenant_id, orchestration_id),
    CONSTRAINT ck_orchestration_task_assignment CHECK (assignment_source IS NULL OR assignment_source IN ('USER', 'ROUTER', 'SCHEDULER'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='团队编排任务节点、依赖前置状态和成员分配';

CREATE TABLE orchestration_task_dependencies (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    task_id CHAR(26) NOT NULL COMMENT '下游任务标识',
    depends_on_task_id CHAR(26) NOT NULL COMMENT '上游依赖任务标识',
    dependency_condition VARCHAR(32) NOT NULL DEFAULT 'SUCCEEDED' COMMENT '依赖满足条件',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '依赖创建时间',
    PRIMARY KEY (platform_id, tenant_id, task_id, depends_on_task_id),
    CONSTRAINT fk_task_dependency_task FOREIGN KEY (platform_id, tenant_id, task_id)
        REFERENCES orchestration_tasks(platform_id, tenant_id, task_id),
    CONSTRAINT fk_task_dependency_parent FOREIGN KEY (platform_id, tenant_id, depends_on_task_id)
        REFERENCES orchestration_tasks(platform_id, tenant_id, task_id),
    CONSTRAINT ck_task_dependency_condition CHECK (dependency_condition IN ('SUCCEEDED', 'TERMINAL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='任务 DAG 的租户范围依赖边';

CREATE TABLE orchestration_task_attempts (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    attempt_id CHAR(26) NOT NULL COMMENT '任务尝试标识',
    task_id CHAR(26) NOT NULL COMMENT '所属任务标识',
    attempt_no INT UNSIGNED NOT NULL COMMENT '任务尝试序号',
    member_id CHAR(26) NULL COMMENT '实际执行团队成员标识',
    session_id CHAR(26) NULL COMMENT '成员独立 Session 标识',
    run_id CHAR(26) NULL COMMENT '实际执行子 Run 标识',
    engine_name VARCHAR(64) NULL COMMENT '本次尝试使用的 EngineProvider',
    model_route_id VARCHAR(64) NULL COMMENT '本次尝试使用的 ModelRoute',
    worker_pool VARCHAR(128) NULL COMMENT '领取该尝试的 Worker Pool',
    status VARCHAR(32) NOT NULL COMMENT '尝试状态',
    idempotency_key VARCHAR(255) NOT NULL COMMENT '尝试幂等键',
    error_code VARCHAR(128) NULL COMMENT '尝试错误类别',
    started_at TIMESTAMP(6) NULL COMMENT '尝试开始时间',
    finished_at TIMESTAMP(6) NULL COMMENT '尝试结束时间',
    PRIMARY KEY (platform_id, tenant_id, attempt_id),
    UNIQUE KEY uk_task_attempt_idempotency (platform_id, tenant_id, task_id, idempotency_key),
    UNIQUE KEY uk_task_attempt_no (platform_id, tenant_id, task_id, attempt_no),
    CONSTRAINT fk_task_attempt_task FOREIGN KEY (platform_id, tenant_id, task_id)
        REFERENCES orchestration_tasks(platform_id, tenant_id, task_id),
    CONSTRAINT fk_task_attempt_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='任务每次执行尝试及其成员、引擎和租约来源';

CREATE TABLE orchestration_task_events (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    event_id CHAR(26) NOT NULL COMMENT '任务事件标识',
    orchestration_id CHAR(26) NOT NULL COMMENT '所属编排标识',
    task_id CHAR(26) NULL COMMENT '关联任务标识',
    attempt_id CHAR(26) NULL COMMENT '关联尝试标识',
    actor_type VARCHAR(16) NOT NULL COMMENT '事件操作者类型：USER、AGENT、WORKER 或 SYSTEM',
    actor_id CHAR(26) NULL COMMENT '事件操作者标识',
    event_type VARCHAR(64) NOT NULL COMMENT '任务或编排事件类型',
    payload_json JSON NOT NULL COMMENT '脱敏事件数据',
    sequence_no BIGINT UNSIGNED NOT NULL COMMENT '租户编排事件顺序号',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '事件创建时间',
    PRIMARY KEY (platform_id, tenant_id, event_id),
    UNIQUE KEY uk_orchestration_event_sequence (platform_id, tenant_id, orchestration_id, sequence_no),
    KEY idx_task_event_page (platform_id, tenant_id, orchestration_id, sequence_no),
    CONSTRAINT fk_task_event_orchestration FOREIGN KEY (platform_id, tenant_id, orchestration_id)
        REFERENCES orchestration_runs(platform_id, tenant_id, orchestration_id),
    CONSTRAINT fk_task_event_task FOREIGN KEY (platform_id, tenant_id, task_id)
        REFERENCES orchestration_tasks(platform_id, tenant_id, task_id),
    CONSTRAINT ck_task_event_actor CHECK (actor_type IN ('USER', 'AGENT', 'WORKER', 'SYSTEM'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='团队编排任务、Worker 和产出物的追加事件审计';

CREATE TABLE execution_artifacts (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    artifact_id CHAR(26) NOT NULL COMMENT '执行产出物稳定标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT '产出物所属 Conversation',
    orchestration_id CHAR(26) NULL COMMENT '产出物所属编排标识',
    task_id CHAR(26) NULL COMMENT '产出物来源任务标识',
    run_id CHAR(26) NULL COMMENT '产出物来源 Run 标识',
    attempt_no INT UNSIGNED NULL COMMENT '产出物来源尝试序号',
    media_asset_id VARCHAR(64) NULL COMMENT '底层 MediaAsset 标识',
    object_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '底层对象不透明引用',
    filename VARCHAR(255) NOT NULL COMMENT '产出物文件名',
    mime_type VARCHAR(128) NOT NULL COMMENT '产出物内容类型',
    size_bytes BIGINT UNSIGNED NOT NULL COMMENT '产出物字节数',
    content_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '产出物 SHA-256 摘要',
    source_engine VARCHAR(64) NULL COMMENT '产出物来源 EngineProvider',
    source_model VARCHAR(128) NULL COMMENT '产出物来源模型摘要',
    visibility VARCHAR(16) NOT NULL DEFAULT 'TEAM' COMMENT '可见范围：INTERNAL、TEAM、USER 或 EXTERNAL',
    retention_policy VARCHAR(32) NOT NULL DEFAULT 'conversation' COMMENT '产出物保留策略',
    status VARCHAR(16) NOT NULL DEFAULT 'CREATED' COMMENT '产出物状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '产出物登记时间',
    expires_at TIMESTAMP(6) NULL COMMENT '产出物计划过期时间',
    PRIMARY KEY (platform_id, tenant_id, artifact_id),
    UNIQUE KEY uk_execution_artifact_object (platform_id, tenant_id, object_ref),
    KEY idx_execution_artifact_task (platform_id, tenant_id, task_id, created_at),
    KEY idx_execution_artifact_status (platform_id, tenant_id, status, created_at),
    CONSTRAINT fk_execution_artifact_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_execution_artifact_asset FOREIGN KEY (platform_id, tenant_id, media_asset_id)
        REFERENCES media_assets(platform_id, tenant_id, asset_id),
    CONSTRAINT ck_execution_artifact_visibility CHECK (visibility IN ('INTERNAL', 'TEAM', 'USER', 'EXTERNAL')),
    CONSTRAINT ck_execution_artifact_status CHECK (status IN ('CREATED', 'UPLOADING', 'SCANNING', 'READY', 'DELIVERING', 'DELIVERED', 'FAILED', 'EXPIRED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='团队和单员工 Runtime 执行产出物索引及租户可见性';

CREATE TABLE artifact_links (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    artifact_id CHAR(26) NOT NULL COMMENT '当前产出物标识',
    parent_artifact_id CHAR(26) NOT NULL COMMENT '来源或组合产出物标识',
    relation_type VARCHAR(32) NOT NULL COMMENT '产出物关系：INPUT、OUTPUT 或 BUNDLE_MEMBER',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '产出物关系创建时间',
    PRIMARY KEY (platform_id, tenant_id, artifact_id, parent_artifact_id, relation_type),
    CONSTRAINT fk_artifact_link_artifact FOREIGN KEY (platform_id, tenant_id, artifact_id)
        REFERENCES execution_artifacts(platform_id, tenant_id, artifact_id),
    CONSTRAINT fk_artifact_link_parent FOREIGN KEY (platform_id, tenant_id, parent_artifact_id)
        REFERENCES execution_artifacts(platform_id, tenant_id, artifact_id),
    CONSTRAINT ck_artifact_link_relation CHECK (relation_type IN ('INPUT', 'OUTPUT', 'BUNDLE_MEMBER'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='执行产出物之间的输入、输出和打包关系';

CREATE TABLE artifact_deliveries (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    delivery_id CHAR(26) NOT NULL COMMENT '产出物交付稳定标识',
    artifact_id CHAR(26) NOT NULL COMMENT '被交付产出物标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT '交付所属 Conversation',
    channel_name VARCHAR(64) NOT NULL COMMENT '目标渠道名称',
    idempotency_key VARCHAR(255) NOT NULL COMMENT '交付幂等键',
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT '交付状态：PENDING、DELIVERING、DELIVERED 或 FAILED',
    external_message_id VARCHAR(255) NULL COMMENT '外部渠道消息标识',
    retry_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '交付重试次数',
    last_error_code VARCHAR(128) NULL COMMENT '最近一次交付错误类别',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '交付创建时间',
    delivered_at TIMESTAMP(6) NULL COMMENT '交付完成时间',
    PRIMARY KEY (platform_id, tenant_id, delivery_id),
    UNIQUE KEY uk_artifact_delivery_idempotency (platform_id, tenant_id, artifact_id, channel_name, idempotency_key),
    KEY idx_artifact_delivery_status (platform_id, tenant_id, status, created_at),
    CONSTRAINT fk_artifact_delivery_artifact FOREIGN KEY (platform_id, tenant_id, artifact_id)
        REFERENCES execution_artifacts(platform_id, tenant_id, artifact_id),
    CONSTRAINT fk_artifact_delivery_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT ck_artifact_delivery_status CHECK (status IN ('PENDING', 'DELIVERING', 'DELIVERED', 'FAILED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='产出物面向 Web 或外部渠道的幂等交付记录';

ALTER TABLE agent_bindings
    ADD COLUMN team_member_id CHAR(26) NULL COMMENT '对应 TeamVersion 的成员标识',
    ADD COLUMN team_member_role VARCHAR(16) NULL COMMENT '团队成员角色快照',
    ADD COLUMN capabilities_snapshot_json JSON NULL COMMENT 'Conversation 创建时的能力快照',
    ADD KEY idx_agent_binding_team_member (platform_id, tenant_id, team_member_id),
    ADD CONSTRAINT ck_agent_binding_team_role CHECK (team_member_role IS NULL OR team_member_role IN ('MEMBER', 'ROUTER', 'FINALIZER')),
    COMMENT='Conversation 中员工与独立 Session 的绑定及团队角色快照';

ALTER TABLE runs
    ADD COLUMN orchestration_id CHAR(26) NULL COMMENT '所属父级编排运行标识',
    ADD COLUMN task_id CHAR(26) NULL COMMENT '所属编排任务标识',
    ADD COLUMN parent_run_id CHAR(26) NULL COMMENT '父级 Run 标识',
    ADD COLUMN attempt_no INT UNSIGNED NULL COMMENT '任务执行尝试序号',
    ADD KEY idx_run_orchestration (platform_id, tenant_id, orchestration_id, created_at),
    ADD KEY idx_run_task (platform_id, tenant_id, task_id, created_at),
    ADD CONSTRAINT fk_run_orchestration FOREIGN KEY (platform_id, tenant_id, orchestration_id)
        REFERENCES orchestration_runs(platform_id, tenant_id, orchestration_id),
    ADD CONSTRAINT fk_run_task FOREIGN KEY (platform_id, tenant_id, task_id)
        REFERENCES orchestration_tasks(platform_id, tenant_id, task_id),
    ADD CONSTRAINT fk_run_parent_run FOREIGN KEY (platform_id, tenant_id, parent_run_id)
        REFERENCES runs(platform_id, tenant_id, run_id),
    COMMENT='Run 状态、幂等、租约、平台执行快照和编排父子关联';

INSERT INTO schema_version (version) VALUES (11);
