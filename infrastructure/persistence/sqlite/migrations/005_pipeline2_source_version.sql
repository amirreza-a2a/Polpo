-- ============================================================
--  PolpoT Desktop SQLite Migration (005_pipeline2_source_version.sql)
--  P2-03 — Pipeline 2 Artifact Lineage Schema
-- ============================================================
--  Schema Version: 5
--  Changes Introduced:
--    1. pipeline2_jobs: Add source_version INTEGER column for
--       durable base-version lineage tracking.
--  Compatibility Assumptions:
--    - Safe for existing databases: nullable column added via ALTER TABLE.
--    - Historical records receive NULL source_version.
--  Upgrade Behavior:
--    - Upgrades databases from schema version 4 to 5.
--  Rollback Considerations:
--    - SQLite does not support DROP COLUMN in older engines without table rebuild.
--    - DELETE FROM schema_version WHERE version = 5;
-- ============================================================

ALTER TABLE pipeline2_jobs ADD COLUMN source_version INTEGER;
