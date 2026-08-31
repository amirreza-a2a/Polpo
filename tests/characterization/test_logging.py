# ============================================================
#  tests/characterization/test_logging.py
# ============================================================

import unittest
import tempfile
import logging
import os
import shutil
from infrastructure.logging.setup import setup_logging, get_logger, SecretRedactingFilter


class TestLoggingInfrastructure(unittest.TestCase):
    """
    Tests structured rotating logging setup and secret redaction filter.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_secret_redacting_filter(self):
        """
        Verifies that SecretRedactingFilter masks known tokens, passwords, and API key patterns.
        """
        secret_password = "super_secret_db_password_123"
        telegram_token = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
        google_api_key = "AIzaSyD-1234567890abcdefghijklmnopqrst"

        filter_inst = SecretRedactingFilter(secrets_to_mask=[secret_password])

        # Test message with DB password
        record1 = logging.LogRecord("test", logging.INFO, "path", 1, f"Connecting with {secret_password}", (), None)
        filter_inst.filter(record1)
        self.assertNotIn(secret_password, record1.msg)
        self.assertIn("[REDACTED]", record1.msg)

        # Test message with Telegram bot token pattern
        record2 = logging.LogRecord("test", logging.INFO, "path", 1, f"Bot started with token {telegram_token}", (), None)
        filter_inst.filter(record2)
        self.assertNotIn(telegram_token, record2.msg)
        self.assertIn("[REDACTED_API_KEY]", record2.msg)

        # Test message with Google API key pattern
        record3 = logging.LogRecord("test", logging.INFO, "path", 1, f"Google key is {google_api_key}", (), None)
        filter_inst.filter(record3)
        self.assertNotIn(google_api_key, record3.msg)
        self.assertIn("[REDACTED_API_KEY]", record3.msg)

    def test_setup_logging_creates_file_and_logs(self):
        """
        Verifies setup_logging initializes log directory and writes rotating file.
        """
        # Clear existing handlers for clean test
        logging.getLogger().handlers = []

        logger = setup_logging(log_level=logging.DEBUG, log_dir=self.test_dir)
        log_file = os.path.join(self.test_dir, "polpot.log")

        self.assertTrue(os.path.exists(log_file))

        logger.info("Test log message for verification")

        # Flush handlers
        for h in logging.getLogger().handlers:
            h.flush()

        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Test log message for verification", content)
        self.assertIn("[INFO]", content)


if __name__ == "__main__":
    unittest.main()
