# ============================================================
#  database/connection.py
# ============================================================

import pymysql
import pymysql.cursors
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS


def get_connection():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASS, database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_db():
    conn = get_connection()
    with conn.cursor() as cur:

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            telegram_id         BIGINT UNIQUE NOT NULL,
            username            VARCHAR(255),
            daily_pages_used    INT DEFAULT 0,
            daily_reset_date    DATE DEFAULT (CURDATE()),
            use_public_fallback TINYINT(1) DEFAULT 1,
            auto_retry          TINYINT(1) DEFAULT 0,
            auto_pipeline2            TINYINT(1) DEFAULT 0
                        COMMENT '1 = بعد از اتمام pipeline1 خودکار pipeline2 اجرا شود',
            auto_pipeline2_prompt_id  INT DEFAULT NULL
                        COMMENT 'پرامپت pipeline2 برای اجرای خودکار',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS private_apis (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            user_id        INT NOT NULL,
            api_key        TEXT NOT NULL,
            label          VARCHAR(255) NOT NULL,
            provider       VARCHAR(50) NOT NULL,
            supported_models JSON,
            selected_model VARCHAR(100) DEFAULT NULL,
            base_url       VARCHAR(500) DEFAULT NULL,
            chain_priority INT DEFAULT 1,
            is_active      TINYINT(1) DEFAULT 1,
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS public_apis (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            api_key          TEXT NOT NULL,
            label            VARCHAR(255) NOT NULL,
            provider         VARCHAR(50) NOT NULL,
            supported_models JSON,
            selected_model   VARCHAR(100) DEFAULT NULL,
            base_url         VARCHAR(500) DEFAULT NULL,
            donated_by       BIGINT DEFAULT NULL,
            daily_page_limit INT DEFAULT 500,
            pages_used_today INT DEFAULT 0,
            daily_reset_date DATE DEFAULT (CURDATE()),
            priority         INT DEFAULT 1,
            is_active        TINYINT(1) DEFAULT 1,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            title         VARCHAR(255) NOT NULL,
            description   TEXT,
            prompt_text   LONGTEXT NOT NULL,
            is_active     TINYINT(1) DEFAULT 1,
            is_default    TINYINT(1) DEFAULT 0,
            display_order INT DEFAULT 1,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            user_id               INT NOT NULL,
            prompt_id             INT NOT NULL,
            file_path             VARCHAR(500),
            file_name             VARCHAR(255),
            total_pages           INT DEFAULT 0,
            processed_pages       INT DEFAULT 0,
            api_chain             JSON,
            current_api_index     INT DEFAULT 0,
            api_switch_log        JSON,
            model                 VARCHAR(100),
            status                ENUM('pending','processing','done','failed','paused') DEFAULT 'pending',
            output_path           VARCHAR(500),
            source_file_id        VARCHAR(500) DEFAULT NULL,
            source_archive_msg_id BIGINT DEFAULT NULL,
            backup_message_id     BIGINT DEFAULT NULL,
            backup_zip_msg_id     BIGINT DEFAULT NULL,
            retry_count           INT DEFAULT 0,
            error_message         TEXT,
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at           DATETIME DEFAULT NULL,
            FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
            FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── pipeline2_prompts ─────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline2_prompts (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            title         VARCHAR(255) NOT NULL,
            description   TEXT,
            prompt_text   LONGTEXT NOT NULL,
            is_active     TINYINT(1) DEFAULT 1,
            is_default    TINYINT(1) DEFAULT 0,
            display_order INT DEFAULT 1,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── pipeline2_jobs ─────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pipeline2_jobs (
            id                 INT AUTO_INCREMENT PRIMARY KEY,
            source_job_id      INT NOT NULL
                        COMMENT 'FK به jobs - کدام Markdown پردازش شود',
            user_id            INT NOT NULL,
            prompt_id          INT NOT NULL,
            api_chain          JSON,
            current_api_index  INT DEFAULT 0,
            api_switch_log     JSON,
            model              VARCHAR(100),
            status             ENUM('pending','processing','done','failed','paused') DEFAULT 'pending',
            input_path         VARCHAR(500)
                        COMMENT 'مسیر Markdown یکپارچه‌شده (پس از حذف ## صفحه X)',
            output_path        VARCHAR(500)
                        COMMENT 'مسیر Markdown نهایی پس از پردازش AI',
            backup_message_id  BIGINT DEFAULT NULL,
            retry_count        INT DEFAULT 0,
            error_message      TEXT,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at        DATETIME DEFAULT NULL,
            FOREIGN KEY (source_job_id) REFERENCES jobs(id)            ON DELETE CASCADE,
            FOREIGN KEY (user_id)       REFERENCES users(id)           ON DELETE CASCADE,
            FOREIGN KEY (prompt_id)     REFERENCES pipeline2_prompts(id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── migration ────────────────────────────────────
        migrations = [
            "ALTER TABLE private_apis ADD COLUMN selected_model VARCHAR(100) DEFAULT NULL",
            "ALTER TABLE private_apis ADD COLUMN base_url VARCHAR(500) DEFAULT NULL",
            "ALTER TABLE public_apis  ADD COLUMN selected_model VARCHAR(100) DEFAULT NULL",
            "ALTER TABLE public_apis  ADD COLUMN base_url VARCHAR(500) DEFAULT NULL",
            "ALTER TABLE jobs ADD COLUMN source_file_id VARCHAR(500) DEFAULT NULL",
            "ALTER TABLE jobs ADD COLUMN source_archive_msg_id BIGINT DEFAULT NULL",
            "ALTER TABLE jobs ADD COLUMN backup_zip_msg_id VARCHAR(500) DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN auto_retry TINYINT(1) DEFAULT 0",
            "ALTER TABLE jobs  ADD COLUMN retry_count INT DEFAULT 0",
            "ALTER TABLE users ADD COLUMN auto_pipeline2 TINYINT(1) DEFAULT 0",
            "ALTER TABLE users ADD COLUMN auto_pipeline2_prompt_id INT DEFAULT NULL",
        ]
        for sql in migrations:
            try:
                cur.execute(sql)
            except Exception:
                pass

    conn.close()
    print("✅ دیتابیس با موفقیت راه‌اندازی شد.")


if __name__ == "__main__":
    init_db()