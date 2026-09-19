# ============================================================
#  infrastructure/logging/setup.py
# ============================================================

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import re
from pathlib import Path


class SecretRedactingFilter(logging.Filter):
    """
    Security filter for log records to automatically redact tokens, passwords, and API keys.
    """
    def __init__(self, secrets_to_mask: list[str] = None):
        super().__init__()
        self.secrets = set()
        if secrets_to_mask:
            for s in secrets_to_mask:
                if s and isinstance(s, str) and len(s) > 3:
                    self.secrets.add(s)

        # Common API key and token regex patterns
        self.patterns = [
            re.compile(r'(\d{8,12}:[A-Za-z0-9_-]{35})'),       # Telegram Bot Token
            re.compile(r'(AIza[A-Za-z0-9_-]{30,})'),           # Google API Key
            re.compile(r'(sk-[A-Za-z0-9_-]{20,})'),            # OpenAI / OpenRouter Key
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize(v) if isinstance(v, str) else v for v in record.args)
        return True

    def _sanitize(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        result = text
        for secret in self.secrets:
            if secret in result:
                result = result.replace(secret, "[REDACTED]")
        for pattern in self.patterns:
            result = pattern.sub("[REDACTED_API_KEY]", result)
        return result


def setup_logging(log_level: int = logging.INFO, log_dir: str = None) -> logging.Logger:
    """
    Initializes structured logging with rotating file and console handlers.
    """
    if log_dir is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        log_dir = project_root / "logs"
    else:
        log_dir = Path(log_dir)

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "polpot.log"

    # Collect sensitive credentials for the redaction filter
    known_secrets = []
    try:
        import config
        for attr in ["BOT_TOKEN", "DB_PASS", "DB_USER"]:
            val = getattr(config, attr, None)
            if val and isinstance(val, str):
                known_secrets.append(val)
    except Exception:
        pass

    redacting_filter = SecretRedactingFilter(known_secrets)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Prevent attaching duplicate handlers on repeated setup invocations
    if not root_logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. Rotating file handler (5 MB, up to 5 backups)
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting_filter)
        root_logger.addHandler(file_handler)

        # 2. Console handler (stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redacting_filter)
        root_logger.addHandler(console_handler)

    logger = logging.getLogger("polpot")
    logger.info("Logging infrastructure initialized. Log file: %s", log_file)
    return logger


def get_logger(name: str = "polpot") -> logging.Logger:
    """Retrieves a named logger instance."""
    return logging.getLogger(name)
