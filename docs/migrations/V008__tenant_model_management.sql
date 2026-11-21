CREATE TABLE model_connections (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    connection_id VARCHAR(64) NOT NULL COMMENT '租户范围内稳定的模型连接标识',
    name VARCHAR(128) NOT NULL COMMENT '租户管理页面展示的连接名称',
    provider_kind VARCHAR(64) NOT NULL COMMENT '模型调用适配器类型',
    protocol VARCHAR(64) NOT NULL COMMENT '模型接口协议类型',
    endpoint VARCHAR(1024) NOT NULL COMMENT '模型服务 Base URL，不包含凭据',
    auth_secret_ref VARCHAR(255) NULL COMMENT '凭据 Secret 引用，不保存密钥明文',
    auth_secret_scope VARCHAR(64) NULL COMMENT '模型凭据 Secret 的授权作用域',
    auth_secret_provider VARCHAR(64) NULL COMMENT '模型凭据 Secret 的解析 Provider',
    catalog_mode VARCHAR(16) NOT NULL DEFAULT 'REMOTE' COMMENT '模型目录来源：REMOTE 或 MANUAL',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '连接状态：ACTIVE 或 DISABLED',
    version INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '连接配置单调递增版本',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '连接创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '连接最近更新时间',
    PRIMARY KEY (platform_id, tenant_id, connection_id),
    UNIQUE KEY uk_model_connection_name (platform_id, tenant_id, name),
    KEY idx_model_connection_status (platform_id, tenant_id, status, name),
    CONSTRAINT fk_model_connection_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_model_connection_catalog_mode CHECK (catalog_mode IN ('REMOTE', 'MANUAL')),
    CONSTRAINT ck_model_connection_status CHECK (status IN ('ACTIVE', 'DISABLED')),
    CONSTRAINT ck_model_connection_version CHECK (version > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内的模型服务连接和凭据引用';

CREATE TABLE model_catalog_items (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    connection_id VARCHAR(64) NOT NULL COMMENT '提供该模型的租户连接标识',
    model_id VARCHAR(255) NOT NULL COMMENT '远端接口接受的模型 ID',
    display_name VARCHAR(255) NOT NULL COMMENT '模型目录展示名称',
    capabilities_json JSON NOT NULL COMMENT '模型能力元数据，不包含凭据或用户授权结果',
    context_window INT UNSIGNED NULL COMMENT '模型声明的最大上下文 Token 数，未知时为空',
    catalog_source VARCHAR(16) NOT NULL COMMENT '模型目录来源：REMOTE、MANUAL 或 BACKFILL',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '目录项状态：ACTIVE 或 DISABLED',
    last_seen_at TIMESTAMP(6) NULL COMMENT '最近一次从远端目录发现的时间，手工录入时为空',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '目录项创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '目录项最近更新时间',
    PRIMARY KEY (platform_id, tenant_id, connection_id, model_id),
    KEY idx_model_catalog_tenant_status (platform_id, tenant_id, status, display_name),
    CONSTRAINT fk_model_catalog_connection FOREIGN KEY (platform_id, tenant_id, connection_id)
        REFERENCES model_connections(platform_id, tenant_id, connection_id),
    CONSTRAINT ck_model_catalog_source CHECK (catalog_source IN ('REMOTE', 'MANUAL', 'BACKFILL')),
    CONSTRAINT ck_model_catalog_status CHECK (status IN ('ACTIVE', 'DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户模型连接提供的模型目录项';

ALTER TABLE model_routes
    ADD COLUMN model_connection_id VARCHAR(64) NULL COMMENT '模型路由引用的租户连接标识',
    ADD COLUMN catalog_model_id VARCHAR(255) NULL COMMENT '模型路由绑定的外部模型 ID',
    ADD COLUMN route_version INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '模型路由单调递增配置版本',
    ADD KEY idx_model_route_catalog (platform_id, tenant_id, model_connection_id, catalog_model_id);

CREATE TEMPORARY TABLE tmp_model_route_connection_backfill AS
SELECT
    platform_id,
    tenant_id,
    route_id,
    provider,
    model_name,
    endpoint,
    protocol,
    auth_secret_ref,
    auth_secret_scope,
    auth_secret_provider,
    CONCAT(
        'conn-',
        LEFT(
            SHA2(
                CONCAT_WS(
                    CHAR(31),
                    BINARY platform_id,
                    BINARY tenant_id,
                    BINARY provider,
                    BINARY protocol,
                    BINARY endpoint,
                    COALESCE(BINARY auth_secret_ref, _binary''),
                    COALESCE(BINARY auth_secret_scope, _binary''),
                    COALESCE(BINARY auth_secret_provider, _binary'')
                ),
                256
            ),
            32
        )
    ) AS model_connection_id
FROM model_routes;

INSERT INTO model_connections (
    platform_id, tenant_id, connection_id, name, provider_kind, protocol, endpoint,
    auth_secret_ref, auth_secret_scope, auth_secret_provider, catalog_mode, status, version
)
SELECT DISTINCT
    platform_id,
    tenant_id,
    model_connection_id,
    CONCAT(provider, '-', SUBSTRING(model_connection_id, 6, 12)),
    provider,
    protocol,
    endpoint,
    auth_secret_ref,
    auth_secret_scope,
    auth_secret_provider,
    'MANUAL',
    'ACTIVE',
    1
FROM tmp_model_route_connection_backfill;

INSERT INTO model_catalog_items (
    platform_id, tenant_id, connection_id, model_id, display_name, capabilities_json,
    context_window, catalog_source, status, last_seen_at
)
SELECT DISTINCT
    platform_id,
    tenant_id,
    model_connection_id,
    model_name,
    model_name,
    JSON_OBJECT(),
    NULL,
    'BACKFILL',
    'ACTIVE',
    NULL
FROM tmp_model_route_connection_backfill;

UPDATE model_routes AS routes
JOIN tmp_model_route_connection_backfill AS backfill
    ON backfill.platform_id = routes.platform_id
    AND backfill.tenant_id = routes.tenant_id
    AND backfill.route_id = routes.route_id
SET routes.model_connection_id = backfill.model_connection_id,
    routes.catalog_model_id = backfill.model_name,
    routes.route_version = 1;

DROP TEMPORARY TABLE tmp_model_route_connection_backfill;

ALTER TABLE model_routes
    MODIFY COLUMN model_connection_id VARCHAR(64) NOT NULL COMMENT '模型路由引用的租户连接标识',
    MODIFY COLUMN catalog_model_id VARCHAR(255) NOT NULL COMMENT '模型路由绑定的外部模型 ID',
    ADD CONSTRAINT fk_model_route_connection FOREIGN KEY (platform_id, tenant_id, model_connection_id)
        REFERENCES model_connections(platform_id, tenant_id, connection_id),
    ADD CONSTRAINT fk_model_route_catalog_model FOREIGN KEY (platform_id, tenant_id, model_connection_id, catalog_model_id)
        REFERENCES model_catalog_items(platform_id, tenant_id, connection_id, model_id),
    ADD CONSTRAINT ck_model_route_version CHECK (route_version > 0);

CREATE TABLE model_test_records (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    test_id CHAR(26) NOT NULL COMMENT '租户范围内的连接或模型测试标识',
    connection_id VARCHAR(64) NOT NULL COMMENT '被测试的租户模型连接标识',
    route_id VARCHAR(64) NULL COMMENT '模型测试对应的 ModelRoute；连接测试时为空',
    model_id VARCHAR(255) NULL COMMENT '被测试的外部模型 ID；连接测试时为空',
    test_kind VARCHAR(16) NOT NULL COMMENT '测试类型：CONNECTION 或 MODEL',
    state VARCHAR(16) NOT NULL COMMENT '测试状态：QUEUED、RUNNING、SUCCEEDED、FAILED 或 UNKNOWN',
    snapshot_json JSON NOT NULL COMMENT '本次测试使用的非敏感连接/模型快照及 SecretRef',
    created_by_user_id VARCHAR(64) NOT NULL COMMENT '发起测试的租户用户标识',
    worker_id VARCHAR(128) NULL COMMENT '当前处理该测试任务的 Worker 标识',
    fencing_token BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '测试任务租约的单调递增 fencing token',
    lease_expires_at TIMESTAMP(6) NULL COMMENT 'RUNNING 测试任务的租约到期时间',
    started_at TIMESTAMP(6) NULL COMMENT '测试开始时间',
    finished_at TIMESTAMP(6) NULL COMMENT '测试结束时间',
    latency_ms BIGINT UNSIGNED NULL COMMENT '模型测试耗时毫秒数',
    provider_request_id VARCHAR(255) NULL COMMENT '上游模型请求引用，不包含请求凭据',
    input_tokens INT UNSIGNED NULL COMMENT '模型返回或估算的输入 Token 数',
    output_tokens INT UNSIGNED NULL COMMENT '模型返回或估算的输出 Token 数',
    total_tokens INT UNSIGNED NULL COMMENT '模型返回或估算的总 Token 数',
    usage_status VARCHAR(16) NULL COMMENT '用量精度：EXACT、ESTIMATED 或 UNAVAILABLE',
    error_category VARCHAR(64) NULL COMMENT '脱敏后的稳定测试错误类别',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '测试任务创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '测试任务最近更新时间',
    PRIMARY KEY (platform_id, tenant_id, test_id),
    KEY idx_model_test_claim (platform_id, state, created_at, test_id),
    KEY idx_model_test_connection (platform_id, tenant_id, connection_id, created_at),
    CONSTRAINT fk_model_test_connection FOREIGN KEY (platform_id, tenant_id, connection_id)
        REFERENCES model_connections(platform_id, tenant_id, connection_id),
    CONSTRAINT fk_model_test_route FOREIGN KEY (platform_id, tenant_id, route_id)
        REFERENCES model_routes(platform_id, tenant_id, route_id),
    CONSTRAINT fk_model_test_catalog_model FOREIGN KEY (platform_id, tenant_id, connection_id, model_id)
        REFERENCES model_catalog_items(platform_id, tenant_id, connection_id, model_id),
    CONSTRAINT ck_model_test_kind CHECK (test_kind IN ('CONNECTION', 'MODEL')),
    CONSTRAINT ck_model_test_state CHECK (state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')),
    CONSTRAINT ck_model_test_usage_status CHECK (usage_status IS NULL OR usage_status IN ('EXACT', 'ESTIMATED', 'UNAVAILABLE')),
    CONSTRAINT ck_model_test_fencing CHECK (state <> 'RUNNING' OR fencing_token > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户模型连接与模型可用性测试任务及脱敏结果';

INSERT INTO schema_version (version) VALUES (8);
