ALTER TABLE model_routes
    DROP FOREIGN KEY fk_model_route_catalog_model,
    DROP FOREIGN KEY fk_model_route_connection,
    DROP CHECK ck_model_route_version,
    DROP INDEX idx_model_route_catalog,
    DROP COLUMN catalog_model_id,
    DROP COLUMN model_connection_id,
    DROP COLUMN route_version;

DROP TABLE IF EXISTS model_test_records;
DROP TABLE IF EXISTS model_catalog_items;
DROP TABLE IF EXISTS model_connections;

DELETE FROM schema_migration_history WHERE version = 8;
DELETE FROM schema_version WHERE version = 8;
