ALTER TABLE skill_versions
    ADD COLUMN content_format VARCHAR(16) NOT NULL DEFAULT 'INLINE' COMMENT 'Skill 内容模式：INLINE 指令或 FILE_PACKAGE 文件包',
    ADD COLUMN entry_path VARCHAR(512) NULL COMMENT 'FILE_PACKAGE 的唯一入口相对路径；必须是根目录 SKILL.md',
    ADD COLUMN file_manifest_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT 'Skill 文件路径与摘要清单的规范化 SHA-256';

ALTER TABLE skill_versions
    ADD CONSTRAINT ck_skill_content_format CHECK (content_format IN ('INLINE', 'FILE_PACKAGE')),
    ADD CONSTRAINT ck_skill_file_package_metadata CHECK (
        (content_format = 'INLINE' AND entry_path IS NULL AND file_manifest_sha256 IS NULL)
        OR
        (content_format = 'FILE_PACKAGE' AND entry_path = 'SKILL.md' AND file_manifest_sha256 IS NOT NULL)
    );

CREATE TABLE skill_file_entries (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    skill_id CHAR(26) NOT NULL COMMENT '文件所属 Skill 稳定标识',
    version INT UNSIGNED NOT NULL COMMENT '文件所属 Skill 不可变版本号',
    file_path VARCHAR(512) NOT NULL COMMENT '包内规范化相对路径；路径分隔符统一为正斜杠',
    media_asset_id VARCHAR(64) NOT NULL COMMENT '文件字节在当前租户 MediaAsset 中的标识',
    mime_type VARCHAR(128) NOT NULL COMMENT '文件内容类型，仅用于展示和下载响应',
    size_bytes BIGINT UNSIGNED NOT NULL COMMENT '解压后单个文件的字节数',
    content_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '解压后文件内容的 SHA-256 摘要',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Skill 文件清单记录创建时间',
    PRIMARY KEY (platform_id, tenant_id, skill_id, version, file_path),
    KEY idx_skill_file_asset (platform_id, tenant_id, media_asset_id),
    CONSTRAINT fk_skill_file_version FOREIGN KEY (platform_id, tenant_id, skill_id, version)
        REFERENCES skill_versions(platform_id, tenant_id, skill_id, version),
    CONSTRAINT fk_skill_file_asset FOREIGN KEY (platform_id, tenant_id, media_asset_id)
        REFERENCES media_assets(platform_id, tenant_id, asset_id),
    CONSTRAINT ck_skill_file_path_nonempty CHECK (LENGTH(file_path) > 0),
    CONSTRAINT ck_skill_file_digest_length CHECK (LENGTH(content_sha256) = 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='FILE_PACKAGE Skill 的租户文件路径与媒体对象清单';

INSERT INTO schema_version (version) VALUES (5);
