# 04: SQLite Schema 004 — Tables & Indexes Only

**What to build:** A forward-only SQL migration creating the `publish_intents` and `document_versions` tables with all constraints and indexes. This is schema-only — no data backfill, no Python logic. After this ticket, the database schema supports durable publication intent journaling and immutable document version history.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Scope

- Create migration file `004_publish_intents_and_document_versions.sql`.
- `publish_intents` table with `UNIQUE(job_id)` constraint ensuring at most one active intent per job.
- `document_versions` table with `UNIQUE(job_id, version)` constraint and nullable `sha256`.
- `integrity_status` column with `CHECK(integrity_status IN ('VALID', 'QUARANTINED'))`.
- Repository query methods for intents and document versions added to the SQLite repository layer.
- Migration applies deterministically via the existing `SQLiteMigrationRunner`.

## Non-Goals

- Data backfill of existing jobs (ticket 05 — requires Python runtime, file I/O, and SHA-256 computation).
- Publication service implementation (ticket 06).
- Any ALTER TABLE on existing tables.
- Any Python application logic.

## Files Likely Impacted

- Create: `infrastructure/persistence/sqlite/migrations/004_publish_intents_and_document_versions.sql`
- Modify: `infrastructure/persistence/sqlite/repositories.py` (add intent and document version query/insert/delete methods)
- Create: `tests/unit/test_migration_004_schema.py`

## Schema Definition (from approved architecture)

```sql
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
```

## Architectural Invariants

- `document_versions` is the sole authoritative source of canonical document version history.
- `document_versions.sha256` is nullable — NULL is permitted only for `QUARANTINED` legacy content.
- `idx_publish_intents_active_job` UNIQUE index prevents concurrent publishers for the same job.
- Migration is forward-only. Source rollback (`git revert`) does not alter already-migrated databases. Database schema rollback requires explicit `DROP TABLE` statements.
- No data is inserted by the SQL migration itself — backfill is a separate Python-level operation (ticket 05).

## Acceptance Criteria

- [ ] Migration 004 applies cleanly via `SQLiteMigrationRunner` on a fresh database.
- [ ] Migration 004 applies cleanly on an existing database at schema version 3.
- [ ] `publish_intents` table exists with all columns and the unique job index.
- [ ] `document_versions` table exists with all columns, nullable `sha256`, `integrity_status` check, and `UNIQUE(job_id, version)`.
- [ ] Repository layer provides query methods: `get_intent_by_job_id`, `insert_intent`, `update_intent_status`, `delete_intent`, `get_latest_document_version`, `insert_document_version`.
- [ ] Schema version table records version 4 after migration.
- [ ] All new tests pass. No existing tests broken.

## Tests Required

- `test_migration_004_creates_tables()`: verify tables exist after migration.
- `test_migration_004_unique_constraints()`: verify UNIQUE(job_id) on intents and UNIQUE(job_id, version) on document_versions.
- `test_migration_004_integrity_status_check()`: verify CHECK constraint rejects invalid values.
- `test_migration_004_nullable_sha256()`: verify NULL is accepted for sha256.
- `test_migration_004_idempotent_on_fresh_db()`: verify clean application.
- `test_repository_intent_crud()`: insert, query, update status, delete.
- `test_repository_document_version_crud()`: insert, get_latest, query by job_id.

## Commit Boundary

Single commit: `feat(persistence): add migration 004 for publish_intents and document_versions tables`

## Rollback

- Source rollback: `git revert` removes the `.sql` file and repository methods. Already-migrated databases are unaffected.
- Database rollback: requires explicit `DROP TABLE IF EXISTS publish_intents; DROP TABLE IF EXISTS document_versions; DELETE FROM schema_version WHERE version = 4;`.

## Out of Scope

- Legacy job backfill (ticket 05).
- Publication service (ticket 06).
- Any ALTER TABLE on `jobs` or other existing tables.
