CREATE TABLE runtime_worker_heartbeats (
    platform_id CHAR(26) NOT NULL COMMENT '所属平台标识',
    worker_id VARCHAR(128) NOT NULL COMMENT '运行时 Worker 的进程标识',
    engine_name VARCHAR(64) NOT NULL COMMENT '该 Worker 接收的固定 EngineProvider 标识',
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'Worker 状态：ACTIVE 或 STOPPING',
    heartbeat_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '最近一次 Worker 存活心跳时间',
    PRIMARY KEY (platform_id, worker_id, engine_name),
    KEY idx_worker_engine_heartbeat (platform_id, engine_name, heartbeat_at),
    CONSTRAINT fk_worker_heartbeat_platform FOREIGN KEY (platform_id)
        REFERENCES platforms(platform_id),
    CONSTRAINT ck_worker_heartbeat_status CHECK (status IN ('ACTIVE', 'STOPPING'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
COMMENT='运行时 Worker 按引擎上报的短期存活心跳';

INSERT INTO schema_version (version) VALUES (14);
