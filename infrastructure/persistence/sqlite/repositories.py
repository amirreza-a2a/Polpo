# ============================================================
#  infrastructure/persistence/sqlite/repositories.py
# ============================================================

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.entities.settings import AppSettings
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.entities.job import Job, Pipeline2Job, JobStatus
from core.entities.bounding_box import BoundingBox
from core.entities.visual_region import (
    VisualRegion,
    RegionOrigin,
    ReviewStatus,
    SyncStatus,
)
from application.ports.repositories import (
    ISettingsRepository,
    IPromptRepository,
    IApiRepository,
    IJobRepository,
    IPipeline2JobRepository,
    IVisualRegionRepository,
)


def _parse_iso_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def _format_iso_dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _ensure_transaction(conn: sqlite3.Connection) -> None:
    """Starts a normal deferred transaction if one is not already active."""
    if not conn.in_transaction:
        conn.execute("BEGIN")


# ============================================================
#  SQLiteSettingsRepository
# ============================================================

class SQLiteSettingsRepository(ISettingsRepository):
    """
    SQLite repository for singleton application settings (row id = 1).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self) -> AppSettings:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM app_settings WHERE id = 1")
        row = cur.fetchone()
        if not row:
            default_settings = AppSettings()
            self.save(default_settings)
            return default_settings

        return AppSettings(
            theme=row["theme"],
            max_concurrent_jobs=row["max_concurrent_jobs"],
            auto_retry=bool(row["auto_retry"]),
            auto_pipeline2=bool(row["auto_pipeline2"]),
            default_prompt_id=row["default_prompt_id"],
            default_pipeline2_prompt_id=row["default_pipeline2_prompt_id"],
            artifact_retention_days=row["artifact_retention_days"],
            missed_schedule_policy=row["missed_schedule_policy"],
        )

    def save(self, settings: AppSettings) -> AppSettings:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO app_settings (
                id, theme, max_concurrent_jobs, auto_retry, auto_pipeline2,
                default_prompt_id, default_pipeline2_prompt_id,
                artifact_retention_days, missed_schedule_policy, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                theme = excluded.theme,
                max_concurrent_jobs = excluded.max_concurrent_jobs,
                auto_retry = excluded.auto_retry,
                auto_pipeline2 = excluded.auto_pipeline2,
                default_prompt_id = excluded.default_prompt_id,
                default_pipeline2_prompt_id = excluded.default_pipeline2_prompt_id,
                artifact_retention_days = excluded.artifact_retention_days,
                missed_schedule_policy = excluded.missed_schedule_policy,
                updated_at = excluded.updated_at
            """,
            (
                settings.theme,
                settings.max_concurrent_jobs,
                1 if settings.auto_retry else 0,
                1 if settings.auto_pipeline2 else 0,
                settings.default_prompt_id,
                settings.default_pipeline2_prompt_id,
                settings.artifact_retention_days,
                settings.missed_schedule_policy,
                now_iso,
            ),
        )
        return settings


# ============================================================
#  SQLitePromptRepository
# ============================================================

class SQLitePromptRepository(IPromptRepository):
    """
    SQLite repository for system and user prompts.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_entity(self, row: sqlite3.Row) -> Prompt:
        return Prompt(
            id=row["id"],
            name=row["name"],
            text=row["text"],
            prompt_type=PromptType(row["prompt_type"]),
            is_default=bool(row["is_default"]),
            created_at=_parse_iso_dt(row["created_at"]),
            updated_at=_parse_iso_dt(row["updated_at"]),
        )

    def get_by_id(self, prompt_id: int) -> Optional[Prompt]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def get_default(self, prompt_type: PromptType) -> Optional[Prompt]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM prompts WHERE prompt_type = ? AND is_default = 1 ORDER BY id DESC LIMIT 1",
            (prompt_type.value,),
        )
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def list_all(self, prompt_type: Optional[PromptType] = None) -> List[Prompt]:
        cur = self.conn.cursor()
        if prompt_type:
            cur.execute(
                "SELECT * FROM prompts WHERE prompt_type = ? ORDER BY id ASC",
                (prompt_type.value,),
            )
        else:
            cur.execute("SELECT * FROM prompts ORDER BY id ASC")
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def save(self, prompt: Prompt) -> Prompt:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()

        if prompt.id is None:
            created_iso = _format_iso_dt(prompt.created_at) or now_iso
            cur.execute(
                """
                INSERT INTO prompts (name, text, prompt_type, is_default, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    prompt.name,
                    prompt.text,
                    prompt.prompt_type.value,
                    1 if prompt.is_default else 0,
                    created_iso,
                    now_iso,
                ),
            )
            prompt.id = cur.lastrowid
            prompt.created_at = _parse_iso_dt(created_iso)
            prompt.updated_at = _parse_iso_dt(now_iso)
        else:
            cur.execute(
                """
                UPDATE prompts
                SET name = ?, text = ?, prompt_type = ?, is_default = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    prompt.name,
                    prompt.text,
                    prompt.prompt_type.value,
                    1 if prompt.is_default else 0,
                    now_iso,
                    prompt.id,
                ),
            )
            prompt.updated_at = _parse_iso_dt(now_iso)

        if prompt.is_default:
            self.set_default(prompt.id, prompt.prompt_type)

        return prompt

    def delete(self, prompt_id: int) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
        return cur.rowcount > 0

    def set_default(self, prompt_id: int, prompt_type: PromptType) -> None:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("UPDATE prompts SET is_default = 0 WHERE prompt_type = ?", (prompt_type.value,))
        cur.execute("UPDATE prompts SET is_default = 1 WHERE id = ?", (prompt_id,))

    def toggle_active(self, prompt_id: int, is_active: bool) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE prompts SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if is_active else 0, _format_iso_dt(datetime.now(timezone.utc)), prompt_id),
        )
        return cur.rowcount > 0

    def get_quick_convert_prompt(self) -> Optional[str]:
        prompt = self.get_default(PromptType.QUICK_CONVERT)
        return prompt.text if prompt else None

    def set_quick_convert_prompt(self, text: str) -> None:
        prompt = self.get_default(PromptType.QUICK_CONVERT)
        if prompt:
            prompt.text = text
            self.save(prompt)
        else:
            new_prompt = Prompt(
                id=None,
                name="Quick Convert Default",
                text=text,
                prompt_type=PromptType.QUICK_CONVERT,
                is_default=True,
            )
            self.save(new_prompt)


# ============================================================
#  SQLiteApiSlotRepository
# ============================================================

class SQLiteApiSlotRepository(IApiRepository):
    """
    SQLite repository for BYOK API slots. Stores only credential references (no plaintext keys).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_entity(self, row: sqlite3.Row) -> ApiSlot:
        supported = None
        if row["supported_models"]:
            try:
                supported = json.loads(row["supported_models"])
            except Exception:
                supported = None

        cred_ref = CredentialRef(
            identifier=row["credential_identifier"],
            provider=row["provider"],
            slot_type=row["slot_type"] or "byok",
        )

        return ApiSlot(
            id=row["id"],
            provider=row["provider"],
            label=row["label"],
            credential_ref=cred_ref,
            slot_type=row["slot_type"] or "byok",
            selected_model=row["selected_model"],
            base_url=row["base_url"],
            supported_models=supported,
        )

    def get_by_id(self, api_id: int, slot_type: str = "byok") -> Optional[ApiSlot]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM api_slots WHERE id = ?", (api_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def list_all(self) -> List[ApiSlot]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM api_slots ORDER BY priority ASC, id ASC")
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def save(self, slot: ApiSlot) -> ApiSlot:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        supported_json = json.dumps(slot.supported_models) if slot.supported_models is not None else None
        cur = self.conn.cursor()

        if slot.id is None:
            cur.execute(
                """
                INSERT INTO api_slots (
                    provider, label, credential_identifier, slot_type,
                    selected_model, base_url, supported_models,
                    priority, is_active, total_pages_processed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 0, ?, ?)
                """,
                (
                    slot.provider,
                    slot.label,
                    slot.credential_ref.identifier,
                    slot.slot_type,
                    slot.selected_model,
                    slot.base_url,
                    supported_json,
                    now_iso,
                    now_iso,
                ),
            )
            new_id = cur.lastrowid
            return ApiSlot(
                id=new_id,
                provider=slot.provider,
                label=slot.label,
                credential_ref=slot.credential_ref,
                slot_type=slot.slot_type,
                selected_model=slot.selected_model,
                base_url=slot.base_url,
                supported_models=slot.supported_models,
            )
        else:
            cur.execute(
                """
                UPDATE api_slots SET
                    provider = ?, label = ?, credential_identifier = ?, slot_type = ?,
                    selected_model = ?, base_url = ?, supported_models = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    slot.provider,
                    slot.label,
                    slot.credential_ref.identifier,
                    slot.slot_type,
                    slot.selected_model,
                    slot.base_url,
                    supported_json,
                    now_iso,
                    slot.id,
                ),
            )
            return slot

    def delete(self, api_id: int) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("DELETE FROM api_slots WHERE id = ?", (api_id,))
        return cur.rowcount > 0

    def report_pages_used(self, api_id: int, slot_type: str, pages: int = 1) -> None:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE api_slots
            SET total_pages_processed = total_pages_processed + ?, updated_at = ?
            WHERE id = ?
            """,
            (pages, now_iso, api_id),
        )

    # Legacy Compatibility Methods (satisfying IApiRepository protocol)
    def list_by_user(self, user_id: int, include_public: bool = True) -> List[ApiSlot]:
        return self.list_all()

    def list_public(self) -> List[ApiSlot]:
        return self.list_all()

    def delete_private(self, api_id: int, user_id: int) -> bool:
        return self.delete(api_id)

    def delete_public(self, api_id: int) -> bool:
        return self.delete(api_id)

    def toggle_public(self, api_id: int, is_active: bool) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("UPDATE api_slots SET is_active = ? WHERE id = ?", (1 if is_active else 0, api_id))
        return cur.rowcount > 0

    def update_public_model_url(self, api_id: int, model: Optional[str] = None, base_url: Optional[str] = None) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("UPDATE api_slots SET selected_model = COALESCE(?, selected_model), base_url = COALESCE(?, base_url) WHERE id = ?", (model, base_url, api_id))
        return cur.rowcount > 0

    def save_private(self, *args, **kwargs) -> int:
        raise NotImplementedError("Legacy save_private is not supported on SQLite. Use save(ApiSlot).")

    def save_public(self, *args, **kwargs) -> int:
        raise NotImplementedError("Legacy save_public is not supported on SQLite. Use save(ApiSlot).")


# ============================================================
#  SQLiteJobRepository
# ============================================================

class SQLiteJobRepository(IJobRepository):
    """
    SQLite repository for primary document conversion jobs with atomic claiming support.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_entity(self, row: sqlite3.Row) -> Job:
        api_chain = []
        if row["api_chain"]:
            try:
                data = json.loads(row["api_chain"])
                api_chain = [ApiSlot.from_dict(item) for item in data]
            except Exception:
                api_chain = []

        switch_log = []
        if row["api_switch_log"]:
            try:
                switch_log = json.loads(row["api_switch_log"])
            except Exception:
                switch_log = []

        return Job(
            id=row["id"],
            file_name=row["file_name"],
            file_path=row["file_path"],
            total_pages=row["total_pages"],
            processed_pages=row["processed_pages"],
            status=JobStatus(row["status"]),
            prompt_id=row["prompt_id"],
            prompt_text=row["prompt_text"],
            api_chain=api_chain,
            current_api_index=row["current_api_index"],
            api_switch_log=switch_log,
            output_path=row["output_path"],
            error_message=row["error_message"],
            retry_count=row["retry_count"],
            auto_pipeline2=bool(row["auto_pipeline2"]),
            pipeline2_prompt_id=row["pipeline2_prompt_id"],
            scheduled_at=_parse_iso_dt(row["scheduled_at"]),
            cancel_requested=bool(row["cancel_requested"]),
            claimed_at=_parse_iso_dt(row["claimed_at"]),
            created_at=_parse_iso_dt(row["created_at"]),
            updated_at=_parse_iso_dt(row["updated_at"]),
        )

    def get_by_id(self, job_id: int) -> Optional[Job]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def get_next_pending(self) -> Optional[Job]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def save(self, job: Job) -> Job:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        api_chain_json = json.dumps([slot.to_dict() for slot in job.api_chain], ensure_ascii=False)
        switch_log_json = json.dumps(job.api_switch_log, ensure_ascii=False)
        scheduled_iso = _format_iso_dt(job.scheduled_at)
        claimed_iso = _format_iso_dt(job.claimed_at)
        cur = self.conn.cursor()

        if job.id is None:
            created_iso = _format_iso_dt(job.created_at) or now_iso
            cur.execute(
                """
                INSERT INTO jobs (
                    file_name, file_path, total_pages, processed_pages,
                    status, prompt_id, prompt_text, api_chain, current_api_index,
                    api_switch_log, output_path, error_message, retry_count,
                    auto_pipeline2, pipeline2_prompt_id, scheduled_at,
                    cancel_requested, claimed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.file_name,
                    job.file_path,
                    job.total_pages,
                    job.processed_pages,
                    job.status.value,
                    job.prompt_id,
                    job.prompt_text,
                    api_chain_json,
                    job.current_api_index,
                    switch_log_json,
                    job.output_path,
                    job.error_message,
                    job.retry_count,
                    1 if job.auto_pipeline2 else 0,
                    job.pipeline2_prompt_id,
                    scheduled_iso,
                    1 if job.cancel_requested else 0,
                    claimed_iso,
                    created_iso,
                    now_iso,
                ),
            )
            job.id = cur.lastrowid
            job.created_at = _parse_iso_dt(created_iso)
            job.updated_at = _parse_iso_dt(now_iso)
        else:
            cur.execute(
                """
                UPDATE jobs SET
                    file_name = ?, file_path = ?, total_pages = ?, processed_pages = ?,
                    status = ?, prompt_id = ?, prompt_text = ?, api_chain = ?,
                    current_api_index = ?, api_switch_log = ?, output_path = ?,
                    error_message = ?, retry_count = ?, auto_pipeline2 = ?,
                    pipeline2_prompt_id = ?, scheduled_at = ?, cancel_requested = ?,
                    claimed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    job.file_name,
                    job.file_path,
                    job.total_pages,
                    job.processed_pages,
                    job.status.value,
                    job.prompt_id,
                    job.prompt_text,
                    api_chain_json,
                    job.current_api_index,
                    switch_log_json,
                    job.output_path,
                    job.error_message,
                    job.retry_count,
                    1 if job.auto_pipeline2 else 0,
                    job.pipeline2_prompt_id,
                    scheduled_iso,
                    1 if job.cancel_requested else 0,
                    claimed_iso,
                    now_iso,
                    job.id,
                ),
            )
            job.updated_at = _parse_iso_dt(now_iso)
        return job

    def update_progress(
        self,
        job_id: int,
        processed_pages: int,
        switch_log: List[dict],
        output_path: Optional[str] = None,
    ) -> None:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        switch_json = json.dumps(switch_log, ensure_ascii=False)
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE jobs SET
                processed_pages = ?,
                api_switch_log = ?,
                output_path = COALESCE(?, output_path),
                updated_at = ?
            WHERE id = ?
            """,
            (processed_pages, switch_json, output_path, now_iso, job_id),
        )

    def update_status(self, job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE jobs SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status.value, error_message, now_iso, job_id),
        )

    def get_queue_position(self, job_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending' AND id <= ?", (job_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] > 0 else 1

    def list(self, limit: int = 50, offset: int = 0, status: Optional[JobStatus] = None) -> List[Job]:
        cur = self.conn.cursor()
        if status:
            cur.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (status.value, limit, offset),
            )
        else:
            cur.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def claim_next_pending(self, due_before: Optional[datetime] = None) -> Optional[Job]:
        """
        Atomically claims the next eligible pending job.
        Requires that no outer transaction is active to guarantee dedicated BEGIN IMMEDIATE execution.
        """
        if self.conn.in_transaction:
            raise RuntimeError(
                "claim_next_pending must execute in its own dedicated atomic transaction, "
                "not within an existing active outer transaction."
            )

        due_limit = _format_iso_dt(due_before or datetime.now(timezone.utc))
        now_iso = _format_iso_dt(datetime.now(timezone.utc))

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.cursor()
            cur.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'pending'
                  AND cancel_requested = 0
                  AND (scheduled_at IS NULL OR scheduled_at <= ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (due_limit,),
            )
            row = cur.fetchone()
            if not row:
                self.conn.execute("COMMIT")
                return None

            job_id = row["id"]
            cur.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    claimed_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now_iso, now_iso, job_id),
            )
            if cur.rowcount != 1:
                self.conn.execute("ROLLBACK")
                return None

            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

        return self.get_by_id(job_id)

    def claim_job(self, job_id: int) -> Optional[Job]:
        """
        Atomically claims a specific job if still in pending state and uncancelled.
        Requires that no outer transaction is active to guarantee dedicated BEGIN IMMEDIATE execution.
        """
        if self.conn.in_transaction:
            raise RuntimeError(
                "claim_job must execute in its own dedicated atomic transaction, "
                "not within an existing active outer transaction."
            )

        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.cursor()
            cur.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    claimed_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'pending' AND cancel_requested = 0
                """,
                (now_iso, now_iso, job_id),
            )
            if cur.rowcount != 1:
                self.conn.execute("ROLLBACK")
                return None

            self.conn.execute("COMMIT")
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

        return self.get_by_id(job_id)

    def get_due_jobs(self, as_of: Optional[datetime] = None) -> List[Job]:
        as_of_iso = _format_iso_dt(as_of or datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending'
              AND cancel_requested = 0
              AND (scheduled_at IS NULL OR scheduled_at <= ?)
            ORDER BY id ASC
            """,
            (as_of_iso,),
        )
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def request_cancellation(self, job_id: int) -> bool:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET cancel_requested = 1,
                status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE status END,
                updated_at = ?
            WHERE id = ?
            """,
            (now_iso, job_id),
        )
        return cur.rowcount > 0

    def reconcile_stale_jobs(self) -> int:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE jobs
            SET status = 'paused',
                error_message = 'Interrupted by application crash or unexpected shutdown',
                updated_at = ?
            WHERE status = 'processing'
            """,
            (now_iso,),
        )
        return cur.rowcount

    def get_missed_schedules(self, as_of: Optional[datetime] = None) -> List[Job]:
        as_of_iso = _format_iso_dt(as_of or datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending'
              AND cancel_requested = 0
              AND scheduled_at IS NOT NULL
              AND scheduled_at < ?
            ORDER BY scheduled_at ASC, id ASC
            """,
            (as_of_iso,),
        )
        return [self._row_to_entity(r) for r in cur.fetchall()]

    def reschedule_job(self, job_id: int, new_scheduled_at: Optional[datetime]) -> None:
        _ensure_transaction(self.conn)
        sched_iso = _format_iso_dt(new_scheduled_at) if new_scheduled_at else None
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE jobs SET scheduled_at = ?, updated_at = ? WHERE id = ?",
            (sched_iso, now_iso, job_id),
        )

    # Legacy Compatibility Methods
    def list_by_user(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Job]:
        return self.list(limit=limit, offset=offset)

    def count_by_user(self, user_id: int, status: Optional[JobStatus] = None) -> int:
        cur = self.conn.cursor()
        if status:
            cur.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (status.value,))
        else:
            cur.execute("SELECT COUNT(*) FROM jobs")
        row = cur.fetchone()
        return row[0] if row else 0


# ============================================================
#  SQLitePipeline2JobRepository
# ============================================================

class SQLitePipeline2JobRepository(IPipeline2JobRepository):
    """
    SQLite repository for Pipeline 2 refinement jobs.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_entity(self, row: sqlite3.Row) -> Pipeline2Job:
        api_chain = []
        if row["api_chain"]:
            try:
                data = json.loads(row["api_chain"])
                api_chain = [ApiSlot.from_dict(item) for item in data]
            except Exception:
                api_chain = []

        return Pipeline2Job(
            id=row["id"],
            source_job_id=row["source_job_id"],
            prompt_id=row["prompt_id"],
            prompt_text=row["prompt_text"],
            status=JobStatus(row["status"]),
            input_path=row["input_path"],
            output_path=row["output_path"],
            api_chain=api_chain,
            current_api_index=row["current_api_index"],
            error_message=row["error_message"],
            cancel_requested=bool(row["cancel_requested"]),
            claimed_at=_parse_iso_dt(row["claimed_at"]),
            created_at=_parse_iso_dt(row["created_at"]),
            updated_at=_parse_iso_dt(row["updated_at"]),
        )

    def get_by_id(self, p2_job_id: int) -> Optional[Pipeline2Job]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM pipeline2_jobs WHERE id = ?", (p2_job_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def get_next_pending(self) -> Optional[Pipeline2Job]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM pipeline2_jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def save(self, job: Pipeline2Job) -> Pipeline2Job:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        api_chain_json = json.dumps([slot.to_dict() for slot in job.api_chain], ensure_ascii=False)
        claimed_iso = _format_iso_dt(job.claimed_at)
        cur = self.conn.cursor()

        if job.id is None:
            created_iso = _format_iso_dt(job.created_at) or now_iso
            cur.execute(
                """
                INSERT INTO pipeline2_jobs (
                    source_job_id, prompt_id, prompt_text, status, input_path,
                    output_path, api_chain, current_api_index, error_message,
                    cancel_requested, claimed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source_job_id,
                    job.prompt_id,
                    job.prompt_text,
                    job.status.value,
                    job.input_path,
                    job.output_path,
                    api_chain_json,
                    job.current_api_index,
                    job.error_message,
                    1 if job.cancel_requested else 0,
                    claimed_iso,
                    created_iso,
                    now_iso,
                ),
            )
            job.id = cur.lastrowid
            job.created_at = _parse_iso_dt(created_iso)
            job.updated_at = _parse_iso_dt(now_iso)
        else:
            cur.execute(
                """
                UPDATE pipeline2_jobs SET
                    source_job_id = ?, prompt_id = ?, prompt_text = ?, status = ?,
                    input_path = ?, output_path = ?, api_chain = ?,
                    current_api_index = ?, error_message = ?, cancel_requested = ?,
                    claimed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    job.source_job_id,
                    job.prompt_id,
                    job.prompt_text,
                    job.status.value,
                    job.input_path,
                    job.output_path,
                    api_chain_json,
                    job.current_api_index,
                    job.error_message,
                    1 if job.cancel_requested else 0,
                    claimed_iso,
                    now_iso,
                    job.id,
                ),
            )
            job.updated_at = _parse_iso_dt(now_iso)
        return job

    def update_status(self, p2_job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE pipeline2_jobs SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status.value, error_message, now_iso, p2_job_id),
        )

    def update_paths(self, p2_job_id: int, input_path: Optional[str] = None, output_path: Optional[str] = None) -> None:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE pipeline2_jobs SET
                input_path = COALESCE(?, input_path),
                output_path = COALESCE(?, output_path),
                updated_at = ?
            WHERE id = ?
            """,
            (input_path, output_path, now_iso, p2_job_id),
        )

    def update_output_path(self, p2_job_id: int, output_path: str) -> None:
        self.update_paths(p2_job_id, output_path=output_path)


# ============================================================
#  SQLiteVisualRegionRepository
# ============================================================

class SQLiteVisualRegionRepository(IVisualRegionRepository):
    """
    SQLite repository for visual document regions and provenance tracking.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _row_to_entity(self, row: sqlite3.Row) -> VisualRegion:
        detected_bbox = None
        if row["detected_ymin"] is not None:
            detected_bbox = BoundingBox(
                ymin=row["detected_ymin"],
                xmin=row["detected_xmin"],
                ymax=row["detected_ymax"],
                xmax=row["detected_xmax"],
            )

        reviewed_bbox = None
        if row["reviewed_ymin"] is not None:
            reviewed_bbox = BoundingBox(
                ymin=row["reviewed_ymin"],
                xmin=row["reviewed_xmin"],
                ymax=row["reviewed_ymax"],
                xmax=row["reviewed_xmax"],
            )

        return VisualRegion(
            id=row["id"],
            region_id=row["region_id"],
            job_id=row["job_id"],
            page_number=row["page_number"],
            display_order=row["display_order"],
            origin=RegionOrigin(row["origin"]),
            detected_bbox=detected_bbox,
            reviewed_bbox=reviewed_bbox,
            review_status=ReviewStatus(row["review_status"]),
            sync_status=SyncStatus(row["sync_status"]),
            active_artifact_version=row["active_artifact_version"],
            active_artifact_uri=row["active_artifact_uri"],
            created_at=_parse_iso_dt(row["created_at"]),
            updated_at=_parse_iso_dt(row["updated_at"]),
        )

    def get_by_id(self, id: int) -> Optional[VisualRegion]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM visual_regions WHERE id = ?", (id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def get_by_region_id(self, region_id: str) -> Optional[VisualRegion]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM visual_regions WHERE region_id = ?", (region_id,))
        row = cur.fetchone()
        return self._row_to_entity(row) if row else None

    def get_by_job_id(self, job_id: int) -> List[VisualRegion]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM visual_regions WHERE job_id = ? ORDER BY page_number ASC, display_order ASC, id ASC",
            (job_id,),
        )
        return [self._row_to_entity(row) for row in cur.fetchall()]

    def get_by_job_and_page(self, job_id: int, page_number: int) -> List[VisualRegion]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM visual_regions WHERE job_id = ? AND page_number = ? ORDER BY display_order ASC, id ASC",
            (job_id, page_number),
        )
        return [self._row_to_entity(row) for row in cur.fetchall()]

    def save(self, region: VisualRegion) -> VisualRegion:
        _ensure_transaction(self.conn)
        now_iso = _format_iso_dt(datetime.now(timezone.utc))
        cur = self.conn.cursor()

        det_ymin = region.detected_bbox.ymin if region.detected_bbox else None
        det_xmin = region.detected_bbox.xmin if region.detected_bbox else None
        det_ymax = region.detected_bbox.ymax if region.detected_bbox else None
        det_xmax = region.detected_bbox.xmax if region.detected_bbox else None

        rev_ymin = region.reviewed_bbox.ymin if region.reviewed_bbox else None
        rev_xmin = region.reviewed_bbox.xmin if region.reviewed_bbox else None
        rev_ymax = region.reviewed_bbox.ymax if region.reviewed_bbox else None
        rev_xmax = region.reviewed_bbox.xmax if region.reviewed_bbox else None

        if region.id is None:
            created_iso = _format_iso_dt(region.created_at) or now_iso
            cur.execute(
                """
                INSERT INTO visual_regions (
                    job_id, region_id, page_number, display_order, origin,
                    detected_ymin, detected_xmin, detected_ymax, detected_xmax,
                    reviewed_ymin, reviewed_xmin, reviewed_ymax, reviewed_xmax,
                    review_status, sync_status, active_artifact_version, active_artifact_uri,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region.job_id,
                    region.region_id,
                    region.page_number,
                    region.display_order,
                    region.origin.value,
                    det_ymin,
                    det_xmin,
                    det_ymax,
                    det_xmax,
                    rev_ymin,
                    rev_xmin,
                    rev_ymax,
                    rev_xmax,
                    region.review_status.value,
                    region.sync_status.value,
                    region.active_artifact_version,
                    region.active_artifact_uri,
                    created_iso,
                    now_iso,
                ),
            )
            new_id = cur.lastrowid
            region.id = new_id
            region.updated_at = _parse_iso_dt(now_iso)
        else:
            cur.execute(
                """
                UPDATE visual_regions SET
                    job_id = ?, region_id = ?, page_number = ?, display_order = ?, origin = ?,
                    detected_ymin = ?, detected_xmin = ?, detected_ymax = ?, detected_xmax = ?,
                    reviewed_ymin = ?, reviewed_xmin = ?, reviewed_ymax = ?, reviewed_xmax = ?,
                    review_status = ?, sync_status = ?, active_artifact_version = ?, active_artifact_uri = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    region.job_id,
                    region.region_id,
                    region.page_number,
                    region.display_order,
                    region.origin.value,
                    det_ymin,
                    det_xmin,
                    det_ymax,
                    det_xmax,
                    rev_ymin,
                    rev_xmin,
                    rev_ymax,
                    rev_xmax,
                    region.review_status.value,
                    region.sync_status.value,
                    region.active_artifact_version,
                    region.active_artifact_uri,
                    now_iso,
                    region.id,
                ),
            )
            region.updated_at = _parse_iso_dt(now_iso)

        return region

    def save_all(self, regions: List[VisualRegion]) -> List[VisualRegion]:
        _ensure_transaction(self.conn)
        return [self.save(r) for r in regions]

    def delete_by_job_id(self, job_id: int) -> int:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("DELETE FROM visual_regions WHERE job_id = ?", (job_id,))
        return cur.rowcount

    def delete_by_region_id(self, region_id: str) -> bool:
        _ensure_transaction(self.conn)
        cur = self.conn.cursor()
        cur.execute("DELETE FROM visual_regions WHERE region_id = ?", (region_id,))
        return cur.rowcount > 0
