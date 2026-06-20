# ============================================================
#  database/models.py  –  توابع CRUD برای همه جداول
# ============================================================

import json
from datetime import date
from database.connection import get_connection


# ════════════════════════════════════════════════════════════
#  USERS
# ════════════════════════════════════════════════════════════

def get_or_create_user(telegram_id: int, username: str = None) -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cur.fetchone()
        if not user:
            cur.execute(
                "INSERT INTO users (telegram_id, username) VALUES (%s, %s)",
                (telegram_id, username),
            )
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            user = cur.fetchone()
    conn.close()
    return user


def get_user(telegram_id: int) -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cur.fetchone()
    conn.close()
    return user


def reset_daily_pages_if_needed(user_id: int):
    """اگر روز عوض شده باشد، صفحات مصرفی را ریست می‌کند."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET daily_pages_used = 0, daily_reset_date = CURDATE() "
            "WHERE id = %s AND daily_reset_date < CURDATE()",
            (user_id,),
        )
    conn.close()


def increment_user_pages(user_id: int, count: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET daily_pages_used = daily_pages_used + %s WHERE id = %s",
            (count, user_id),
        )
    conn.close()


def set_user_fallback(telegram_id: int, use_fallback: bool):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET use_public_fallback = %s WHERE telegram_id = %s",
            (1 if use_fallback else 0, telegram_id),
        )
    conn.close()


# ════════════════════════════════════════════════════════════
#  PRIVATE APIS
# ════════════════════════════════════════════════════════════

def add_private_api(user_id: int, api_key: str, label: str,
                    provider: str, models: list, priority: int = 1,
                    selected_model: str = None,
                    base_url: str = None) -> int:
    import json
    from database.connection import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO private_apis
               (user_id, api_key, label, provider, supported_models,
                chain_priority, selected_model, base_url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, api_key, label, provider, json.dumps(models),
             priority, selected_model, base_url),
        )
        new_id = cur.lastrowid
    conn.close()
    return new_id

def get_user_private_apis(user_id: int) -> list:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM private_apis WHERE user_id = %s AND is_active = 1 "
            "ORDER BY chain_priority ASC",
            (user_id,),
        )
        rows = cur.fetchall()
    conn.close()
    for r in rows:
        if isinstance(r["supported_models"], str):
            r["supported_models"] = json.loads(r["supported_models"])
    return rows


def delete_private_api(api_id: int, user_id: int) -> bool:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM private_apis WHERE id = %s AND user_id = %s",
            (api_id, user_id),
        )
        deleted = cur.rowcount > 0
    conn.close()
    return deleted


def update_private_api_priority(api_id: int, user_id: int, priority: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE private_apis SET chain_priority = %s WHERE id = %s AND user_id = %s",
            (priority, api_id, user_id),
        )
    conn.close()


# ════════════════════════════════════════════════════════════
#  PUBLIC APIS
# ════════════════════════════════════════════════════════════

def add_public_api(api_key: str, label: str, provider: str,
                   models: list, daily_limit: int, priority: int,
                   donated_by: int = None) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO public_apis
               (api_key, label, provider, supported_models,
                daily_page_limit, priority, donated_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (api_key, label, provider, json.dumps(models),
             daily_limit, priority, donated_by),
        )
        new_id = cur.lastrowid
    conn.close()
    return new_id


def get_next_available_public_api() -> dict | None:
    """اولین API عمومی که ظرفیت دارد را برمی‌گرداند."""
    conn = get_connection()
    with conn.cursor() as cur:
        # ابتدا ریست روزانه API هایی که نیاز دارند
        cur.execute(
            "UPDATE public_apis SET pages_used_today = 0, daily_reset_date = CURDATE() "
            "WHERE daily_reset_date < CURDATE()"
        )
        cur.execute(
            """SELECT * FROM public_apis
               WHERE is_active = 1
                 AND pages_used_today < daily_page_limit
               ORDER BY priority ASC
               LIMIT 1"""
        )
        api = cur.fetchone()
    conn.close()
    if api and isinstance(api.get("supported_models"), str):
        api["supported_models"] = json.loads(api["supported_models"])
    return api


def get_all_public_apis() -> list:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM public_apis ORDER BY priority ASC")
        rows = cur.fetchall()
    conn.close()
    for r in rows:
        if isinstance(r.get("supported_models"), str):
            r["supported_models"] = json.loads(r["supported_models"])
    return rows


def increment_public_api_pages(api_id: int, count: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public_apis SET pages_used_today = pages_used_today + %s WHERE id = %s",
            (count, api_id),
        )
    conn.close()


def toggle_public_api(api_id: int, is_active: bool):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public_apis SET is_active = %s WHERE id = %s",
            (1 if is_active else 0, api_id),
        )
    conn.close()


def update_public_api_priority(api_id: int, priority: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public_apis SET priority = %s WHERE id = %s",
            (priority, api_id),
        )
    conn.close()


def delete_public_api(api_id: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public_apis WHERE id = %s", (api_id,))
    conn.close()


# ════════════════════════════════════════════════════════════
#  PROMPTS
# ════════════════════════════════════════════════════════════

def get_active_prompts() -> list:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM prompts WHERE is_active = 1 ORDER BY display_order ASC"
        )
        rows = cur.fetchall()
    conn.close()
    return rows


def get_default_prompt() -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM prompts WHERE is_default = 1 AND is_active = 1 LIMIT 1")
        p = cur.fetchone()
    conn.close()
    return p


def get_prompt_by_id(prompt_id: int) -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM prompts WHERE id = %s", (prompt_id,))
        p = cur.fetchone()
    conn.close()
    return p


def get_all_prompts() -> list:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM prompts ORDER BY display_order ASC")
        rows = cur.fetchall()
    conn.close()
    return rows


def add_prompt(title: str, description: str, prompt_text: str,
               is_default: bool = False, order: int = 1) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        if is_default:
            cur.execute("UPDATE prompts SET is_default = 0")
        cur.execute(
            """INSERT INTO prompts (title, description, prompt_text, is_default, display_order)
               VALUES (%s, %s, %s, %s, %s)""",
            (title, description, prompt_text, 1 if is_default else 0, order),
        )
        new_id = cur.lastrowid
    conn.close()
    return new_id


def update_prompt(prompt_id: int, title: str = None, description: str = None,
                  prompt_text: str = None, is_active: bool = None,
                  is_default: bool = None, order: int = None):
    conn = get_connection()
    with conn.cursor() as cur:
        if is_default:
            cur.execute("UPDATE prompts SET is_default = 0")
        fields, vals = [], []
        if title       is not None: fields.append("title = %s");          vals.append(title)
        if description is not None: fields.append("description = %s");    vals.append(description)
        if prompt_text is not None: fields.append("prompt_text = %s");    vals.append(prompt_text)
        if is_active   is not None: fields.append("is_active = %s");      vals.append(1 if is_active else 0)
        if is_default  is not None: fields.append("is_default = %s");     vals.append(1 if is_default else 0)
        if order       is not None: fields.append("display_order = %s");  vals.append(order)
        if fields:
            vals.append(prompt_id)
            cur.execute(f"UPDATE prompts SET {', '.join(fields)} WHERE id = %s", vals)
    conn.close()


def delete_prompt(prompt_id: int):
    """حذف پرامپت و جایگزین کردن جاب‌های pending با پرامپت پیش‌فرض."""
    default = get_default_prompt()
    conn = get_connection()
    with conn.cursor() as cur:
        if default and default["id"] != prompt_id:
            cur.execute(
                "UPDATE jobs SET prompt_id = %s WHERE prompt_id = %s AND status = 'pending'",
                (default["id"], prompt_id),
            )
        cur.execute("DELETE FROM prompts WHERE id = %s", (prompt_id,))
    conn.close()


# ════════════════════════════════════════════════════════════
#  JOBS
# ════════════════════════════════════════════════════════════

def create_job(user_id: int, prompt_id: int, file_path: str,
               file_name: str, total_pages: int,
               api_chain: list, model: str) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO jobs
               (user_id, prompt_id, file_path, file_name, total_pages,
                api_chain, current_api_index, api_switch_log, model)
               VALUES (%s, %s, %s, %s, %s, %s, 0, '[]', %s)""",
            (user_id, prompt_id, file_path, file_name, total_pages,
             json.dumps(api_chain), model),
        )
        new_id = cur.lastrowid
    conn.close()
    return new_id


def get_next_pending_job() -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        )
        job = cur.fetchone()
    conn.close()
    if job:
        for f in ("api_chain", "api_switch_log"):
            if isinstance(job.get(f), str):
                job[f] = json.loads(job[f])
    return job


def get_job(job_id: int) -> dict | None:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
    conn.close()
    if job:
        for f in ("api_chain", "api_switch_log"):
            if isinstance(job.get(f), str):
                job[f] = json.loads(job[f])
    return job


def update_job_status(job_id: int, status: str, error_message: str = None):
    conn = get_connection()
    with conn.cursor() as cur:
        if status in ("done", "failed"):
            cur.execute(
                "UPDATE jobs SET status = %s, error_message = %s, finished_at = NOW() WHERE id = %s",
                (status, error_message, job_id),
            )
        else:
            cur.execute(
                "UPDATE jobs SET status = %s, error_message = %s WHERE id = %s",
                (status, error_message, job_id),
            )
    conn.close()


def update_job_progress(job_id: int, processed_pages: int,
                        current_api_index: int, switch_log: list,
                        output_path: str = None):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
               SET processed_pages = %s,
                   current_api_index = %s,
                   api_switch_log = %s,
                   output_path = COALESCE(%s, output_path)
               WHERE id = %s""",
            (processed_pages, current_api_index,
             json.dumps(switch_log), output_path, job_id),
        )
    conn.close()



def update_job_backup(job_id: int,
                      backup_message_id: int,
                      backup_zip_msg_id: int = None) -> None:
    """شناسه پیام‌های بکاپ MD و ZIP را ذخیره می‌کند."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
               SET backup_message_id = %s, backup_zip_msg_id = %s
               WHERE id = %s""",
            (backup_message_id, backup_zip_msg_id, job_id),
        )
    conn.close()


def get_user_jobs(user_id: int, limit: int = 10) -> list:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM jobs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
        rows = cur.fetchall()
    conn.close()
    return rows


def count_user_pending_jobs(user_id: int) -> int:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE user_id = %s AND status IN ('pending','processing')",
            (user_id,),
        )
        result = cur.fetchone()
    conn.close()
    return result["cnt"] if result else 0


def get_queue_position(job_id: int) -> int:
    """موقعیت یک جاب در صف را برمی‌گرداند."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = 'pending' AND id <= %s",
            (job_id,),
        )
        result = cur.fetchone()
    conn.close()
    return result["cnt"] if result else 1


def get_today_stats() -> dict:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total_jobs, SUM(processed_pages) AS total_pages "
            "FROM jobs WHERE DATE(created_at) = CURDATE()"
        )
        stats = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) AS in_queue FROM jobs WHERE status IN ('pending','processing')"
        )
        queue = cur.fetchone()
    conn.close()
    return {
        "total_jobs":   stats["total_jobs"]  or 0,
        "total_pages":  stats["total_pages"] or 0,
        "in_queue":     queue["in_queue"]    or 0,
    }



def add_public_api(api_key: str, label: str, provider: str,
                   models: list, daily_limit: int = 500,
                   priority: int = 1, donated_by: int = None,
                   selected_model: str = None,
                   base_url: str = None) -> int:
    import json
    from database.connection import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO public_apis
               (api_key, label, provider, supported_models, daily_page_limit,
                priority, donated_by, selected_model, base_url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (api_key, label, provider, json.dumps(models),
             daily_limit, priority, donated_by, selected_model, base_url),
        )
        new_id = cur.lastrowid
    conn.close()
    return new_id



# ─── آپدیت اطلاعات سورس جاب ─────────────────────────────
 
def update_job_source(job_id: int,
                      source_file_id: str,
                      source_archive_msg_id: int) -> None:
    """file_id و message_id سورس PDF را در آرشیو ذخیره می‌کند."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
               SET source_file_id = %s, source_archive_msg_id = %s
               WHERE id = %s""",
            (source_file_id, source_archive_msg_id, job_id),
        )
    conn.close()
 



def update_public_api_model_url(api_id: int,
                                 selected_model: str,
                                 base_url: str | None) -> None:
    """مدل و base_url یک API عمومی را آپدیت می‌کند."""
    from database.connection import get_connection
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE public_apis
               SET selected_model = %s, base_url = %s
               WHERE id = %s""",
            (selected_model, base_url, api_id),
        )
    conn.close()
    
    
    
    
    
    

# ─── تاریخچه صفحه‌بندی‌شده ──────────────────────────────
 
def get_user_jobs_paginated(user_id: int,
                             page: int = 1,
                             per_page: int = 5) -> tuple[list, int]:
    """
    جاب‌های کاربر را صفحه‌بندی‌شده برمی‌گرداند.
    خروجی: (لیست جاب‌ها، تعداد کل)
    """
    offset = (page - 1) * per_page
    conn   = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total FROM jobs WHERE user_id = %s",
            (user_id,),
        )
        total = cur.fetchone()["total"]
 
        cur.execute(
            """SELECT id, file_name, total_pages, processed_pages,
                      status, created_at, backup_message_id,
                      backup_zip_msg_id, source_file_id, error_message,
                      output_path
               FROM jobs
               WHERE user_id = %s
               ORDER BY created_at DESC
               LIMIT %s OFFSET %s""",
            (user_id, per_page, offset),
        )
        jobs = cur.fetchall()
    conn.close()
    return jobs, total
 
 
# ─── دریافت یک جاب خاص برای کاربر (بررسی امنیتی) ───────
 
def get_job_for_user(job_id: int, user_id: int) -> dict | None:
    """جاب را فقط اگر متعلق به این کاربر باشد برمی‌گرداند."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        job = cur.fetchone()
    conn.close()
    return job
 
 
# ─── آپدیت api_chain جاب ────────────────────────────────
 
def update_job_api_chain(job_id: int, api_chain: list) -> None:
    """زنجیره API جاب را آپدیت می‌کند (برای resume با API جدید)."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs
               SET api_chain = %s, current_api_index = 0
               WHERE id = %s""",
            (json.dumps(api_chain), job_id),
        )
    conn.close()
 
 
# ─── requeue کردن جاب ───────────────────────────────────
 
def requeue_job(job_id: int, file_path: str = None) -> None:
    """
    جاب paused/failed را به صف برمی‌گرداند.
    processed_pages حفظ می‌شود تا از همانجا ادامه دهد.
    """
    conn = get_connection()
    with conn.cursor() as cur:
        if file_path:
            cur.execute(
                """UPDATE jobs
                   SET status = 'pending', error_message = NULL,
                       file_path = %s
                   WHERE id = %s""",
                (file_path, job_id),
            )
        else:
            cur.execute(
                """UPDATE jobs
                   SET status = 'pending', error_message = NULL
                   WHERE id = %s""",
                (job_id,),
            )
    conn.close()
 