-- ============================================================
--  PolpoT Desktop SQLite Migration (002_visual_regions.sql)
--  Visual Regions & Provenance Persistence
-- ============================================================

CREATE TABLE IF NOT EXISTS visual_regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    region_id TEXT NOT NULL UNIQUE,
    page_number INTEGER NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL CHECK(origin IN ('ai_detected', 'user_manual')),
    detected_ymin INTEGER,
    detected_xmin INTEGER,
    detected_ymax INTEGER,
    detected_xmax INTEGER,
    reviewed_ymin INTEGER,
    reviewed_xmin INTEGER,
    reviewed_ymax INTEGER,
    reviewed_xmax INTEGER,
    review_status TEXT NOT NULL DEFAULT 'unreviewed' 
        CHECK(review_status IN ('unreviewed', 'modified', 'manual', 'rejected', 'accepted')),
    sync_status TEXT NOT NULL DEFAULT 'pending_initial_crop' 
        CHECK(sync_status IN ('pending_initial_crop', 'synced', 'dirty_recrop_required', 'sync_failed')),
    active_artifact_version INTEGER NOT NULL DEFAULT 0,
    artifact_version_watermark INTEGER NOT NULL DEFAULT 0,
    active_artifact_uri TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_regions_region_id ON visual_regions(region_id);
CREATE INDEX IF NOT EXISTS idx_visual_regions_job_page ON visual_regions(job_id, page_number);
CREATE INDEX IF NOT EXISTS idx_visual_regions_sync_status ON visual_regions(job_id, sync_status);
