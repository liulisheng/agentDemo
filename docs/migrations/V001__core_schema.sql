-- Harness control-plane core schema
-- Target: MySQL 8.4 / InnoDB / utf8mb4
-- Apply only after review in a disposable database.

CREATE TABLE schema_version (
    version INT NOT NULL,
    applied_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO schema_version (version) VALUES (1);

CREATE TABLE platforms (
    platform_id CHAR(26) NOT NULL,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id),
    UNIQUE KEY uk_platform_code (code),
    CONSTRAINT ck_platform_status CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE tenants (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id),
    UNIQUE KEY uk_tenant_code (platform_id, code),
    CONSTRAINT fk_tenant_platform FOREIGN KEY (platform_id) REFERENCES platforms(platform_id),
    CONSTRAINT ck_tenant_status CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    user_id CHAR(26) NOT NULL,
    external_subject VARCHAR(255) NOT NULL,
    username VARCHAR(128) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id, user_id),
    UNIQUE KEY uk_user_subject (platform_id, external_subject),
    UNIQUE KEY uk_user_name (platform_id, tenant_id, username),
    CONSTRAINT fk_user_tenant FOREIGN KEY (platform_id, tenant_id) REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_user_status CHECK (status IN ('PENDING', 'ACTIVE', 'LOCKED', 'DISABLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE sessions (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    session_id CHAR(26) NOT NULL,
    user_id CHAR(26) NOT NULL,
    engine_name VARCHAR(64) NOT NULL,
    engine_session_id VARCHAR(255) NOT NULL,
    harness_home_ref VARCHAR(512) NULL,
    workspace_ref VARCHAR(512) NULL,
    status VARCHAR(32) NOT NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    last_run_at TIMESTAMP(6) NULL,
    PRIMARY KEY (platform_id, tenant_id, session_id),
    UNIQUE KEY uk_engine_session (platform_id, tenant_id, engine_name, engine_session_id),
    KEY idx_session_list (platform_id, tenant_id, status, updated_at),
    KEY idx_session_page (platform_id, tenant_id, updated_at, session_id),
    CONSTRAINT fk_session_user FOREIGN KEY (platform_id, tenant_id, user_id) REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT ck_session_status CHECK (status IN ('ACTIVE', 'PAUSED', 'INTERRUPTED', 'CLOSED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE runs (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    run_id CHAR(26) NOT NULL,
    session_id CHAR(26) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    request_digest CHAR(64) NOT NULL,
    request_digest_version SMALLINT NOT NULL DEFAULT 1,
    state VARCHAR(32) NOT NULL,
    worker_id VARCHAR(128) NULL,
    fencing_token BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMP(6) NULL,
    started_at TIMESTAMP(6) NULL,
    finished_at TIMESTAMP(6) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    error_code VARCHAR(128) NULL,
    result_ref VARCHAR(512) NULL,
    PRIMARY KEY (platform_id, tenant_id, run_id),
    UNIQUE KEY uk_run_idempotency (platform_id, tenant_id, idempotency_key),
    KEY idx_run_claim (platform_id, tenant_id, state, created_at, run_id),
    KEY idx_run_lease (platform_id, tenant_id, state, lease_expires_at),
    KEY idx_run_page (platform_id, tenant_id, updated_at, run_id),
    CONSTRAINT fk_run_session FOREIGN KEY (platform_id, tenant_id, session_id) REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT ck_run_state CHECK (state IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCELLING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE audit_events (
    platform_id CHAR(26) NOT NULL,
    tenant_id CHAR(26) NOT NULL,
    event_id CHAR(26) NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_user_id CHAR(26) NULL,
    session_id CHAR(26) NULL,
    run_id CHAR(26) NULL,
    event_type VARCHAR(128) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NULL,
    resource_id CHAR(26) NULL,
    fencing_token BIGINT NULL,
    payload_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (platform_id, tenant_id, event_id),
    KEY idx_audit_resource (platform_id, tenant_id, resource_type, resource_id, created_at),
    KEY idx_audit_session (platform_id, tenant_id, session_id, created_at),
    KEY idx_audit_run (platform_id, tenant_id, run_id, created_at),
    CONSTRAINT fk_audit_tenant FOREIGN KEY (platform_id, tenant_id) REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_audit_actor FOREIGN KEY (platform_id, tenant_id, actor_user_id) REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_audit_session FOREIGN KEY (platform_id, tenant_id, session_id) REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT fk_audit_run FOREIGN KEY (platform_id, tenant_id, run_id) REFERENCES runs(platform_id, tenant_id, run_id),
    CONSTRAINT ck_audit_actor_type CHECK (actor_type IN ('USER', 'WORKER', 'SYSTEM'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
