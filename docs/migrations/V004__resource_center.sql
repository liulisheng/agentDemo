-- 平台资源中心：只保存平台级资源目录与租户授权映射，不改变租户资源外键。
-- 目标版本：MySQL 8.4 / InnoDB / utf8mb4。
-- 必须通过显式 migration runner 执行；本迁移新增表和字段均提供中文 COMMENT。

CREATE TABLE hub_resources (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    hub_resource_id CHAR(26) NOT NULL COMMENT '资源中心资源稳定标识',
    resource_type VARCHAR(32) NOT NULL COMMENT '资源类型：数字员工、Skill 或 Tool',
    publisher_tenant_id CHAR(26) NOT NULL COMMENT '发布该资源的租户标识',
    publisher_user_id CHAR(26) NOT NULL COMMENT '发布该资源的用户标识',
    name VARCHAR(128) NOT NULL COMMENT '资源中心展示名称',
    description VARCHAR(2048) NOT NULL COMMENT '资源中心展示用途说明',
    visibility VARCHAR(24) NOT NULL DEFAULT 'PLATFORM' COMMENT '目录可见范围：平台可发现或仅授权租户可发现',
    status VARCHAR(24) NOT NULL DEFAULT 'DRAFT' COMMENT '资源中心资源状态',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '资源中心条目创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '资源中心条目更新时间',
    PRIMARY KEY (platform_id, hub_resource_id),
    UNIQUE KEY uk_hub_resource_publisher (platform_id, hub_resource_id, publisher_tenant_id),
    KEY idx_hub_discovery (platform_id, visibility, status, resource_type, updated_at, hub_resource_id),
    KEY idx_hub_publisher (platform_id, publisher_tenant_id, status, updated_at, hub_resource_id),
    CONSTRAINT fk_hub_resource_publisher_tenant FOREIGN KEY (platform_id, publisher_tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_hub_resource_publisher_user FOREIGN KEY (platform_id, publisher_tenant_id, publisher_user_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_hub_resource_type CHECK (resource_type IN ('DIGITAL_EMPLOYEE', 'SKILL', 'TOOL')),
    CONSTRAINT ck_hub_resource_visibility CHECK (visibility IN ('PLATFORM', 'RESTRICTED')),
    CONSTRAINT ck_hub_resource_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED', 'REVOKED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='平台级可发现资源目录；资源包读取仍需租户 Grant';

CREATE TABLE hub_resource_versions (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    hub_resource_id CHAR(26) NOT NULL COMMENT '资源中心资源标识',
    version INT UNSIGNED NOT NULL COMMENT '资源中心不可变版本号',
    publisher_tenant_id CHAR(26) NOT NULL COMMENT '该版本源资源所属租户标识',
    source_resource_type VARCHAR(32) NOT NULL COMMENT '源租户资源类型',
    source_resource_id CHAR(26) NOT NULL COMMENT '源租户资源标识',
    source_resource_version INT UNSIGNED NOT NULL COMMENT '源租户资源固定版本号',
    manifest_json JSON NOT NULL COMMENT '规范化资源清单与依赖快照',
    manifest_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '资源清单 SHA-256 摘要',
    artifact_asset_id VARCHAR(64) NULL COMMENT '资源包文件对应的发布租户 MediaAsset 标识',
    status VARCHAR(24) NOT NULL DEFAULT 'DRAFT' COMMENT '资源中心版本状态',
    created_by CHAR(26) NOT NULL COMMENT '创建该资源版本的发布租户用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '资源中心版本创建时间',
    published_at TIMESTAMP(6) NULL COMMENT '资源中心版本发布时间',
    PRIMARY KEY (platform_id, hub_resource_id, version),
    UNIQUE KEY uk_hub_version_publisher (platform_id, hub_resource_id, version, publisher_tenant_id),
    KEY idx_hub_version_status (platform_id, hub_resource_id, status, version),
    KEY idx_hub_source_resource (platform_id, publisher_tenant_id, source_resource_type, source_resource_id, source_resource_version),
    CONSTRAINT fk_hub_version_resource FOREIGN KEY (platform_id, hub_resource_id, publisher_tenant_id)
        REFERENCES hub_resources(platform_id, hub_resource_id, publisher_tenant_id),
    CONSTRAINT fk_hub_version_publisher_tenant FOREIGN KEY (platform_id, publisher_tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_hub_version_publisher_user FOREIGN KEY (platform_id, publisher_tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_hub_version_artifact_asset FOREIGN KEY (platform_id, publisher_tenant_id, artifact_asset_id)
        REFERENCES media_assets(platform_id, tenant_id, asset_id),
    CONSTRAINT ck_hub_source_type CHECK (source_resource_type IN ('DIGITAL_EMPLOYEE', 'SKILL', 'TOOL')),
    CONSTRAINT ck_hub_version_status CHECK (status IN ('DRAFT', 'PUBLISHED', 'DEPRECATED', 'REVOKED')),
    CONSTRAINT ck_hub_manifest_hash CHECK (LENGTH(manifest_sha256) = 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='资源中心不可变版本、依赖清单摘要与包对象引用';

CREATE TABLE hub_resource_dependencies (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    hub_resource_id CHAR(26) NOT NULL COMMENT '包含依赖的资源标识',
    hub_version INT UNSIGNED NOT NULL COMMENT '包含依赖的资源版本号',
    dependency_id CHAR(26) NOT NULL COMMENT '资源依赖条目标识',
    dependency_type VARCHAR(32) NOT NULL COMMENT '依赖资源类型',
    dependency_hub_resource_id CHAR(26) NOT NULL COMMENT '被依赖的资源中心标识',
    dependency_hub_version INT UNSIGNED NOT NULL COMMENT '被依赖的不可变资源版本号',
    required BOOLEAN NOT NULL DEFAULT TRUE COMMENT '该依赖是否为导入和发布的必需项',
    sequence_no INT UNSIGNED NOT NULL COMMENT '依赖在资源包中的稳定顺序',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '依赖快照创建时间',
    PRIMARY KEY (platform_id, hub_resource_id, hub_version, dependency_id),
    UNIQUE KEY uk_hub_dependency_order (platform_id, hub_resource_id, hub_version, sequence_no),
    KEY idx_hub_dependency_target (platform_id, dependency_hub_resource_id, dependency_hub_version),
    CONSTRAINT fk_hub_dependency_source_version FOREIGN KEY (platform_id, hub_resource_id, hub_version)
        REFERENCES hub_resource_versions(platform_id, hub_resource_id, version),
    CONSTRAINT fk_hub_dependency_target_version FOREIGN KEY (platform_id, dependency_hub_resource_id, dependency_hub_version)
        REFERENCES hub_resource_versions(platform_id, hub_resource_id, version),
    CONSTRAINT ck_hub_dependency_type CHECK (dependency_type IN ('DIGITAL_EMPLOYEE', 'SKILL', 'TOOL'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='资源中心版本固定的 Hub 依赖关系清单';

CREATE TABLE hub_resource_grants (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    grant_id CHAR(26) NOT NULL COMMENT '资源中心租户授权记录标识',
    hub_resource_id CHAR(26) NOT NULL COMMENT '被授权的资源中心资源标识',
    hub_version INT UNSIGNED NOT NULL COMMENT '被授权的精确资源版本号',
    publisher_tenant_id CHAR(26) NOT NULL COMMENT '发布资源的源租户标识',
    recipient_tenant_id CHAR(26) NOT NULL COMMENT '获得导入授权的接收租户标识',
    copy_allowed BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否允许复制到接收租户',
    reference_allowed BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否允许在接收租户只读引用',
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE' COMMENT '授权状态',
    expires_at TIMESTAMP(6) NULL COMMENT '授权失效时间；空表示不按时间自动过期',
    revoked_at TIMESTAMP(6) NULL COMMENT '授权撤销时间',
    created_by CHAR(26) NOT NULL COMMENT '执行授权操作的发布租户用户标识',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '授权创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '授权状态更新时间',
    PRIMARY KEY (platform_id, grant_id),
    KEY idx_hub_grant_recipient (platform_id, recipient_tenant_id, status, hub_resource_id, hub_version),
    KEY idx_hub_grant_source (platform_id, publisher_tenant_id, hub_resource_id, hub_version, status),
    CONSTRAINT fk_hub_grant_version FOREIGN KEY (platform_id, hub_resource_id, hub_version, publisher_tenant_id)
        REFERENCES hub_resource_versions(platform_id, hub_resource_id, version, publisher_tenant_id),
    CONSTRAINT fk_hub_grant_publisher_tenant FOREIGN KEY (platform_id, publisher_tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_hub_grant_recipient_tenant FOREIGN KEY (platform_id, recipient_tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_hub_grant_creator FOREIGN KEY (platform_id, publisher_tenant_id, created_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_hub_grant_mode CHECK (copy_allowed OR reference_allowed),
    CONSTRAINT ck_hub_grant_status CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='按接收租户与精确版本授权 COPY 或 REFERENCE 的审计记录';

CREATE TABLE tenant_resource_imports (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '接收导入资源的租户标识',
    import_id CHAR(26) NOT NULL COMMENT '租户资源导入记录标识',
    grant_id CHAR(26) NOT NULL COMMENT '导入时使用的资源授权记录标识',
    hub_resource_id CHAR(26) NOT NULL COMMENT '来源资源中心资源标识',
    hub_version INT UNSIGNED NOT NULL COMMENT '来源资源中心精确版本号',
    mode VARCHAR(16) NOT NULL COMMENT '导入模式：COPY 租户副本或 REFERENCE 只读代理',
    local_resource_type VARCHAR(32) NOT NULL COMMENT '接收租户本地代理资源类型',
    local_resource_id CHAR(26) NOT NULL COMMENT '接收租户本地代理资源标识',
    local_resource_version INT UNSIGNED NOT NULL COMMENT '接收租户本地代理资源版本号',
    manifest_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '导入时固定的来源清单摘要',
    status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE' COMMENT '租户导入记录状态',
    imported_by CHAR(26) NOT NULL COMMENT '发起导入的接收租户用户标识',
    imported_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '导入完成时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '导入状态更新时间',
    PRIMARY KEY (platform_id, tenant_id, import_id),
    UNIQUE KEY uk_tenant_hub_import_mode (platform_id, tenant_id, hub_resource_id, hub_version, mode),
    UNIQUE KEY uk_tenant_import_local_version (platform_id, tenant_id, local_resource_type, local_resource_id, local_resource_version),
    KEY idx_tenant_import_list (platform_id, tenant_id, status, imported_at, import_id),
    KEY idx_tenant_import_source (platform_id, hub_resource_id, hub_version),
    CONSTRAINT fk_tenant_import_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_tenant_import_grant FOREIGN KEY (platform_id, grant_id)
        REFERENCES hub_resource_grants(platform_id, grant_id),
    CONSTRAINT fk_tenant_import_hub_version FOREIGN KEY (platform_id, hub_resource_id, hub_version)
        REFERENCES hub_resource_versions(platform_id, hub_resource_id, version),
    CONSTRAINT fk_tenant_import_user FOREIGN KEY (platform_id, tenant_id, imported_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_tenant_import_mode CHECK (mode IN ('COPY', 'REFERENCE')),
    CONSTRAINT ck_tenant_import_resource_type CHECK (local_resource_type IN ('DIGITAL_EMPLOYEE', 'SKILL', 'TOOL')),
    CONSTRAINT ck_tenant_import_status CHECK (status IN ('IMPORTING', 'ACTIVE', 'OUTDATED', 'REVOKED', 'FAILED')),
    CONSTRAINT ck_tenant_import_manifest_hash CHECK (LENGTH(manifest_sha256) = 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='接收租户 COPY 副本和 REFERENCE 代理的来源及版本映射';

ALTER TABLE tool_versions
    ADD COLUMN requires_recipient_credentials BOOLEAN NOT NULL DEFAULT FALSE COMMENT 'COPY 工具后接收租户是否必须绑定自己的凭据 SecretRef';

INSERT INTO schema_version (version) VALUES (4);
