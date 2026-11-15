DROP TABLE IF EXISTS skill_file_entries;

ALTER TABLE skill_versions
    DROP CHECK ck_skill_file_package_metadata,
    DROP CHECK ck_skill_content_format,
    DROP COLUMN file_manifest_sha256,
    DROP COLUMN entry_path,
    DROP COLUMN content_format;

DELETE FROM schema_version WHERE version = 5;
