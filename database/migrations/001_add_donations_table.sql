-- ============================================================
--  001_add_donations_table.sql
--  Create table to store public API donations (replaces in-memory bot_data)
-- ============================================================

CREATE TABLE IF NOT EXISTS `donations` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `user_id` INT NOT NULL,
    `provider` VARCHAR(64) NOT NULL,
    `api_key` TEXT NOT NULL,
    `label` VARCHAR(128) NOT NULL,
    `models` TEXT NULL,
    `status` VARCHAR(32) NOT NULL DEFAULT 'pending',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY `idx_user_id` (`user_id`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
