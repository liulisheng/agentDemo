-- V004 rollback for disposable database validation only.
-- Shared environments must roll forward; do not remove published Resource Center history.

DROP TABLE IF EXISTS tenant_resource_imports;
DROP TABLE IF EXISTS hub_resource_grants;
DROP TABLE IF EXISTS hub_resource_dependencies;
DROP TABLE IF EXISTS hub_resource_versions;
DROP TABLE IF EXISTS hub_resources;

ALTER TABLE tool_versions
    DROP COLUMN requires_recipient_credentials;

DELETE FROM schema_migration_history WHERE version = 4;
DELETE FROM schema_version WHERE version = 4;
