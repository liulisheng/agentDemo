-- V010 down 仅用于 disposable database 演练，不得用于生产或共享数据库。
DROP TABLE IF EXISTS runtime_strategy_bindings;
DELETE FROM schema_version WHERE version = 10;
