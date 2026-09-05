-- ============================================================
--  PolpoT Desktop SQLite Initial Schema (001_initial_schema.sql)
-- ============================================================

-- 1. Schema Version Tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- 2. System and User Prompts
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    prompt_type TEXT NOT NULL CHECK(prompt_type IN ('pipeline1', 'pipeline2', 'quick_convert')),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Singleton Application Settings
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT NOT NULL DEFAULT 'system' CHECK(theme IN ('system', 'dark', 'light')),
    max_concurrent_jobs INTEGER NOT NULL DEFAULT 2 CHECK(max_concurrent_jobs BETWEEN 1 AND 8),
    auto_retry INTEGER NOT NULL DEFAULT 1 CHECK(auto_retry IN (0, 1)),
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0 CHECK(auto_pipeline2 IN (0, 1)),
    default_prompt_id INTEGER,
    default_pipeline2_prompt_id INTEGER,
    artifact_retention_days INTEGER NOT NULL DEFAULT 30 CHECK(artifact_retention_days >= 1),
    missed_schedule_policy TEXT NOT NULL DEFAULT 'prompt' CHECK(missed_schedule_policy IN ('run_immediately', 'prompt', 'mark_paused')),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(default_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(default_pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 4. AI Provider Slots (BYOK Metadata Only — Secrets stored in OS Keyring)
CREATE TABLE IF NOT EXISTS api_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL CHECK(provider IN ('google', 'openai', 'openrouter', 'custom')),
    label TEXT NOT NULL,
    credential_identifier TEXT NOT NULL UNIQUE,
    slot_type TEXT NOT NULL DEFAULT 'byok' CHECK(slot_type IN ('byok', 'custom')),
    selected_model TEXT,
    base_url TEXT,
    supported_models TEXT,
    priority INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    total_pages_processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 5. Primary PDF Document Conversion Jobs
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    total_pages INTEGER NOT NULL DEFAULT 0,
    processed_pages INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    prompt_id INTEGER,
    prompt_text TEXT,
    api_chain TEXT,
    current_api_index INTEGER NOT NULL DEFAULT 0,
    api_switch_log TEXT,
    output_path TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    auto_pipeline2 INTEGER NOT NULL DEFAULT 0 CHECK(auto_pipeline2 IN (0, 1)),
    pipeline2_prompt_id INTEGER,
    scheduled_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL,
    FOREIGN KEY(pipeline2_prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 6. Pipeline 2 Typography Refinement Jobs
CREATE TABLE IF NOT EXISTS pipeline2_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_job_id INTEGER NOT NULL,
    prompt_id INTEGER,
    prompt_text TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'done', 'paused', 'failed', 'cancelled')),
    input_path TEXT,
    output_path TEXT,
    api_chain TEXT,
    current_api_index INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

-- 7. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_scheduled ON jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_p2_jobs_status ON pipeline2_jobs(status);
CREATE INDEX IF NOT EXISTS idx_p2_jobs_source ON pipeline2_jobs(source_job_id);
CREATE INDEX IF NOT EXISTS idx_prompts_type_default ON prompts(prompt_type, is_default);
CREATE INDEX IF NOT EXISTS idx_api_slots_active ON api_slots(is_active, priority);
