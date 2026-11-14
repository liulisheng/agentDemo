-- Conversation message metadata and stream checkpoint references.
-- Target: MySQL 8.4 / InnoDB / utf8mb4.
-- Apply only after V001 using the deployment's migration process; never apply automatically at application startup.

ALTER TABLE sessions
    ADD COLUMN last_message_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0;

CREATE TABLE messages (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    message_id CHAR(26) NOT NULL,
    session_id CHAR(26) NOT NULL,
    run_id CHAR(26) NULL,
    sequence_no BIGINT UNSIGNED NOT NULL,
    role VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    content_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NULL,
    content_sha256 CHAR(64) NULL,
    byte_length BIGINT UNSIGNED NULL,
    content_type VARCHAR(128) NULL,
    last_committed_chunk_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    completed_at TIMESTAMP(6) NULL,
    PRIMARY KEY (platform_id, tenant_id, message_id),
    UNIQUE KEY uk_message_sequence (platform_id, tenant_id, session_id, sequence_no),
    UNIQUE KEY uk_message_content_ref (platform_id, tenant_id, content_ref),
    KEY idx_message_run (platform_id, tenant_id, run_id, sequence_no),
    KEY idx_message_page (platform_id, tenant_id, session_id, created_at, message_id),
    CONSTRAINT fk_message_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT fk_message_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id),
    CONSTRAINT ck_message_role CHECK (role IN ('USER', 'ASSISTANT')),
    CONSTRAINT ck_message_status CHECK (status IN ('PENDING', 'STREAMING', 'COMPLETE', 'PARTIAL', 'FAILED')),
    CONSTRAINT ck_message_sequence CHECK (sequence_no > 0),
    CONSTRAINT ck_message_content_ref CHECK (content_ref IS NULL OR LENGTH(TRIM(content_ref)) > 0),
    CONSTRAINT ck_message_content_sha256 CHECK (content_sha256 IS NULL OR LENGTH(content_sha256) = 64),
    CONSTRAINT ck_message_content_metadata CHECK (
        (content_ref IS NULL AND content_sha256 IS NULL AND byte_length IS NULL AND content_type IS NULL)
        OR
        (content_ref IS NOT NULL AND content_sha256 IS NOT NULL AND byte_length IS NOT NULL AND content_type IS NOT NULL)
    ),
    CONSTRAINT ck_message_terminal_content CHECK (
        status NOT IN ('COMPLETE', 'PARTIAL') OR content_ref IS NOT NULL
    ),
    CONSTRAINT ck_user_message_complete CHECK (role <> 'USER' OR status = 'COMPLETE')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE message_chunks (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    message_id CHAR(26) NOT NULL,
    chunk_sequence BIGINT UNSIGNED NOT NULL,
    content_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    byte_length BIGINT UNSIGNED NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id, message_id, chunk_sequence),
    UNIQUE KEY uk_message_chunk_content_ref (platform_id, tenant_id, content_ref),
    CONSTRAINT fk_message_chunk_message FOREIGN KEY (platform_id, tenant_id, message_id)
        REFERENCES messages(platform_id, tenant_id, message_id) ON DELETE CASCADE,
    CONSTRAINT ck_message_chunk_sequence CHECK (chunk_sequence > 0),
    CONSTRAINT ck_message_chunk_content_ref CHECK (LENGTH(TRIM(content_ref)) > 0),
    CONSTRAINT ck_message_chunk_content_sha256 CHECK (LENGTH(content_sha256) = 64),
    CONSTRAINT ck_message_chunk_byte_length CHECK (byte_length > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Optional source IDs intentionally have no FK so cleanup survives source-row deletion.
CREATE TABLE content_upload_intents (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    upload_id CHAR(26) NOT NULL,
    session_id CHAR(26) NULL,
    message_id CHAR(26) NULL,
    run_id CHAR(26) NULL,
    object_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_type VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'UPLOADING',
    lease_expires_at TIMESTAMP(6) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id, upload_id),
    UNIQUE KEY uk_content_upload_object_ref (platform_id, tenant_id, object_ref),
    KEY idx_content_upload_expiry (platform_id, tenant_id, status, lease_expires_at),
    KEY idx_content_upload_session (platform_id, tenant_id, session_id, status),
    CONSTRAINT fk_content_upload_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_content_upload_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE')
    ),
    CONSTRAINT ck_content_upload_status CHECK (status IN ('UPLOADING', 'PUBLISHED', 'EXPIRED')),
    CONSTRAINT ck_content_upload_object_ref CHECK (LENGTH(TRIM(object_ref)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE content_gc_queue (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    cleanup_id CHAR(26) NOT NULL,
    object_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_type VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    last_error_code VARCHAR(128) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id, cleanup_id),
    UNIQUE KEY uk_content_gc_object_ref (platform_id, tenant_id, object_ref),
    KEY idx_content_gc_claim (platform_id, tenant_id, status, next_attempt_at, created_at),
    CONSTRAINT fk_content_gc_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_content_gc_object_type CHECK (
        object_type IN ('MESSAGE_CONTENT', 'STREAM_CHUNK', 'MANIFEST', 'HARNESS_HOME', 'WORKSPACE')
    ),
    CONSTRAINT ck_content_gc_status CHECK (status IN ('QUEUED', 'DELETING', 'RETRY', 'DELETED')),
    CONSTRAINT ck_content_gc_object_ref CHECK (LENGTH(TRIM(object_ref)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO schema_version (version) VALUES (2);
