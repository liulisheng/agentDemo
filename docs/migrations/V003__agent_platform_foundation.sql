-- Agent Platform foundation control-plane metadata.
-- 迁移由部署流程执行，应用启动不自动修改 schema。
CREATE TABLE digital_employees (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  employee_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  PRIMARY KEY (platform_id, tenant_id, employee_id)
) ENGINE=InnoDB;

CREATE TABLE digital_employee_versions (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  employee_id VARCHAR(64) NOT NULL,
  version INT NOT NULL,
  status VARCHAR(32) NOT NULL,
  execution_profile_id VARCHAR(64) NOT NULL,
  model_route_id VARCHAR(64) NULL,
  workspace_profile_id VARCHAR(64) NULL,
  sandbox_profile_id VARCHAR(64) NULL,
  PRIMARY KEY (platform_id, tenant_id, employee_id, version),
  CONSTRAINT fk_employee_version_employee FOREIGN KEY (platform_id, tenant_id, employee_id)
    REFERENCES digital_employees(platform_id, tenant_id, employee_id)
) ENGINE=InnoDB;

CREATE TABLE deployments (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  deployment_id VARCHAR(64) NOT NULL,
  employee_id VARCHAR(64) NOT NULL,
  employee_version INT NOT NULL,
  status VARCHAR(32) NOT NULL,
  PRIMARY KEY (platform_id, tenant_id, deployment_id)
) ENGINE=InnoDB;

CREATE TABLE model_routes (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  route_id VARCHAR(64) NOT NULL,
  alias VARCHAR(128) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  PRIMARY KEY (platform_id, tenant_id, route_id)
) ENGINE=InnoDB;

CREATE TABLE platform_trace_spans (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  run_id VARCHAR(64) NOT NULL,
  span_id VARCHAR(64) NOT NULL,
  trace_id VARCHAR(64) NOT NULL,
  parent_span_id VARCHAR(64) NULL,
  span_type VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  sequence_no INT NOT NULL,
  PRIMARY KEY (platform_id, tenant_id, span_id),
  KEY ix_trace_run (platform_id, tenant_id, run_id, sequence_no)
) ENGINE=InnoDB;

CREATE TABLE usage_ledger (
  platform_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) NOT NULL,
  call_id VARCHAR(64) NOT NULL,
  run_id VARCHAR(64) NOT NULL,
  total_tokens BIGINT NOT NULL,
  cost_amount DECIMAL(20,8) NOT NULL,
  currency VARCHAR(8) NOT NULL,
  pricing_version VARCHAR(64) NOT NULL,
  usage_status VARCHAR(16) NOT NULL,
  PRIMARY KEY (platform_id, tenant_id, call_id)
) ENGINE=InnoDB;
