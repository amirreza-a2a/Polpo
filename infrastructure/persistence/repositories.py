# ============================================================
#  infrastructure/persistence/repositories.py
# ============================================================

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import pymysql.cursors

from core.entities.job import Job, Pipeline2Job, JobStatus
from core.entities.user import User, UserPreferences, QuotaAllocation
from core.entities.prompt import Prompt, PromptType
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from application.ports.repositories import (
    IJobRepository,
    IPipeline2JobRepository,
    IUserRepository,
    IPromptRepository,
    IApiRepository,
    IDonationRepository,
)


class MySQLJobRepository(IJobRepository):
    def __init__(self, conn):
        self.conn = conn

    def get_by_id(self, job_id: int) -> Optional[Job]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def get_next_pending(self) -> Optional[Job]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def list_by_user(self, user_id: int, limit: int = 50, offset: int = 0) -> List[Job]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM jobs WHERE user_id = %s ORDER BY id DESC LIMIT %s OFFSET %s",
                (user_id, limit, offset),
            )
            rows = cur.fetchall()
            return [self._row_to_entity(r) for r in rows]

    def save(self, job: Job) -> Job:
        with self.conn.cursor() as cur:
            api_chain_json = json.dumps([slot.to_dict() for slot in job.api_chain], ensure_ascii=False)
            switch_log_json = json.dumps(job.api_switch_log, ensure_ascii=False)

            if job.id is None:
                cur.execute(
                    """
                    INSERT INTO jobs (
                        user_id, file_name, file_path, total_pages, processed_pages,
                        status, prompt_id, prompt_text, api_chain, current_api_index,
                        api_switch_log, output_path, error_message, retry_count,
                        auto_pipeline2, pipeline2_prompt_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job.user_id, job.file_name, job.file_path, job.total_pages, job.processed_pages,
                        job.status.value, job.prompt_id, job.prompt_text, api_chain_json, job.current_api_index,
                        switch_log_json, job.output_path, job.error_message, job.retry_count,
                        int(job.auto_pipeline2), job.pipeline2_prompt_id,
                    ),
                )
                job.id = cur.lastrowid
            else:
                cur.execute(
                    """
                    UPDATE jobs SET
                        total_pages = %s, processed_pages = %s, status = %s,
                        api_chain = %s, current_api_index = %s, api_switch_log = %s,
                        output_path = %s, error_message = %s, retry_count = %s,
                        auto_pipeline2 = %s, pipeline2_prompt_id = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        job.total_pages, job.processed_pages, job.status.value,
                        api_chain_json, job.current_api_index, switch_log_json,
                        job.output_path, job.error_message, job.retry_count,
                        int(job.auto_pipeline2), job.pipeline2_prompt_id, job.id,
                    ),
                )
        return job

    def update_progress(self, job_id: int, processed_pages: int, switch_log: List[dict], output_path: Optional[str] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs SET
                    processed_pages = %s,
                    api_switch_log = %s,
                    output_path = COALESCE(%s, output_path),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (processed_pages, json.dumps(switch_log, ensure_ascii=False), output_path, job_id),
            )

    def update_status(self, job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = %s, error_message = %s, updated_at = NOW() WHERE id = %s",
                (status.value, error_message, job_id),
            )

    def _row_to_entity(self, row: dict) -> Job:
        chain_raw = json.loads(row["api_chain"]) if row.get("api_chain") else []
        chain = [ApiSlot.from_dict(s) if isinstance(s, dict) else s for s in chain_raw]

        switch_log = json.loads(row["api_switch_log"]) if row.get("api_switch_log") else []

        return Job(
            id=row["id"],
            user_id=row["user_id"],
            file_name=row["file_name"],
            file_path=row["file_path"],
            total_pages=row.get("total_pages") or 0,
            processed_pages=row.get("processed_pages") or 0,
            status=JobStatus(row["status"]) if row.get("status") else JobStatus.PENDING,
            prompt_id=row.get("prompt_id"),
            prompt_text=row.get("prompt_text"),
            api_chain=chain,
            current_api_index=row.get("current_api_index") or 0,
            api_switch_log=switch_log,
            output_path=row.get("output_path"),
            error_message=row.get("error_message"),
            retry_count=row.get("retry_count") or 0,
            auto_pipeline2=bool(row.get("auto_pipeline2")),
            pipeline2_prompt_id=row.get("pipeline2_prompt_id"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


class MySQLPipeline2JobRepository(IPipeline2JobRepository):
    def __init__(self, conn):
        self.conn = conn

    def get_by_id(self, p2_job_id: int) -> Optional[Pipeline2Job]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM pipeline2_jobs WHERE id = %s", (p2_job_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def get_next_pending(self) -> Optional[Pipeline2Job]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM pipeline2_jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def save(self, job: Pipeline2Job) -> Pipeline2Job:
        with self.conn.cursor() as cur:
            chain_json = json.dumps([slot.to_dict() for slot in job.api_chain], ensure_ascii=False)
            if job.id is None:
                cur.execute(
                    """
                    INSERT INTO pipeline2_jobs (
                        source_job_id, user_id, prompt_id, prompt_text,
                        status, input_path, output_path, api_chain, current_api_index
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job.source_job_id, job.user_id, job.prompt_id, job.prompt_text,
                        job.status.value, job.input_path, job.output_path, chain_json, job.current_api_index,
                    ),
                )
                job.id = cur.lastrowid
            else:
                cur.execute(
                    """
                    UPDATE pipeline2_jobs SET
                        status = %s, input_path = %s, output_path = %s,
                        api_chain = %s, current_api_index = %s, error_message = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        job.status.value, job.input_path, job.output_path,
                        chain_json, job.current_api_index, job.error_message, job.id,
                    ),
                )
        return job

    def update_status(self, p2_job_id: int, status: JobStatus, error_message: Optional[str] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE pipeline2_jobs SET status = %s, error_message = %s, updated_at = NOW() WHERE id = %s",
                (status.value, error_message, p2_job_id),
            )

    def update_paths(self, p2_job_id: int, input_path: Optional[str] = None, output_path: Optional[str] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline2_jobs SET
                    input_path = COALESCE(%s, input_path),
                    output_path = COALESCE(%s, output_path),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (input_path, output_path, p2_job_id),
            )

    def _row_to_entity(self, row: dict) -> Pipeline2Job:
        chain_raw = json.loads(row["api_chain"]) if row.get("api_chain") else []
        chain = [ApiSlot.from_dict(s) if isinstance(s, dict) else s for s in chain_raw]
        return Pipeline2Job(
            id=row["id"],
            source_job_id=row["source_job_id"],
            user_id=row["user_id"],
            prompt_id=row.get("prompt_id"),
            prompt_text=row.get("prompt_text"),
            status=JobStatus(row["status"]) if row.get("status") else JobStatus.PENDING,
            input_path=row.get("input_path"),
            output_path=row.get("output_path"),
            api_chain=chain,
            current_api_index=row.get("current_api_index") or 0,
            error_message=row.get("error_message"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


class MySQLUserRepository(IUserRepository):
    def __init__(self, conn):
        self.conn = conn

    def get_by_id(self, user_id: int) -> Optional[User]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def get_by_username(self, username: str) -> Optional[User]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row)

    def save(self, user: User) -> User:
        with self.conn.cursor() as cur:
            if user.id is None:
                cur.execute(
                    """
                    INSERT INTO users (
                        telegram_id, username, is_admin, daily_pages_used,
                        last_active_date, use_public_fallback, auto_retry,
                        auto_pipeline2, default_prompt_id, default_pipeline2_prompt_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user.telegram_id, user.username, int(user.is_admin),
                        user.quota.daily_pages_used, user.quota.last_active_date,
                        int(user.preferences.use_public_fallback), int(user.preferences.auto_retry),
                        int(user.preferences.auto_pipeline2), user.preferences.default_prompt_id,
                        user.preferences.default_pipeline2_prompt_id,
                    ),
                )
                user.id = cur.lastrowid
            else:
                cur.execute(
                    """
                    UPDATE users SET
                        username = %s, is_admin = %s, daily_pages_used = %s,
                        last_active_date = %s, use_public_fallback = %s, auto_retry = %s,
                        auto_pipeline2 = %s, default_prompt_id = %s,
                        default_pipeline2_prompt_id = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        user.username, int(user.is_admin), user.quota.daily_pages_used,
                        user.quota.last_active_date, int(user.preferences.use_public_fallback),
                        int(user.preferences.auto_retry), int(user.preferences.auto_pipeline2),
                        user.preferences.default_prompt_id, user.preferences.default_pipeline2_prompt_id,
                        user.id,
                    ),
                )
        return user

    def increment_daily_pages(self, user_id: int, amount: int = 1) -> int:
        with self.conn.cursor() as cur:
            today = date.today()
            cur.execute(
                """
                UPDATE users SET
                    daily_pages_used = daily_pages_used + %s,
                    last_active_date = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (amount, today, user_id),
            )
            cur.execute("SELECT daily_pages_used FROM users WHERE id = %s", (user_id,))
            res = cur.fetchone()
            return res[0] if res else 0

    def reset_daily_quota(self, user_id: int) -> None:
        with self.conn.cursor() as cur:
            today = date.today()
            cur.execute(
                "UPDATE users SET daily_pages_used = 0, last_active_date = %s, updated_at = NOW() WHERE id = %s",
                (today, user_id),
            )

    def _row_to_entity(self, row: dict) -> User:
        return User(
            id=row["id"],
            telegram_id=row.get("telegram_id"),
            username=row.get("username"),
            is_admin=bool(row.get("is_admin")),
            quota=QuotaAllocation(
                daily_limit=50,
                daily_pages_used=row.get("daily_pages_used") or 0,
                last_active_date=row.get("last_active_date"),
            ),
            preferences=UserPreferences(
                use_public_fallback=bool(row.get("use_public_fallback", 1)),
                auto_retry=bool(row.get("auto_retry", 0)),
                auto_pipeline2=bool(row.get("auto_pipeline2", 0)),
                default_prompt_id=row.get("default_prompt_id"),
                default_pipeline2_prompt_id=row.get("default_pipeline2_prompt_id"),
            ),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


class MySQLPromptRepository(IPromptRepository):
    def __init__(self, conn):
        self.conn = conn

    def get_by_id(self, prompt_id: int) -> Optional[Prompt]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Check pipeline1 prompts
            cur.execute("SELECT id, name, text, is_default, created_at, updated_at FROM prompts WHERE id = %s", (prompt_id,))
            row = cur.fetchone()
            if row:
                return Prompt(
                    id=row["id"],
                    name=row["name"],
                    text=row["text"],
                    prompt_type=PromptType.PIPELINE_1,
                    is_default=bool(row.get("is_default")),
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at"),
                )

            # Check pipeline2 prompts
            cur.execute("SELECT id, name, text, is_default, created_at, updated_at FROM pipeline2_prompts WHERE id = %s", (prompt_id,))
            row = cur.fetchone()
            if row:
                return Prompt(
                    id=row["id"],
                    name=row["name"],
                    text=row["text"],
                    prompt_type=PromptType.PIPELINE_2,
                    is_default=bool(row.get("is_default")),
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at"),
                )
            return None

    def get_default(self, prompt_type: PromptType) -> Optional[Prompt]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            table = "pipeline2_prompts" if prompt_type == PromptType.PIPELINE_2 else "prompts"
            cur.execute(f"SELECT * FROM {table} WHERE is_default = 1 LIMIT 1")
            row = cur.fetchone()
            if not row:
                cur.execute(f"SELECT * FROM {table} ORDER BY id ASC LIMIT 1")
                row = cur.fetchone()
            if not row:
                return None
            return Prompt(
                id=row["id"],
                name=row["name"],
                text=row["text"],
                prompt_type=prompt_type,
                is_default=bool(row.get("is_default")),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )

    def list_all(self, prompt_type: Optional[PromptType] = None) -> List[Prompt]:
        prompts: List[Prompt] = []
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            if prompt_type is None or prompt_type == PromptType.PIPELINE_1:
                cur.execute("SELECT * FROM prompts ORDER BY id ASC")
                for r in cur.fetchall():
                    prompts.append(Prompt(
                        id=r["id"],
                        name=r["name"],
                        text=r["text"],
                        prompt_type=PromptType.PIPELINE_1,
                        is_default=bool(r.get("is_default")),
                        created_at=r.get("created_at"),
                        updated_at=r.get("updated_at"),
                    ))

            if prompt_type is None or prompt_type == PromptType.PIPELINE_2:
                cur.execute("SELECT * FROM pipeline2_prompts ORDER BY id ASC")
                for r in cur.fetchall():
                    prompts.append(Prompt(
                        id=r["id"],
                        name=r["name"],
                        text=r["text"],
                        prompt_type=PromptType.PIPELINE_2,
                        is_default=bool(r.get("is_default")),
                        created_at=r.get("created_at"),
                        updated_at=r.get("updated_at"),
                    ))
        return prompts

    def save(self, prompt: Prompt) -> Prompt:
        table = "pipeline2_prompts" if prompt.prompt_type == PromptType.PIPELINE_2 else "prompts"
        with self.conn.cursor() as cur:
            if prompt.id is None:
                cur.execute(
                    f"INSERT INTO {table} (name, text, is_default) VALUES (%s, %s, %s)",
                    (prompt.name, prompt.text, int(prompt.is_default)),
                )
                prompt.id = cur.lastrowid
            else:
                cur.execute(
                    f"UPDATE {table} SET name = %s, text = %s, is_default = %s, updated_at = NOW() WHERE id = %s",
                    (prompt.name, prompt.text, int(prompt.is_default), prompt.id),
                )
        return prompt

    def delete(self, prompt_id: int) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM prompts WHERE id = %s", (prompt_id,))
            p1_del = cur.rowcount > 0
            cur.execute("DELETE FROM pipeline2_prompts WHERE id = %s", (prompt_id,))
            p2_del = cur.rowcount > 0
            return p1_del or p2_del

    def set_default(self, prompt_id: int, prompt_type: PromptType) -> None:
        table = "pipeline2_prompts" if prompt_type == PromptType.PIPELINE_2 else "prompts"
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET is_default = 0")
            cur.execute(f"UPDATE {table} SET is_default = 1 WHERE id = %s", (prompt_id,))


class MySQLApiRepository(IApiRepository):
    def __init__(self, conn):
        self.conn = conn

    def get_by_id(self, api_id: int, slot_type: str = "private") -> Optional[ApiSlot]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            if slot_type == "private":
                cur.execute("SELECT * FROM private_apis WHERE id = %s AND is_active = 1", (api_id,))
            else:
                cur.execute("SELECT * FROM public_apis WHERE id = %s AND is_active = 1", (api_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_entity(row, slot_type)

    def list_by_user(self, user_id: int, include_public: bool = False) -> List[ApiSlot]:
        slots: List[ApiSlot] = []
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM private_apis WHERE user_id = %s AND is_active = 1 ORDER BY priority ASC, id ASC",
                (user_id,),
            )
            for r in cur.fetchall():
                slots.append(self._row_to_entity(r, "private"))

            if include_public:
                cur.execute("SELECT * FROM public_apis WHERE is_active = 1 ORDER BY priority ASC, id ASC")
                for r in cur.fetchall():
                    slots.append(self._row_to_entity(r, "public"))
        return slots

    def save_private(
        self,
        user_id: int,
        provider: str,
        api_key: str,
        label: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> ApiSlot:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO private_apis (user_id, provider, api_key, label, selected_model, base_url, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, 1)
                """,
                (user_id, provider, api_key, label, model, base_url),
            )
            new_id = cur.lastrowid
            return ApiSlot(
                id=new_id,
                provider=provider,
                label=label,
                slot_type="private",
                credential_ref=CredentialRef(identifier=str(new_id), provider=provider, slot_type="private"),
                selected_model=model,
                base_url=base_url,
            )

    def delete_private(self, api_id: int, user_id: int) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM private_apis WHERE id = %s AND user_id = %s", (api_id, user_id))
            return cur.rowcount > 0

    def report_pages_used(self, api_id: int, slot_type: str, pages: int = 1) -> None:
        table = "private_apis" if slot_type == "private" else "public_apis"
        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET total_pages_processed = total_pages_processed + %s, updated_at = NOW() WHERE id = %s",
                (pages, api_id),
            )

    def _row_to_entity(self, row: dict, slot_type: str) -> ApiSlot:
        models = json.loads(row["models"]) if row.get("models") else None
        return ApiSlot(
            id=row["id"],
            provider=row["provider"],
            label=row.get("label") or f"{row['provider']}_{row['id']}",
            slot_type=slot_type,
            credential_ref=CredentialRef(identifier=str(row["id"]), provider=row["provider"], slot_type=slot_type),
            selected_model=row.get("selected_model"),
            base_url=row.get("base_url"),
            supported_models=models,
        )


class MySQLDonationRepository(IDonationRepository):
    def __init__(self, conn):
        self.conn = conn

    def save_donation(self, user_id: int, provider: str, api_key: str, label: str, models: List[str]) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO donations (user_id, provider, api_key, label, models)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, provider, api_key, label, json.dumps(models, ensure_ascii=False)),
            )
            return cur.lastrowid

    def list_all(self) -> List[dict]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM donations ORDER BY id DESC")
            return cur.fetchall()
