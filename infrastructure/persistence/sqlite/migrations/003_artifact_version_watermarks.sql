-- ============================================================
--  PolpoT Desktop SQLite Migration (003_artifact_version_watermarks.sql)
--  Phase 10B Watermark Persistence
-- ============================================================
--  Schema Version: 3
--  Changes Introduced:
--    1. jobs.output_artifact_version_watermark (INTEGER NOT NULL DEFAULT 0)
--       Tracks the highest generated Markdown review output artifact version
--       associated with the job to guarantee monotonic artifact progression.
--    2. visual_regions.artifact_version_watermark (INTEGER NOT NULL DEFAULT 0)
--       Tracks the crop artifact version watermark for individual visual regions.
--  Compatibility Assumptions:
--    - Safe for existing rows: SQLite ALTER TABLE ADD COLUMN populates existing
--      rows with the declared DEFAULT value (0) without table rebuilds.
--    - Forward-only deterministic migration.
--  Upgrade Behavior:
--    - Upgrades databases from schema version 1 or 2 up to version 3.
--  Rollback Considerations:
--    - SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN, but desktop installations
--      treat schema migrations as strictly forward-only.
-- ============================================================

-- 1. Output artifact version watermark for primary PDF document conversion jobs
ALTER TABLE jobs ADD COLUMN output_artifact_version_watermark INTEGER NOT NULL DEFAULT 0;

-- 2. Crop artifact version watermark for visual regions
ALTER TABLE visual_regions ADD COLUMN artifact_version_watermark INTEGER NOT NULL DEFAULT 0;
