-- ============================================================
--  PolpoT Desktop SQLite Migration (004_publish_intents_and_document_versions.sql)
--  Phase 10E.3a — Publication Authority Schema
-- ============================================================
--  Schema Version: 4
--  Changes Introduced:
--    1. publish_intents — Durable journal for crash-consistent publication.
--    2. document_versions — Immutable canonical document version history
--       with integrity status tracking.
--  Compatibility Assumptions:
--    - Safe for existing databases: new tables only, no ALTER TABLE.
--  Upgrade Behavior:
--    - Upgrades databases from schema version 3 to 4.
--  Rollback Considerations:
--    - DROP TABLE IF EXISTS publish_intents;
--    - DROP TABLE IF EXISTS document_versions;
--    - DELETE FROM schema_version WHERE version = 4;
-- ============================================================

-- 1. Durable Publish Intents
CREATE TABLE IF NOT EXISTS publish_intents (
    intent_id TEXT PRIMARY KEY,
    job_id INTEGER NOT NULL,
    base_version INTEGER NOT NULL,
    target_version INTEGER NOT NULL,
    output_filename TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    staged_artifacts_manifest TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'FLUSHED')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_intents_active_job
    ON publish_intents(job_id);

-- 2. Immutable Canonical Document Version History
CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    output_path TEXT NOT NULL,
    sha256 TEXT,
    integrity_status TEXT NOT NULL DEFAULT 'VALID'
        CHECK(integrity_status IN ('VALID', 'QUARANTINED')),
    published_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    UNIQUE(job_id, version)
);

CREATE INDEX IF NOT EXISTS idx_document_versions_job_ver
    ON document_versions(job_id, version);
