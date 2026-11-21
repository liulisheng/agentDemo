CREATE TABLE mcp_server_profiles (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    profile_id CHAR(26) NOT NULL COMMENT 'MCP Server Profile 稳定标识',
    version INT UNSIGNED NOT NULL COMMENT 'MCP Server Profile 不可变版本号',
    name VARCHAR(128) NOT NULL COMMENT 'MCP Server Profile 管理名称',
    transport VARCHAR(16) NOT NULL COMMENT 'MCP 传输类型，首期只允许 stdio',
    command VARCHAR(512) NOT NULL COMMENT 'Stdio Server 可执行程序路径或受控注册名',
    args_json JSON NOT NULL COMMENT 'Stdio Server 命令参数数组，不进行 shell 展开',
    working_directory_ref VARCHAR(512) NOT NULL COMMENT 'Stdio Server 授权工作目录的 opaque 引用',
    environment_secret_refs_json JSON NOT NULL COMMENT '环境变量名到 SecretRef 的映射，不保存密钥值',
    enabled_tool_names_json JSON NOT NULL COMMENT '此 Profile 启用的远端 MCP 工具名称数组',
    tool_catalog_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'MCP 工具名和输入 Schema 目录的 SHA-256',
    startup_timeout_ms INT UNSIGNED NOT NULL DEFAULT 10000 COMMENT 'Stdio Server 启动和工具发现超时毫秒数',
    tool_timeout_ms INT UNSIGNED NOT NULL DEFAULT 60000 COMMENT '单次 MCP 工具调用超时毫秒数',
    status VARCHAR(32) NOT NULL COMMENT 'Profile 版本状态，仅允许 DRAFT、ACTIVE、DISABLED',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT 'Profile 版本创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'Profile 版本更新时间',
    PRIMARY KEY (platform_id, tenant_id, profile_id, version),
    KEY idx_mcp_profile_catalog (platform_id, tenant_id, status, name),
    CONSTRAINT fk_mcp_profile_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT ck_mcp_profile_transport CHECK (transport = 'stdio'),
    CONSTRAINT ck_mcp_profile_status CHECK (status IN ('DRAFT', 'ACTIVE', 'DISABLED')),
    CONSTRAINT ck_mcp_profile_version CHECK (version > 0),
    CONSTRAINT ck_mcp_profile_timeouts CHECK (
        startup_timeout_ms BETWEEN 1 AND 300000
        AND tool_timeout_ms BETWEEN 1 AND 600000
    ),
    CONSTRAINT ck_mcp_profile_args_array CHECK (JSON_TYPE(args_json) = 'ARRAY'),
    CONSTRAINT ck_mcp_profile_env_object CHECK (JSON_TYPE(environment_secret_refs_json) = 'OBJECT'),
    CONSTRAINT ck_mcp_profile_tools_array CHECK (JSON_TYPE(enabled_tool_names_json) = 'ARRAY')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='租户范围内不可变的本机 MCP Stdio Server 配置';

ALTER TABLE tool_versions
    ADD COLUMN mcp_server_profile_id CHAR(26) NULL COMMENT 'MCP Tool 所属 Server Profile 标识',
    ADD COLUMN mcp_server_profile_version INT UNSIGNED NULL COMMENT 'MCP Tool 固定的 Server Profile 版本',
    ADD COLUMN remote_tool_name VARCHAR(128) NULL COMMENT 'MCP Server tools/list 返回的精确工具名称',
    ADD COLUMN input_schema_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '远端 MCP 输入 JSON Schema 的 SHA-256',
    ADD COLUMN preview_tool_name VARCHAR(128) NULL COMMENT '此写工具对应的只读预览工具名称',
    ADD COLUMN mcp_risk_level VARCHAR(16) NOT NULL DEFAULT 'READ' COMMENT 'MCP 工具风险等级，仅允许 READ、PREVIEW、WRITE',
    ADD COLUMN idempotency_support BOOLEAN NOT NULL DEFAULT FALSE COMMENT '远端 MCP 写工具是否支持稳定幂等键',
    ADD KEY idx_tool_mcp_profile (platform_id, tenant_id, mcp_server_profile_id, mcp_server_profile_version, remote_tool_name),
    ADD CONSTRAINT fk_tool_mcp_profile FOREIGN KEY (platform_id, tenant_id, mcp_server_profile_id, mcp_server_profile_version)
        REFERENCES mcp_server_profiles(platform_id, tenant_id, profile_id, version),
    ADD CONSTRAINT ck_tool_mcp_profile_binding CHECK (
        (mcp_server_profile_id IS NULL AND mcp_server_profile_version IS NULL
            AND remote_tool_name IS NULL AND input_schema_sha256 IS NULL)
        OR
        (protocol = 'mcp' AND mcp_server_profile_id IS NOT NULL
            AND mcp_server_profile_version IS NOT NULL AND remote_tool_name IS NOT NULL
            AND input_schema_sha256 IS NOT NULL)
    ),
    ADD CONSTRAINT ck_tool_mcp_risk_level CHECK (mcp_risk_level IN ('READ', 'PREVIEW', 'WRITE')),
    COMMENT='租户范围内不可变的 Tool 版本及 MCP 调用配置';

ALTER TABLE runs
    ADD COLUMN submitted_by_user_id CHAR(26) NULL COMMENT '创建该 Run 的已认证租户用户标识；历史 Run 可为空',
    ADD KEY idx_run_session_latest (platform_id, tenant_id, session_id, created_at, run_id),
    ADD KEY idx_run_platform_claim (platform_id, state, created_at, run_id),
    ADD CONSTRAINT fk_run_submitter FOREIGN KEY (platform_id, tenant_id, submitted_by_user_id)
        REFERENCES users(platform_id, tenant_id, user_id),
    DROP CHECK ck_run_state,
    ADD CONSTRAINT ck_run_state CHECK (
        state IN ('QUEUED', 'STARTING', 'RUNNING', 'AWAITING_APPROVAL', 'CANCELLING',
                  'SUCCEEDED', 'FAILED', 'CANCELLED', 'UNKNOWN')
    ),
    COMMENT='Run 状态、幂等、租约、平台执行快照和审批等待状态';

CREATE TABLE tool_approval_requests (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    tenant_id CHAR(26) NOT NULL COMMENT '所属租户标识',
    approval_id CHAR(26) NOT NULL COMMENT '一次性 Tool Approval 标识',
    run_id CHAR(26) NOT NULL COMMENT '等待或执行该批准动作的 Run 标识',
    session_id CHAR(26) NOT NULL COMMENT '审批所属 Platform Session 标识',
    conversation_id VARCHAR(64) NOT NULL COMMENT '审批所在用户可见 Conversation 标识',
    requested_by CHAR(26) NOT NULL COMMENT '提交该 Run 且唯一有权批准本次写操作的用户标识',
    approved_by CHAR(26) NULL COMMENT '实际批准用户标识，必须与 requested_by 相同',
    tool_invocation_id VARCHAR(64) NOT NULL COMMENT '对应 Trace Tool Span 的标识',
    tool_id CHAR(26) NOT NULL COMMENT '本次一次性写操作的 Tool 稳定标识',
    tool_version INT UNSIGNED NOT NULL COMMENT '本次一次性写操作的不可变 Tool 版本号',
    mcp_server_profile_id CHAR(26) NOT NULL COMMENT '执行本次写操作的 MCP Server Profile 标识',
    mcp_server_profile_version INT UNSIGNED NOT NULL COMMENT '执行本次写操作的 MCP Server Profile 版本',
    arguments_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '加密工具参数对象的 opaque 引用',
    arguments_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '工具参数 canonical JSON 的 SHA-256',
    preview_ref VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '加密用户预览对象的 opaque 引用',
    preview_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '用户可见预览内容的 SHA-256',
    confirmation_idempotency_key VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT 'Run 提交者确认该审批时使用的幂等键',
    state VARCHAR(32) NOT NULL COMMENT '审批及一次性动作状态：PENDING、APPROVED、REJECTED、EXPIRED、RUNNING、SUCCEEDED、FAILED、UNKNOWN',
    active_run_id CHAR(26) GENERATED ALWAYS AS (
        CASE WHEN state IN ('PENDING', 'APPROVED', 'RUNNING') THEN run_id ELSE NULL END
    ) STORED COMMENT '同一 Run 的活动审批唯一键，终态时为空',
    expires_at TIMESTAMP(6) NOT NULL COMMENT '用户确认此预览的有效截止时间',
    worker_id VARCHAR(128) NULL COMMENT '当前持有批准动作租约的 Worker 标识',
    fencing_token BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '批准动作 Worker 单调递增 fencing token',
    lease_expires_at TIMESTAMP(6) NULL COMMENT '批准动作 Worker 租约截止时间',
    approved_at TIMESTAMP(6) NULL COMMENT 'Run 提交者批准本次预览的时间',
    rejected_by CHAR(26) NULL COMMENT 'Run 提交者拒绝本次预览的用户标识',
    consumed_at TIMESTAMP(6) NULL COMMENT 'Worker 原子消费一次性批准授权的时间',
    finished_at TIMESTAMP(6) NULL COMMENT '批准动作进入终态的时间',
    error_code VARCHAR(128) NULL COMMENT '批准动作稳定错误类别，不保存敏感错误正文',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '审批请求创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '审批请求更新时间',
    PRIMARY KEY (platform_id, tenant_id, approval_id),
    UNIQUE KEY uk_tool_approval_invocation (platform_id, tenant_id, tool_invocation_id),
    UNIQUE KEY uk_tool_approval_active_run (platform_id, tenant_id, active_run_id),
    UNIQUE KEY uk_tool_approval_confirmation_idempotency (platform_id, tenant_id, confirmation_idempotency_key),
    KEY idx_tool_approval_claim (platform_id, tenant_id, state, created_at, approval_id),
    KEY idx_tool_approval_lease (platform_id, tenant_id, state, lease_expires_at),
    KEY idx_tool_approval_run (platform_id, tenant_id, run_id, created_at),
    KEY idx_tool_approval_conversation (platform_id, tenant_id, conversation_id, state),
    CONSTRAINT fk_tool_approval_tenant FOREIGN KEY (platform_id, tenant_id)
        REFERENCES tenants(platform_id, tenant_id),
    CONSTRAINT fk_tool_approval_run FOREIGN KEY (platform_id, tenant_id, run_id)
        REFERENCES runs(platform_id, tenant_id, run_id),
    CONSTRAINT fk_tool_approval_session FOREIGN KEY (platform_id, tenant_id, session_id)
        REFERENCES sessions(platform_id, tenant_id, session_id),
    CONSTRAINT fk_tool_approval_conversation FOREIGN KEY (platform_id, tenant_id, conversation_id)
        REFERENCES conversations(platform_id, tenant_id, conversation_id),
    CONSTRAINT fk_tool_approval_requester FOREIGN KEY (platform_id, tenant_id, requested_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_tool_approval_approver FOREIGN KEY (platform_id, tenant_id, approved_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_tool_approval_rejecter FOREIGN KEY (platform_id, tenant_id, rejected_by)
        REFERENCES users(platform_id, tenant_id, user_id),
    CONSTRAINT fk_tool_approval_invocation FOREIGN KEY (platform_id, tenant_id, tool_invocation_id)
        REFERENCES platform_trace_spans(platform_id, tenant_id, span_id),
    CONSTRAINT fk_tool_approval_version FOREIGN KEY (platform_id, tenant_id, tool_id, tool_version)
        REFERENCES tool_versions(platform_id, tenant_id, tool_id, version),
    CONSTRAINT fk_tool_approval_profile FOREIGN KEY (platform_id, tenant_id, mcp_server_profile_id, mcp_server_profile_version)
        REFERENCES mcp_server_profiles(platform_id, tenant_id, profile_id, version),
    CONSTRAINT ck_tool_approval_state CHECK (
        state IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')
    ),
    CONSTRAINT ck_tool_approval_submitter_only CHECK (approved_by IS NULL OR approved_by = requested_by),
    CONSTRAINT ck_tool_approval_rejecter_only CHECK (rejected_by IS NULL OR rejected_by = requested_by),
    CONSTRAINT ck_tool_approval_single_decision CHECK (approved_by IS NULL OR rejected_by IS NULL),
    CONSTRAINT ck_tool_approval_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='绑定 Run 提交者、精确预览和单次 MCP 写操作的审批及动作队列';

INSERT INTO schema_version (version) VALUES (6);
