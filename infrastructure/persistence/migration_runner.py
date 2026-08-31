# ============================================================
#  infrastructure/persistence/migration_runner.py
# ============================================================

import os
import re
import logging
from pathlib import Path
from typing import List, Set, Dict
from contextlib import contextmanager

from infrastructure.persistence.connection import DatabaseManager, get_db_manager
from config import PROJECT_ROOT

logger = logging.getLogger("database.migrations")

MIGRATIONS_DIR_DEFAULT = PROJECT_ROOT / "database" / "migrations"


class MigrationError(Exception):
    """خطای اجرای مایگریشن دیتابیس."""
    pass


class MigrationRunner:
    """
    مجری مایگریشن‌های نسخه‌بندی‌شده SQL با قابلیت تشخیص ایمن پایگاه‌داده از پیش ساخته‌شده (Baseline Detection)
    بر اساس جداول و ستون‌های دقیق، و محافظت غیرقابل نفوذ در برابر اجرای همزمان با MySQL GET_LOCK.
    """

    SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
    BASELINE_VERSION = "000"

    # تعریف صریح ساختار کامل ۸ جدول اصلی پروداکشن به همراه ستون‌های ضروری
    EXPECTED_BASELINE_SCHEMA: Dict[str, Set[str]] = {
        "users": {
            "id", "telegram_id", "username", "daily_pages_used", "daily_reset_date",
            "use_public_fallback", "auto_retry", "auto_pipeline2", "auto_pipeline2_prompt_id",
            "created_at",
        },
        "private_apis": {
            "id", "user_id", "api_key", "label", "provider", "supported_models",
            "selected_model", "base_url", "chain_priority", "is_active", "created_at",
        },
        "public_apis": {
            "id", "api_key", "label", "provider", "supported_models", "selected_model",
            "base_url", "donated_by", "daily_page_limit", "pages_used_today",
            "daily_reset_date", "priority", "is_active", "created_at",
        },
        "prompts": {
            "id", "title", "description", "prompt_text", "is_active", "is_default",
            "display_order", "created_at",
        },
        "jobs": {
            "id", "user_id", "prompt_id", "file_path", "file_name", "total_pages",
            "processed_pages", "api_chain", "current_api_index", "api_switch_log",
            "model", "status", "output_path", "source_file_id", "source_archive_msg_id",
            "backup_message_id", "backup_zip_msg_id", "retry_count", "error_message",
            "created_at", "finished_at",
        },
        "pipeline2_prompts": {
            "id", "title", "description", "prompt_text", "is_active", "is_default",
            "display_order", "created_at",
        },
        "pipeline2_jobs": {
            "id", "source_job_id", "user_id", "prompt_id", "api_chain",
            "current_api_index", "api_switch_log", "model", "status", "input_path",
            "output_path", "backup_message_id", "retry_count", "error_message",
            "created_at", "finished_at",
        },
        "bot_settings": {
            "setting_key", "setting_value", "updated_at",
        },
    }

    def __init__(self, db: DatabaseManager = None, migrations_dir: Path | str = None):
        self.db = db or get_db_manager()
        self.migrations_dir = Path(migrations_dir or MIGRATIONS_DIR_DEFAULT)

    def ensure_migrations_table(self):
        """ایجاد جدول ردیابی مایگریشن‌ها در صورت عدم وجود."""
        with self.db.cursor() as cur:
            cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.SCHEMA_MIGRATIONS_TABLE} (
                version    VARCHAR(255) PRIMARY KEY,
                name       VARCHAR(255) NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

    def get_applied_migrations(self) -> Set[str]:
        """دریافت لیست نسخه‌های اعمال‌شده مایگریشن."""
        self.ensure_migrations_table()
        with self.db.cursor() as cur:
            cur.execute(f"SELECT version FROM {self.SCHEMA_MIGRATIONS_TABLE}")
            rows = cur.fetchall()
            return {r["version"] for r in rows}

    def get_existing_schema(self) -> Dict[str, Set[str]]:
        """دریافت ساختار فعلی دیتابیس (جداول و ستون‌ها) از information_schema."""
        with self.db.cursor() as cur:
            cur.execute("""
            SELECT LOWER(table_name) AS table_name, LOWER(column_name) AS column_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
            """)
            rows = cur.fetchall()
            schema: Dict[str, Set[str]] = {}
            for r in rows:
                if isinstance(r, dict):
                    t = r.get("table_name")
                    c = r.get("column_name")
                elif isinstance(r, (list, tuple)):
                    t = str(r[0]).lower()
                    c = str(r[1]).lower()
                else:
                    continue

                if t:
                    if t not in schema:
                        schema[t] = set()
                    if c:
                        schema[t].add(c)
            return schema

    def evaluate_baseline_state(self) -> str:
        """
        ارزیابی دقیق وضعیت پایگاه داده با تطبیق جداول و ستون‌های موجود:
        - 'empty': هیچ جدولی در دیتابیس وجود ندارد (نیاز به اجرای 000_baseline_production.sql).
        - 'full_baseline': تمام ۸ جدول اصلی پروداکشن به همراه تمام ستون‌های مورد انتظار وجود دارند.
        - در صورت عدم تطابق جداول یا ستون‌ها: MigrationError صادر می‌شود.
        """
        existing_schema = self.get_existing_schema()
        # حذف جدول ردیابی مایگریشن از ارزیابی ساختار
        existing_schema.pop(self.SCHEMA_MIGRATIONS_TABLE.lower(), None)

        if not existing_schema:
            return "empty"

        existing_tables = set(existing_schema.keys())
        expected_tables = set(self.EXPECTED_BASELINE_SCHEMA.keys())

        missing_tables = expected_tables - existing_tables
        missing_columns: Dict[str, Set[str]] = {}

        for table, expected_cols in self.EXPECTED_BASELINE_SCHEMA.items():
            if table in existing_schema:
                current_cols = existing_schema[table]
                diff = expected_cols - current_cols
                if diff:
                    missing_columns[table] = diff

        # اگر هیچ جدول یا ستونی کم نباشد، دیتابیس معتبر است
        if not missing_tables and not missing_columns:
            return "full_baseline"

        # ساخت پیام خطای تفصیلی در صورت وجود ساختار ناقص
        error_parts = []
        if missing_tables:
            error_parts.append(f"Missing tables: {sorted(missing_tables)}")
        if missing_columns:
            for tbl, cols in missing_columns.items():
                error_parts.append(f"Table '{tbl}' missing columns: {sorted(cols)}")

        raise MigrationError(
            f"Inconsistent/partial database schema detected. "
            f"{'; '.join(error_parts)}. Automatic baselining aborted to protect against partial schema corruption."
        )

    def record_migration(self, version: str, name: str):
        """ثبت نسخه مایگریشن در جدول schema_migrations."""
        with self.db.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.SCHEMA_MIGRATIONS_TABLE} (version, name) VALUES (%s, %s)",
                (version, name),
            )

    def discover_migrations(self) -> List[Path]:
        """یافتن تمام فایل‌های مایگریشن به ترتیب نسخه."""
        if not self.migrations_dir.exists():
            return []

        pattern = re.compile(r"^(\d{3})_(.+)\.sql$")
        migration_files = []

        for p in sorted(self.migrations_dir.glob("*.sql")):
            if pattern.match(p.name):
                migration_files.append(p)

        return migration_files

    def _split_sql_statements(self, sql_content: str) -> List[str]:
        """تجزیه متن SQL به دستورات مجزا بدون کامنت‌ها و خطوط خالی."""
        lines = []
        for line in sql_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") or stripped.startswith("#"):
                continue
            lines.append(line)

        full_clean_sql = "\n".join(lines)
        statements = [stmt.strip() for stmt in full_clean_sql.split(";") if stmt.strip()]
        return statements

    @contextmanager
    def _acquire_migration_lock(self, timeout_seconds: int = 30):
        """
        دریافت قفل سراسری مایگریشن با MySQL GET_LOCK.
        در صورت عدم دریافت قفل به هر دلیلی (Timeout, NULL, Exception)، بلافاصله با خطا متوقف می‌شود (Never Fail Open).
        """
        lock_name = "polpot_migration_lock"
        lock_acquired = False

        with self.db.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT GET_LOCK(%s, %s) AS lock_acquired", (lock_name, timeout_seconds))
                    row = cur.fetchone()
                    acquired_val = None
                    if isinstance(row, dict):
                        acquired_val = row.get("lock_acquired")
                    elif isinstance(row, (list, tuple)):
                        acquired_val = row[0]

                    if acquired_val == 1 or acquired_val == "1":
                        lock_acquired = True
                    elif acquired_val == 0 or acquired_val == "0":
                        raise MigrationError(
                            f"Could not acquire migration lock '{lock_name}' within {timeout_seconds}s timeout (lock held by another process)."
                        )
                    else:
                        # NULL or unexpected return value
                        raise MigrationError(
                            f"Failed to acquire migration lock '{lock_name}': GET_LOCK returned {acquired_val}."
                        )
                except Exception as e:
                    if isinstance(e, MigrationError):
                        raise
                    logger.error("Database error while executing GET_LOCK: %s", e)
                    raise MigrationError(f"Database error while acquiring migration lock '{lock_name}': {e}") from e

                try:
                    yield
                finally:
                    if lock_acquired:
                        try:
                            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                        except Exception as e:
                            logger.warning("Error releasing migration lock '%s': %s", lock_name, e)

    def run_migrations(self) -> List[str]:
        """
        اجرای تمام مایگریشن‌های معلق به صورت ترتیبی با مدیریت ایمن بیس‌لاین و قفل همزمانی.
        """
        with self._acquire_migration_lock():
            self.ensure_migrations_table()
            applied_versions = self.get_applied_migrations()
            migration_files = self.discover_migrations()

            applied_this_run: List[str] = []

            # ارزیابی وضعیت دیتابیس موجود و مدیریت ایمن بیس‌لاین
            state = self.evaluate_baseline_state()

            if state == "full_baseline" and self.BASELINE_VERSION not in applied_versions:
                logger.info(
                    "Existing fully populated database detected (%d tables validated). Recording baseline (%s) without re-executing DDL.",
                    len(self.EXPECTED_BASELINE_SCHEMA), self.BASELINE_VERSION
                )
                self.record_migration(self.BASELINE_VERSION, "baseline_production")
                applied_versions.add(self.BASELINE_VERSION)
                applied_this_run.append(self.BASELINE_VERSION)

            for migration_file in migration_files:
                match = re.match(r"^(\d{3})_(.+)\.sql$", migration_file.name)
                if not match:
                    continue

                version, name = match.group(1), match.group(2)

                if version in applied_versions:
                    continue

                logger.info("Applying migration %s: %s (%s)...", version, name, migration_file.name)

                try:
                    with open(migration_file, "r", encoding="utf-8") as f:
                        content = f.read()

                    statements = self._split_sql_statements(content)

                    with self.db.cursor() as cur:
                        for stmt in statements:
                            cur.execute(stmt)

                    self.record_migration(version, name)
                    applied_versions.add(version)
                    applied_this_run.append(version)
                    logger.info("Migration %s successfully applied.", version)

                except Exception as e:
                    logger.error(
                        "CRITICAL: Migration %s (%s) failed: %s. Halting migration sequence.",
                        version, migration_file.name, e, exc_info=True
                    )
                    raise MigrationError(f"Migration {version} failed: {e}") from e

            return applied_this_run


def run_migrations(db: DatabaseManager = None, migrations_dir: Path | str = None) -> List[str]:
    """تابع کمکی جهت اجرای مایگریشن‌ها."""
    runner = MigrationRunner(db=db, migrations_dir=migrations_dir)
    return runner.run_migrations()
