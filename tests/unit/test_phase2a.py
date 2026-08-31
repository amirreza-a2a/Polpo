# ============================================================
#  tests/unit/test_phase2a.py
# ============================================================

import unittest
import os
import tempfile
import json
import time
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import tests.characterization.conftest_base
import config
from utils.file_manager import (
    get_temp_path, get_job_dir, get_pipeline2_dir,
    get_output_path, get_attachments_dir, get_zip_path,
)
from utils.rate_limiter import wait_if_needed, mark_request_sent, _make_key, _load, _save


class TestPhase2AConfigurationAndPaths(unittest.TestCase):
    """
    Tests for Phase 2A: Configuration, Path independence, and Rate Limiter collision fix.
    """

    def test_project_root_is_absolute_path(self):
        """Verify PROJECT_ROOT is an absolute Path object pointing to project directory."""
        self.assertTrue(config.PROJECT_ROOT.is_absolute())
        self.assertTrue((config.PROJECT_ROOT / "config.py").exists())

    def test_configured_paths_are_project_root_anchored(self):
        """Verify all path constants are absolute paths anchored to PROJECT_ROOT."""
        self.assertTrue(os.path.isabs(config.TEMP_DIR))
        self.assertTrue(os.path.isabs(config.OUTPUT_DIR))
        self.assertTrue(os.path.isabs(config.WORKER_LOCK_FILE))
        self.assertTrue(os.path.isabs(config.RATE_FILE))

        self.assertTrue(config.TEMP_DIR.startswith(str(config.PROJECT_ROOT)))
        self.assertTrue(config.OUTPUT_DIR.startswith(str(config.PROJECT_ROOT)))
        self.assertTrue(config.WORKER_LOCK_FILE.startswith(str(config.PROJECT_ROOT)))
        self.assertTrue(config.RATE_FILE.startswith(str(config.PROJECT_ROOT)))

    def test_file_manager_paths_are_cwd_independent(self):
        """Verify file_manager path helpers produce identical project-anchored paths regardless of CWD."""
        job_id = 42
        p2_job_id = 99
        temp_file = "test.pdf"

        t_path = get_temp_path(temp_file)
        j_dir = get_job_dir(job_id)
        p2_dir = get_pipeline2_dir(p2_job_id)
        out_path = get_output_path(job_id)
        att_dir = get_attachments_dir(job_id)
        zip_path = get_zip_path(job_id)

        self.assertTrue(os.path.isabs(t_path))
        self.assertTrue(os.path.isabs(j_dir))
        self.assertTrue(os.path.isabs(p2_dir))
        self.assertTrue(os.path.isabs(out_path))
        self.assertTrue(os.path.isabs(att_dir))
        self.assertTrue(os.path.isabs(zip_path))

        # Check they all reside inside project root
        root_str = str(config.PROJECT_ROOT)
        self.assertTrue(t_path.startswith(root_str))
        self.assertTrue(j_dir.startswith(root_str))
        self.assertTrue(p2_dir.startswith(root_str))
        self.assertTrue(out_path.startswith(root_str))

        # Clean up test created dirs if any
        if os.path.exists(p2_dir):
            os.rmdir(p2_dir)
        if os.path.exists(att_dir):
            os.rmdir(att_dir)
        if os.path.exists(j_dir):
            os.rmdir(j_dir)

    def test_env_example_contains_only_placeholders(self):
        """Verify .env.example contains placeholder values and no hardcoded secret keys."""
        example_path = config.PROJECT_ROOT / ".env.example"
        self.assertTrue(example_path.exists(), ".env.example must exist in project root")

        with open(example_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Placeholders present
        self.assertIn("BOT_TOKEN=your_telegram_bot_token_here", content)
        self.assertIn("DB_PASS=your_db_password_here", content)
        self.assertIn("DB_USER=polpot_user", content)
        self.assertIn("DB_NAME=polpot_db", content)

        # No real Telegram tokens or live credentials
        self.assertNotIn("8183143378:", content)
        self.assertNotIn("1ZZXxCdJK", content)

    def test_dotenv_in_requirements(self):
        """Verify python-dotenv is explicitly pinned in requirements.txt."""
        req_path = config.PROJECT_ROOT / "requirements.txt"
        self.assertTrue(req_path.exists())
        with open(req_path, "r", encoding="utf-8") as f:
            req_content = f.read()
        self.assertIn("python-dotenv==1.0.1", req_content)

    def test_rate_limiter_composite_key_generation(self):
        """Verify rate limiter generates distinct keys for distinct API types, providers, and IDs."""
        key_pub_1 = _make_key(1, provider="google", api_type="public")
        key_priv_1 = _make_key(1, provider="google", api_type="private")
        key_openai_1 = _make_key(1, provider="openai", api_type="private")

        self.assertEqual(key_pub_1, "google_public_1")
        self.assertEqual(key_priv_1, "google_private_1")
        self.assertEqual(key_openai_1, "openai_private_1")

        # All 3 keys are strictly distinct even though they all have ID = 1
        self.assertNotEqual(key_pub_1, key_priv_1)
        self.assertNotEqual(key_priv_1, key_openai_1)
        self.assertNotEqual(key_pub_1, key_openai_1)

    def test_rate_limiter_prevents_cross_api_collision(self):
        """Verify updating rate timestamp for one API does not alter the rate state of another API with the same ID."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("{}")
            test_rate_file = tf.name

        with patch("utils.rate_limiter.RATE_FILE", test_rate_file):
            api_private_1 = {"id": 1, "provider": "google", "type": "private"}
            api_public_1 = {"id": 1, "provider": "google", "type": "public"}

            # Mark private API 1 as sent
            mark_request_sent(api_private_1)

            # Load state
            with open(test_rate_file, "r") as f:
                data = json.load(f)

            self.assertIn("google_private_1", data)
            self.assertNotIn("google_public_1", data)

            # Public API 1 should have zero elapsed restriction
            key_public = _make_key(1, provider="google", api_type="public")
            self.assertEqual(data.get(key_public, 0), 0)

        if os.path.exists(test_rate_file):
            os.remove(test_rate_file)

    def test_rate_limiter_backward_compatible_signatures(self):
        """Verify wait_if_needed and mark_request_sent accept all legacy and new argument patterns."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("{}")
            test_rate_file = tf.name

        with patch("utils.rate_limiter.RATE_FILE", test_rate_file):
            # 1. Legacy int-only call
            mark_request_sent(10)
            data = _load()
            self.assertIn("10", data)

            # 2. Legacy (id, provider) call
            mark_request_sent(20, "google")
            data = _load()
            self.assertIn("google_20", data)

            # 3. 3-arg (id, provider, type) call
            mark_request_sent(30, "openai", "private")
            data = _load()
            self.assertIn("openai_private_30", data)

            # 4. Dict slot entry call
            mark_request_sent({"id": 40, "provider": "google", "type": "public"})
            data = _load()
            self.assertIn("google_public_40", data)

        if os.path.exists(test_rate_file):
            os.remove(test_rate_file)

    def test_rate_limiter_error_handling_observability(self):
        """Verify _load logs error on corrupted JSON rather than failing silently."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("{corrupted_json_content: true")
            test_rate_file = tf.name

        with patch("utils.rate_limiter.RATE_FILE", test_rate_file):
            with self.assertLogs("rate_limiter", level="ERROR") as cm:
                data = _load()
                self.assertEqual(data, {})
                self.assertTrue(any("Corrupted JSON" in log for log in cm.output))

        if os.path.exists(test_rate_file):
            os.remove(test_rate_file)


if __name__ == "__main__":
    unittest.main()
