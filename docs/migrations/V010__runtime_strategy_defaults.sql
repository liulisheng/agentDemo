-- 作用域回答策略默认值；与 EngineProfile、ModelRoute 独立保存。
-- 目标版本：MySQL 8.4 / InnoDB / utf8mb4。
-- 只允许显式 migration runner 执行；不改写历史 migration。

CREATE TABLE runtime_strategy_bindings (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    binding_id CHAR(26) NOT NULL COMMENT '回答策略默认绑定稳定标识',
    scope_type VARCHAR(16) NOT NULL COMMENT '默认作用域：user、tenant 或 platform',
    scope_id CHAR(26) NOT NULL COMMENT '作用域标识；user 为用户、tenant 为租户、platform 为平台',
    runtime_options_json JSON NOT NULL COMMENT '回答策略白名单 JSON；不得包含凭据、Prompt 或任意环境变量',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '绑定状态：ACTIVE 或 DISABLED',
    created_by CHAR(26) NULL COMMENT '创建或最近修改该绑定的用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '绑定创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '绑定更新时间',
    PRIMARY KEY (platform_id, tenant_id, binding_id),
    UNIQUE KEY uk_runtime_strategy_scope (platform_id, tenant_id, scope_type, scope_id),
    KEY idx_runtime_strategy_status (platform_id, tenant_id, status, scope_type),
    CONSTRAINT fk_runtime_strategy_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_runtime_strategy_creator FOREIGN KEY (platform_id, tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_runtime_strategy_scope CHECK (scope_type IN ('user', 'tenant', 'platform')),
    CONSTRAINT ck_runtime_strategy_status CHECK (status IN ('ACTIVE', 'DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='用户、租户和平台作用域的回答策略默认绑定';

INSERT INTO schema_version (version) VALUES (10);
