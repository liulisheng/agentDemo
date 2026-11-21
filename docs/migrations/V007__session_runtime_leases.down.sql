DROP TABLE IF EXISTS session_runtime_leases;

DELETE FROM schema_migration_history WHERE version = 7;
DELETE FROM schema_version WHERE version = 7;
