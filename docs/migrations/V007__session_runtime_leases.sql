CREATE TABLE session_runtime_leases (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    session_id CHAR(26) NOT NULL COMMENT '被独占访问的 Harness Session 标识',
    run_id CHAR(26) NOT NULL COMMENT '当前访问该 Session Home 的 Run 标识',
    worker_id VARCHAR(128) NULL COMMENT 'ACTIVE lease 的 Worker 标识；隔离后为空',
    fencing_token BIGINT UNSIGNED NOT NULL COMMENT '与当前 Run claim 相同的单调递增 fencing token',
    lease_expires_at TIMESTAMP(6) NULL COMMENT 'ACTIVE lease 到期时间；QUARANTINED 状态为空',
    state VARCHAR(16) NOT NULL COMMENT 'Session Home 租约状态：ACTIVE 或 QUARANTINED',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Session Home 租约创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Session Home 租约更新时间',
    PRIMARY KEY (platform_id, tenant_id, session_id),
    UNIQUE KEY uk_session_runtime_lease_run (platform_id, tenant_id, run_id),
    KEY idx_session_runtime_lease_expiry (platform_id, state, lease_expires_at),
    CONSTRAINT fk_session_runtime_lease_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT fk_session_runtime_lease_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id),
    CONSTRAINT ck_session_runtime_lease_state CHECK (state IN ('ACTIVE', 'QUARANTINED')),
    CONSTRAINT ck_session_runtime_lease_owner CHECK (
        (state = 'ACTIVE' AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state = 'QUARANTINED' AND worker_id IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT ck_session_runtime_lease_fencing CHECK (fencing_token > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='串行保护租户 Session Home 的独立 Harness Run 租约';

INSERT INTO session_runtime_leases (
    platform_id, tenant_id, session_id, run_id, worker_id, fencing_token,
    lease_expires_at, state
)
SELECT r.platform_id, r.tenant_id, r.session_id, r.run_id, r.worker_id,
       r.fencing_token, r.lease_expires_at, 'ACTIVE'
FROM runs r
JOIN sessions s ON s.platform_id=r.platform_id AND s.tenant_id=r.tenant_id
    AND s.session_id=r.session_id
WHERE r.state IN ('STARTING','RUNNING','CANCELLING')
  AND r.worker_id IS NOT NULL
  AND r.lease_expires_at IS NOT NULL
  AND s.status='ACTIVE';

INSERT INTO schema_version (version) VALUES (7);
