# ============================================================
#  database/connection.py  –  اتصال به MySQL
# ============================================================

import pymysql
import pymysql.cursors
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS


def get_connection():
    """یک اتصال جدید به دیتابیس برمی‌گرداند."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def init_db():
    """تمام جداول مورد نیاز را می‌سازد (اگر وجود نداشته باشند)."""
    conn = get_connection()
    with conn.cursor() as cur:

        # ─── users ────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            telegram_id           BIGINT UNIQUE NOT NULL,
            username              VARCHAR(255),
            daily_pages_used      INT DEFAULT 0,
            daily_reset_date      DATE DEFAULT (CURDATE()),
            use_public_fallback   TINYINT(1) DEFAULT 1
                        COMMENT '1 = اگر API خصوصی تمام شد از عمومی استفاده کن',
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── private_apis ─────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS private_apis (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            user_id           INT NOT NULL,
            api_key           TEXT NOT NULL,
            label             VARCHAR(255) NOT NULL,
            provider          VARCHAR(50) NOT NULL
                              COMMENT 'google | openai | openrouter',
            supported_models  JSON,
            chain_priority    INT DEFAULT 1
                              COMMENT 'ترتیب استفاده در زنجیره (کمتر = اول)',
            is_active         TINYINT(1) DEFAULT 1,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── public_apis ──────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public_apis (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            api_key           TEXT NOT NULL,
            label             VARCHAR(255) NOT NULL,
            provider          VARCHAR(50) NOT NULL,
            supported_models  JSON,
            donated_by        BIGINT DEFAULT NULL
                              COMMENT 'NULL = ادمین، عدد = telegram_id اهداکننده',
            daily_page_limit  INT DEFAULT 500,
            pages_used_today  INT DEFAULT 0,
            daily_reset_date  DATE DEFAULT (CURDATE()),
            priority          INT DEFAULT 1
                              COMMENT 'ترتیب چرخش (کمتر = اول)',
            is_active         TINYINT(1) DEFAULT 1,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── prompts ──────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS prompts (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            title          VARCHAR(255) NOT NULL,
            description    TEXT,
            prompt_text    LONGTEXT NOT NULL,
            is_active      TINYINT(1) DEFAULT 1,
            is_default     TINYINT(1) DEFAULT 0,
            display_order  INT DEFAULT 1,
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ─── jobs ─────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            user_id             INT NOT NULL,
            prompt_id           INT NOT NULL,
            file_path           VARCHAR(500),
            file_name           VARCHAR(255),
            total_pages         INT DEFAULT 0,
            processed_pages     INT DEFAULT 0,
            api_chain           JSON
                                COMMENT 'آرایه مرتب‌شده API ها با نوع و id',
            current_api_index   INT DEFAULT 0
                                COMMENT 'ایندکس فعلی در api_chain',
            api_switch_log      JSON
                                COMMENT 'لاگ سوئیچ‌های انجام‌شده',
            model               VARCHAR(100),
            status              ENUM(
                                    'pending',
                                    'processing',
                                    'done',
                                    'failed',
                                    'paused'
                                ) DEFAULT 'pending',
            output_path         VARCHAR(500),
            backup_message_id   BIGINT DEFAULT NULL,
            error_message       TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at         DATETIME DEFAULT NULL,
            FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
            FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

    conn.close()
    print("✅ دیتابیس با موفقیت راه‌اندازی شد.")


if __name__ == "__main__":
    init_db()
